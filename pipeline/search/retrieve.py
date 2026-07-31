"""Hybrid retrieval over the LanceDB ``units`` and ``frames`` tables.

Enabled channels independently retrieve visual, semantic-text, and native
full-text candidates.  Their ranked lists are combined with weighted
reciprocal-rank fusion (RRF), so incomparable cosine distances and BM25
scores are never added together.  A zero channel weight is a true ablation:
that channel performs no retrieval (and lexical-only search loads no model).
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Iterable
from itertools import combinations
from typing import Any

import lancedb
import numpy as np
from lancedb.expr import col, lit
from lancedb.query import BooleanQuery, FullTextOperator, MatchQuery, Occur
from PIL import Image

from pipeline.config import Config
from pipeline.index.writer import table_names
from pipeline.ingest.embed import embed_spatial_images, embed_text


_CANDIDATE_LIMIT = 100
_FRAME_CANDIDATE_LIMIT = _CANDIDATE_LIMIT * 3
_REFERENCE_FRAME_CANDIDATE_LIMIT = 96
_REFERENCE_RAW_FRAME_CANDIDATE_LIMIT = _REFERENCE_FRAME_CANDIDATE_LIMIT * 3
_REFERENCE_SPATIAL_GRID_SIZE = 6
_REFERENCE_GLOBAL_WEIGHT = 0.65
_REFERENCE_SPATIAL_WEIGHT = 1.0 - _REFERENCE_GLOBAL_WEIGHT
_REFERENCE_RESULT_TEMPORAL_GAP_SECONDS = 90.0
_LEXICAL_CANDIDATE_LIMIT = _CANDIDATE_LIMIT * 3
_MAX_LEXICAL_QUERY_TERMS = 12
SEARCH_RESULT_LIMIT = 12
_RESULT_LIMIT = SEARCH_RESULT_LIMIT
_RRF_K = 60
# Treat time as supporting evidence for a duplicate, never as a duplicate by
# itself: .90 within 30 seconds, or .92 at any distance/film.  Measured on a
# four-film library: same-subject repeats (one character's close-ups across a
# scene) cluster at .92-.94 cosine minutes apart, while deliberate visual
# callbacks sit at ~.91 and must survive.  The per-film result cap comes from
# config (retrieval.diversity.max_per_film); scene IDs are not stored yet, so
# max_per_scene remains unenforced.
_TEMPORAL_WINDOW_SECONDS = 30.0
_TEMPORAL_VISUAL_DUP_COSINE = 0.90
_VISUAL_DUP_COSINE = 0.92

_TOKEN_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
_LEXICAL_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "but",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "this",
        "to",
        "up",
        "was",
        "were",
        "with",
        "without",
    }
)
# Corpus-wide scan columns: text only.  Hauling img_vec for every row costs
# ~1.5s per query at a few thousand shots; vectors are fetched afterwards for
# just the ranked candidates (see _attach_image_vectors).
_LEXICAL_COLUMNS = [
    "unit_id",
    "film_id",
    "shot_id",
    "t_start",
    "t_end",
    "caption",
    "searchable_text",
    "dialogue",
    "keyframe_paths",
]
# Scoped candidate fetches (frame hits, reference search, ranked lexical rows)
# are bounded to ~100 rows, so carrying the vector for the final diversity
# pass is cheap there.
_CANDIDATE_COLUMNS = [*_LEXICAL_COLUMNS, "img_vec"]
_FTS_COLUMNS = [*_LEXICAL_COLUMNS, "_score"]

# Each category has deliberately narrower document patterns than query
# patterns.  For example, spoken dialogue containing "give me credit" should
# not be mistaken for a credit roll.
_JUNK_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "credits": tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            # Captions write compounds both ways: "end credits" and
            # "end-credit cards/frame", so separators allow a hyphen and the
            # trailing noun allows a plural.
            r"\bcredits?[-\s]+(?:rolls?|crawls?|sequences?|screens?|cards?"
            r"|text|frames?)\b",
            r"\bcredits?[-\s]+(?:scroll|scrolls|scrolling|list|names)\b",
            r"\b(?:opening|closing|end|final|rolling|production|cast|crew)"
            r"[-\s]+credits?\b",
            r"\b(?:film|movie|music|legal)[-\s]+credits?\b",
            r"\b(?:directed|written|produced)\s+by\b",
        )
    ),
    "logos": tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\b(?:studio|production(?:\s+company)?|distributor|company|brand)"
            r"\s+(?:logo|ident)\b",
            r"\blogo\s+(?:appears|animation|screen|card|sequence)\b",
        )
    ),
    "title_cards": tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\btitle[- ]cards?\b",
            r"\bintertitles?\b",
            r"\b(?:opening|main|film|movie)\s+titles?\b",
        )
    ),
    "static": tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"^\s*(?:full[- ]frame\s+)?(?:analog\s+)?"
            r"(?:television|tv|video)\s+static\b",
            r"^\s*static\s+(?:frame|image|graphic)\b",
            r"\b(?:still|freeze(?:-?frame)?)\s+(?:frame|image|graphic)\b",
            r"\bfrozen\s+frame\b",
        )
    ),
    "blank": tuple(
        re.compile(pattern, re.IGNORECASE)
        for pattern in (
            r"\b(?:blank|black|white|solid[- ]colou?r)"
            r"\s+(?:screen|frame|image)\b",
            r"\b(?:fade|fades|faded|cut|cuts)\s+to\s+black\b",
            r"\bnearly\s+black\s+(?:screen|frame|image)\b",
            r"\b(?:almost|nearly)\s+(?:entirely\s+)?black\b",
        )
    ),
}

_JUNK_QUERY_PATTERNS: dict[str, re.Pattern[str]] = {
    "credits": re.compile(
        r"\b(?:credits|credit roll|opening credit|closing credit|end credit"
        r"|cast list|crew list)\b",
        re.IGNORECASE,
    ),
    "logos": re.compile(r"\b(?:logo|logos|studio ident|idents?)\b", re.IGNORECASE),
    "title_cards": re.compile(
        r"\b(?:title card|title cards|intertitle|intertitles|title sequence)\b",
        re.IGNORECASE,
    ),
    "static": re.compile(
        r"\b(?:static frame|still frame|freeze frame|static image|still image)\b",
        re.IGNORECASE,
    ),
    "blank": re.compile(
        r"\b(?:blank|black|white)\s+(?:screen|frame|image)\b"
        r"|\bfade\s+to\s+black\b",
        re.IGNORECASE,
    ),
}
_JUNK_NEGATED_QUERY_PATTERNS: dict[str, re.Pattern[str]] = {
    "credits": re.compile(
        r"\b(?:no|without|exclude|excluding)\s+"
        r"(?:(?:opening|closing|end)\s+)?credits?\b",
        re.IGNORECASE,
    ),
    "logos": re.compile(
        r"\b(?:no|without|exclude|excluding)\s+(?:logos?|studio idents?)\b",
        re.IGNORECASE,
    ),
    "title_cards": re.compile(
        r"\b(?:no|without|exclude|excluding)\s+"
        r"(?:title cards?|intertitles?|title sequences?)\b",
        re.IGNORECASE,
    ),
    "static": re.compile(
        r"\b(?:no|without|exclude|excluding)\s+"
        r"(?:static|still|freeze)[- ]?(?:frames?|images?)\b",
        re.IGNORECASE,
    ),
    "blank": re.compile(
        r"\b(?:no|without|exclude|excluding)\s+"
        r"(?:blank|black|white)\s+(?:screens?|frames?|images?)\b",
        re.IGNORECASE,
    ),
}


def _row_id(row: dict[str, Any]) -> str:
    """Return the row's stable identifier, falling back to ``shot_id``."""
    return str(row.get("unit_id") or row.get("shot_id") or "")


def _tokens(value: Any) -> list[str]:
    """Return stable case-folded word tokens for a possibly-null value."""
    return [match.group(0).casefold() for match in _TOKEN_RE.finditer(str(value or ""))]


def _query_tokens(value: Any) -> list[str]:
    """Return meaningful lexical query terms without incidental glue words."""
    return [token for token in _tokens(value) if token not in _LEXICAL_STOP_WORDS]


def _unique_query_terms(value: Any) -> list[str]:
    """Return a bounded, order-preserving lexical query vocabulary.

    Dense retrieval still sees the complete prompt. The cap bounds native
    pairwise compound evidence so pasted prose cannot turn one FTS request
    into quadratic unbounded work.
    """
    return list(dict.fromkeys(_query_tokens(value)))[:_MAX_LEXICAL_QUERY_TERMS]


def _row_text(row: dict[str, Any]) -> str:
    """Combine text fields for content classification."""
    return " ".join(
        str(row.get(field) or "")
        for field in ("caption", "searchable_text", "dialogue")
    )


def _lexical_text(row: dict[str, Any]) -> str:
    """Return caption plus dialogue once, without ``searchable_text`` repeats."""
    return " ".join(
        str(row.get(field) or "")
        for field in ("caption", "dialogue")
    )


def _lexical_ranking(
    query: str,
    rows: Iterable[dict[str, Any]],
) -> list[tuple[dict[str, Any], float]]:
    """Rank rows with a compact BM25 implementation.

    Only positive matches are returned.  Caption, searchable annotation text,
    and dialogue are all part of the document.  Repeated terms in
    ``searchable_text`` naturally give descriptive concepts a modest field
    boost without introducing hand-tuned raw-score mixing.
    """
    query_terms = _unique_query_terms(query)
    if not query_terms:
        return []
    minimum_term_matches = _minimum_term_matches(query_terms)

    # A scan can theoretically contain duplicate unit rows after migrations;
    # score only the first occurrence so document frequency stays meaningful.
    unique_rows: dict[str, dict[str, Any]] = {}
    for row in rows:
        unit_id = _row_id(row)
        if unit_id and unit_id not in unique_rows:
            unique_rows[unit_id] = row

    # Tokenize and count each document exactly once; both document frequency
    # and per-document scoring reuse the same Counter.
    documents: list[tuple[dict[str, Any], int, Counter[str]]] = []
    for row in unique_rows.values():
        tokens = _tokens(_lexical_text(row))
        documents.append((row, len(tokens), Counter(tokens)))
    if not documents:
        return []

    document_count = len(documents)
    average_length = (
        sum(length for _, length, _ in documents) / document_count
    ) or 1.0
    document_frequency = {
        term: sum(counts[term] > 0 for _, _, counts in documents)
        for term in query_terms
    }

    # Standard BM25 constants.  Their absolute scale is intentionally
    # irrelevant because fusion uses rank, not this score.
    k1 = 1.5
    b = 0.75
    ranked: list[tuple[dict[str, Any], float]] = []
    for row, document_length, counts in documents:
        matched_terms = sum(
            counts.get(term, 0) > 0
            for term in query_terms
        )
        if matched_terms < minimum_term_matches:
            continue
        length_norm = 1.0 - b + b * document_length / average_length
        score = 0.0
        for term in query_terms:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            df = document_frequency[term]
            inverse_document_frequency = math.log(
                1.0 + (document_count - df + 0.5) / (df + 0.5)
            )
            score += inverse_document_frequency * (
                frequency * (k1 + 1.0)
                / (frequency + k1 * length_norm)
            )
        if score > 0.0:
            ranked.append((row, score))

    ranked.sort(key=lambda item: (-item[1], _row_id(item[0])))
    return ranked[:_CANDIDATE_LIMIT]


def _minimum_term_matches(query_terms: list[str]) -> int:
    """Require compound lexical queries to have more than incidental evidence."""
    return 1 if len(query_terms) == 1 else 2


def _native_fts_query(query_terms: list[str]) -> MatchQuery | BooleanQuery:
    """Build an index-native query requiring two terms for compound prompts.

    Pairwise boolean clauses preserve the original high-precision rule (any
    two meaningful terms) while letting Lance's tokenizer perform stemming and
    ASCII folding. A raw Python token check would wrongly discard valid native
    matches such as ``runs`` against ``running``.
    """
    term_queries = [
        MatchQuery(
            term,
            "searchable_text",
            operator=FullTextOperator.OR,
        )
        for term in query_terms
    ]
    if len(term_queries) == 1:
        return term_queries[0]
    pairs = [
        BooleanQuery(
            [
                (Occur.MUST, term_queries[first]),
                (Occur.MUST, term_queries[second]),
            ]
        )
        for first, second in combinations(range(len(term_queries)), 2)
    ]
    return BooleanQuery([(Occur.SHOULD, pair) for pair in pairs])


def _native_lexical_ranking(
    query: str,
    table: Any,
    representative_filter: Any,
    film_ids: tuple[str, ...],
) -> list[tuple[dict[str, Any], float]]:
    """Return bounded native-FTS candidates, with a correctness fallback.

    API startup and film publication keep the versioned FTS index current.
    The unbounded Python fallback is deliberately slow-but-correct for direct
    library callers that bypass that lifecycle; it never silently truncates a
    growing collection as the previous 10,000-row scan did.
    """
    query_terms = _unique_query_terms(query)
    if not query_terms:
        return []

    fts_query = _native_fts_query(query_terms)
    try:
        rows = (
            table.search(fts_query, query_type="fts")
            .select(_FTS_COLUMNS)
            .where(representative_filter)
            .limit(_LEXICAL_CANDIDATE_LIMIT)
            .to_list()
        )
    except (RuntimeError, ValueError):
        rows = (
            table.search()
            .select(_LEXICAL_COLUMNS)
            .where(representative_filter)
            .limit(None)
            .to_list()
        )
        return _lexical_ranking(
            query,
            _rows_in_film_scope(rows, film_ids),
        )

    rows = _rows_in_film_scope(rows, film_ids)
    # Unit-test doubles and legacy indexless callers may not expose Lance's
    # synthetic score column. Preserve correct behavior without weakening the
    # production requirement that startup validates the real native index.
    if rows and any("_score" not in row for row in rows):
        return _lexical_ranking(query, rows)

    ranked = [(row, float(row["_score"])) for row in rows]
    ranked.sort(key=lambda item: (-item[1], _row_id(item[0])))
    return ranked[:_CANDIDATE_LIMIT]


def _attach_image_vectors(
    ranked: list[tuple[dict[str, Any], float]],
    unit_table: Any,
    film_ids: tuple[str, ...],
) -> list[tuple[dict[str, Any], float]]:
    """Fetch ``img_vec`` for the bounded ranked rows the dedup pass will see.

    The corpus scan deliberately omits vectors; only the ≤``_CANDIDATE_LIMIT``
    rows that survive lexical ranking need visual evidence downstream.
    """
    unit_ids = tuple(
        dict.fromkeys(
            unit_id for row, _score in ranked if (unit_id := _row_id(row))
        )
    )
    unit_filter = _unit_filter(unit_ids)
    if unit_filter is None:
        return ranked
    vector_rows = (
        unit_table.search()
        .select(["unit_id", "img_vec"])
        .where(_representative_filter(film_ids) & unit_filter)
        .limit(len(unit_ids))
        .to_list()
    )
    vectors = {
        str(row["unit_id"]): row.get("img_vec")
        for row in vector_rows
        if row.get("unit_id")
    }
    attached: list[tuple[dict[str, Any], float]] = []
    for row, score in ranked:
        vector = vectors.get(_row_id(row))
        attached.append(({**row, "img_vec": vector} if vector else row, score))
    return attached


#: Reference-image search has no text query, so nothing is ever "requested".
_NO_REQUESTED_JUNK: set[str] = set()


def _requested_junk_categories(query: str) -> set[str]:
    return {
        category
        for category, pattern in _JUNK_QUERY_PATTERNS.items()
        if (
            pattern.search(query)
            and not _JUNK_NEGATED_QUERY_PATTERNS[category].search(query)
        )
    }


def _is_unrequested_junk(
    row: dict[str, Any],
    query: str,
    requested: set[str] | None = None,
) -> bool:
    """Return whether the row is junk the query did not explicitly ask for.

    Callers looping over many candidates should hoist
    ``_requested_junk_categories(query)`` and pass it as *requested*; the
    query-side regexes are constant per request.
    """
    text = _row_text(row)
    if requested is None:
        requested = _requested_junk_categories(query)
    detected = {
        category
        for category, patterns in _JUNK_PATTERNS.items()
        if any(pattern.search(text) for pattern in patterns)
    }
    blocking_categories = detected - requested
    # Black/static describes the substrate of many requested credit, logo, and
    # title cards; it is not a separate reason to suppress that requested
    # content.  The inverse is intentionally not true: asking for a black
    # screen must not re-enable a credit roll.
    if detected & requested & {"credits", "logos", "title_cards"}:
        blocking_categories -= {"blank", "static"}
    return bool(blocking_categories)


def _as_float_vec(value: Any) -> np.ndarray | None:
    """Convert a stored vector to a 1-D float32 array, or ``None``."""
    try:
        vec = np.asarray(value, dtype=np.float32)
    except (TypeError, ValueError):
        return None
    if vec.ndim != 1 or vec.size == 0:
        return None
    return vec


def _cosine_similarity(left: Any, right: Any) -> float | None:
    """Return cosine similarity, or ``None`` for absent/invalid vectors."""
    try:
        left_vec = np.asarray(left, dtype=np.float32)
        right_vec = np.asarray(right, dtype=np.float32)
    except (TypeError, ValueError):
        return None
    if (
        left_vec.ndim != 1
        or right_vec.ndim != 1
        or left_vec.shape != right_vec.shape
        or left_vec.size == 0
    ):
        return None
    denominator = float(np.linalg.norm(left_vec) * np.linalg.norm(right_vec))
    if denominator == 0.0:
        return None
    return float(np.dot(left_vec, right_vec) / denominator)


def _temporally_close(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if str(left.get("film_id")) != str(right.get("film_id")):
        return False
    try:
        left_midpoint = (float(left["t_start"]) + float(left["t_end"])) / 2.0
        right_midpoint = (float(right["t_start"]) + float(right["t_end"])) / 2.0
    except (KeyError, TypeError, ValueError):
        return False
    return abs(left_midpoint - right_midpoint) <= _TEMPORAL_WINDOW_SECONDS


def _is_duplicate(
    candidate: dict[str, Any],
    selected: Iterable[dict[str, Any]],
) -> bool:
    # Stored vectors arrive as Python float lists; converting them is the
    # dominant cost of this pass, so convert each row at most once and stash
    # the array on the row dict for later comparisons.
    candidate_vec = _as_float_vec(candidate.get("img_vec"))
    if candidate_vec is None:
        return False
    candidate_norm = float(np.linalg.norm(candidate_vec))
    if candidate_norm == 0.0:
        return False

    for prior in selected:
        prior_vec = prior.get("_img_np")
        if prior_vec is None:
            prior_vec = _as_float_vec(prior.get("img_vec"))
            if prior_vec is None:
                continue
            prior["_img_np"] = prior_vec
        if prior_vec.shape != candidate_vec.shape:
            continue
        denominator = candidate_norm * float(np.linalg.norm(prior_vec))
        if denominator == 0.0:
            continue
        similarity = float(np.dot(candidate_vec, prior_vec) / denominator)
        if similarity >= _VISUAL_DUP_COSINE:
            return True
        if (
            similarity >= _TEMPORAL_VISUAL_DUP_COSINE
            and _temporally_close(candidate, prior)
        ):
            return True
    return False


def _keyframe_index(row: dict[str, Any]) -> int:
    """Choose the middle recorded keyframe (index 1 for a three-frame shot)."""
    raw_paths = row.get("keyframe_paths")
    try:
        paths = json.loads(raw_paths) if isinstance(raw_paths, str) else raw_paths
    except (TypeError, json.JSONDecodeError):
        return 0
    if not isinstance(paths, list) or not paths:
        return 0
    return len(paths) // 2


def _frame_image_ranking(
    frame_rows: Iterable[dict[str, Any]],
    unit_rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse frame hits to shots using each shot's best-matching frame.

    A frame table may contain one or three rows per shot.  Returning frame
    rows directly would let longer shots occupy several candidate positions
    and would leave the result without its shot caption and timing.  This
    pass uses a max-similarity baseline (minimum cosine distance), retains
    the argmax frame as evidence, and emits each representative unit once.
    """
    units_by_id = {
        str(row.get("unit_id") or row.get("shot_id") or ""): row
        for row in unit_rows
        if row.get("unit_id") or row.get("shot_id")
    }
    best_frame_by_unit: dict[str, dict[str, Any]] = {}
    for frame in frame_rows:
        unit_id = str(frame.get("unit_id") or frame.get("shot_id") or "")
        if not unit_id or unit_id not in units_by_id:
            continue
        try:
            distance = float(frame.get("_distance", 1.0))
        except (TypeError, ValueError):
            continue
        prior = best_frame_by_unit.get(unit_id)
        if prior is None or distance < float(prior["_distance"]):
            best_frame_by_unit[unit_id] = {**frame, "_distance": distance}

    ranked: list[dict[str, Any]] = []
    for unit_id, frame in best_frame_by_unit.items():
        unit = dict(units_by_id[unit_id])
        unit["_distance"] = float(frame["_distance"])
        unit["_matched_frame"] = {
            "frame_id": frame.get("frame_id"),
            "frame_index": frame.get("frame_index"),
            "timestamp": frame.get("timestamp"),
        }
        ranked.append(unit)
    ranked.sort(
        key=lambda row: (
            float(row["_distance"]),
            str(row.get("unit_id") or row.get("shot_id") or ""),
        )
    )
    return ranked[:_CANDIDATE_LIMIT]


def _frame_search_rows(
    vector: np.ndarray,
    db: lancedb.DBConnection,
    unit_table: Any,
    film_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Return shot rows ranked by their best frame, or an empty fallback cue.

    Frame search is independent from the bounded lexical candidate set. Once
    the nearest frames are known, fetch only their owning units so a frame hit
    remains eligible even when lexical retrieval did not select that unit.
    """
    if "frames" not in table_names(db):
        return []

    frame_query = (
        db.open_table("frames")
        .search(vector, vector_column_name="visual_vec")
        .metric("cosine")
    )
    film_filter = _film_filter(film_ids)
    if film_filter is not None:
        frame_query = frame_query.where(film_filter)
    frame_rows = frame_query.limit(_FRAME_CANDIDATE_LIMIT).to_list()
    frame_rows = _rows_in_film_scope(frame_rows, film_ids)
    if not frame_rows:
        return []

    unit_ids = tuple(
        dict.fromkeys(
            str(row.get("unit_id") or row.get("shot_id") or "")
            for row in frame_rows
            if row.get("unit_id") or row.get("shot_id")
        )
    )
    unit_filter = _unit_filter(unit_ids)
    if unit_filter is None:
        return []
    unit_rows = (
        unit_table.search()
        .select(_CANDIDATE_COLUMNS)
        .where(_representative_filter(film_ids) & unit_filter)
        .limit(len(unit_ids))
        .to_list()
    )
    unit_rows = _rows_in_film_scope(unit_rows, film_ids)
    return _frame_image_ranking(frame_rows, unit_rows)


def _channel_debug(rank: int, score: float, distance: float | None) -> dict:
    return {
        "rank": int(rank),
        "score": float(score),
        "distance": float(distance) if distance is not None else None,
    }


def _normalise_film_ids(film_ids: Iterable[str] | None) -> tuple[str, ...]:
    """Return a stable, de-duplicated film scope; an empty tuple means all."""
    if film_ids is None:
        return ()
    return tuple(
        dict.fromkeys(
            str(film_id).strip()
            for film_id in film_ids
            if str(film_id).strip()
        )
    )


def _any_of(column: str, values: tuple[str, ...]) -> Any | None:
    """Build a safe Lance expression matching any of *values* in *column*."""
    if not values:
        return None
    expression = col(column) == lit(values[0])
    for value in values[1:]:
        expression = expression | (col(column) == lit(value))
    return expression


def _film_filter(film_ids: tuple[str, ...]) -> Any | None:
    """Build a safe Lance expression matching any selected film ID."""
    return _any_of("film_id", film_ids)


def _unit_filter(unit_ids: tuple[str, ...]) -> Any | None:
    """Build a safe Lance expression matching returned frame-hit unit IDs."""
    return _any_of("unit_id", unit_ids)


def _representative_filter(film_ids: tuple[str, ...]) -> Any:
    expression = col("is_representative") == lit(True)
    film_filter = _film_filter(film_ids)
    return expression if film_filter is None else expression & film_filter


def _rows_in_film_scope(
    rows: Iterable[dict[str, Any]],
    film_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Defensively enforce film scope after database prefiltering."""
    materialized = list(rows)
    if not film_ids:
        return materialized
    allowed = set(film_ids)
    return [
        row
        for row in materialized
        if str(row.get("film_id") or "") in allowed
    ]


def search(
    query: str,
    db: lancedb.DBConnection,
    config: Config,
    *,
    film_ids: Iterable[str] | None = None,
) -> list[dict]:
    """Return up to twelve hybrid, junk-filtered, diverse search results."""
    weights = {
        "img": float(config.retrieval.weights.img),
        "txt": float(config.retrieval.weights.txt),
        "lex": float(config.retrieval.weights.lex),
    }
    if any(weight < 0.0 for weight in weights.values()):
        raise ValueError("retrieval channel weights must be non-negative")
    enabled = {channel for channel, weight in weights.items() if weight > 0.0}
    if not enabled:
        raise ValueError("at least one retrieval channel weight must be positive")

    table = db.open_table("units")
    scoped_film_ids = _normalise_film_ids(film_ids)
    representative_filter = _representative_filter(scoped_film_ids)

    # PE is a joint image/text model, so one query embedding can serve both
    # vector channels.  Crucially, do not load it for lexical-only variants.
    vector: np.ndarray | None = None
    if enabled & {"img", "txt"}:
        vector = embed_text([query], config)[0]

    text_rows: list[dict[str, Any]] = []
    if "txt" in enabled:
        assert vector is not None
        text_rows = _rows_in_film_scope(
            table.search(vector, vector_column_name="txt_vec")
            .metric("cosine")
            .where(representative_filter)
            .limit(_CANDIDATE_LIMIT)
            .to_list(),
            scoped_film_ids,
        )

    lexical_ranked: list[tuple[dict[str, Any], float]] = []
    if "lex" in enabled:
        lexical_ranked = _attach_image_vectors(
            _native_lexical_ranking(
                query,
                table,
                representative_filter,
                scoped_film_ids,
            ),
            table,
            scoped_film_ids,
        )

    image_rows: list[dict[str, Any]] = []
    if "img" in enabled:
        assert vector is not None
        image_rows = _frame_search_rows(
            vector,
            db,
            table,
            scoped_film_ids,
        )
        if not image_rows:
            image_rows = _rows_in_film_scope(
                table.search(vector, vector_column_name="img_vec")
                .metric("cosine")
                .where(representative_filter)
                .limit(_CANDIDATE_LIMIT)
                .to_list(),
                scoped_film_ids,
            )

    fused: dict[str, dict[str, Any]] = {}

    def add_channel(
        channel: str,
        ranked_rows: Iterable[tuple[dict[str, Any], float, float | None]],
    ) -> None:
        seen: set[str] = set()
        for rank, (row, score, distance) in enumerate(ranked_rows, start=1):
            unit_id = str(row.get("unit_id") or row.get("shot_id") or "")
            if not unit_id or unit_id in seen:
                continue
            seen.add(unit_id)
            candidate = fused.setdefault(
                unit_id,
                {
                    "row": dict(row),
                    "final_score": 0.0,
                    "channels": {},
                },
            )
            # Vector results contain the complete source row.  Preserve any
            # fields absent from the first channel that found this candidate.
            for key, value in row.items():
                if key != "_distance" and key not in candidate["row"]:
                    candidate["row"][key] = value
            channel_evidence = _channel_debug(
                rank,
                score,
                distance,
            )
            if channel == "img":
                matched_frame = row.get("_matched_frame")
                channel_evidence["source"] = (
                    "frame" if isinstance(matched_frame, dict) else "unit"
                )
                if isinstance(matched_frame, dict):
                    channel_evidence["matched_frame"] = matched_frame
            candidate["channels"][channel] = channel_evidence
            candidate["final_score"] += weights[channel] / (_RRF_K + rank)

    def vector_channel(
        rows: Iterable[dict[str, Any]],
    ) -> Iterable[tuple[dict[str, Any], float, float]]:
        for row in rows:
            distance = float(row.get("_distance", 1.0))
            yield row, 1.0 - distance, distance

    add_channel("img", vector_channel(image_rows))
    add_channel("txt", vector_channel(text_rows))
    add_channel(
        "lex",
        ((row, score, None) for row, score in lexical_ranked),
    )

    ordered = sorted(
        fused.values(),
        key=lambda candidate: (
            -float(candidate["final_score"]),
            str(candidate["row"].get("unit_id") or ""),
        ),
    )

    selected: list[dict[str, Any]] = []
    selected_candidates: list[dict[str, Any]] = []
    requested_junk = _requested_junk_categories(query)
    # An explicit movie scope is a relevance constraint chosen by the user,
    # not a browse request that needs library-wide balancing.  Applying the
    # global cap to a one- or two-film scope can underfill a twelve-result page
    # (the previous default returned only four results for one selected film).
    max_per_film = (
        0
        if scoped_film_ids
        else int(config.retrieval.diversity.max_per_film)
    )
    film_result_counts: Counter[str] = Counter()
    for candidate in ordered:
        row = candidate["row"]
        film_id = str(row.get("film_id") or "")
        if max_per_film > 0 and film_result_counts[film_id] >= max_per_film:
            continue
        if _is_unrequested_junk(row, query, requested_junk):
            continue
        if _is_duplicate(row, selected_candidates):
            continue

        shot_id = str(row.get("shot_id") or row.get("unit_id") or "")
        result_rank = len(selected) + 1
        matched_frame = row.get("_matched_frame")
        matched_frame_index = (
            matched_frame.get("frame_index")
            if isinstance(matched_frame, dict)
            else None
        )
        try:
            keyframe_index = int(matched_frame_index)
        except (TypeError, ValueError):
            keyframe_index = _keyframe_index(row)
        keyframe_url = f"/media/keyframe/{shot_id}/{keyframe_index}"
        result = {
            "unit_id": row["unit_id"],
            "film_id": row["film_id"],
            "t_start": row["t_start"],
            "t_end": row["t_end"],
            "caption": row["caption"],
            "keyframe_url": keyframe_url,
            "preview_url": f"/media/preview/{shot_id}",
            "rank": result_rank,
            "debug": {
                "final_score": float(candidate["final_score"]),
                "channels": candidate["channels"],
            },
        }
        if isinstance(matched_frame, dict):
            result["matched_frame_url"] = keyframe_url
            result["matched_frame_index"] = keyframe_index
            result["matched_frame_timestamp"] = matched_frame.get("timestamp")
        selected.append(result)
        selected_candidates.append(row)
        film_result_counts[film_id] += 1
        if len(selected) >= _RESULT_LIMIT:
            break

    return selected


def _spatial_grid_scores(
    query_grid: np.ndarray,
    candidate_grids: np.ndarray,
) -> np.ndarray:
    """Return mean corresponding-cell cosine similarity for each candidate."""
    if query_grid.ndim != 3:
        raise ValueError(
            "query spatial grid must have shape (H, W, D); "
            f"got {query_grid.shape}"
        )
    if (
        candidate_grids.ndim != 4
        or candidate_grids.shape[1:] != query_grid.shape
    ):
        raise ValueError(
            "candidate spatial grids must have shape (N, H, W, D) "
            f"matching {query_grid.shape}; got {candidate_grids.shape}"
        )
    cell_cosines = np.einsum(
        "hwd,nhwd->nhw",
        query_grid,
        candidate_grids,
        optimize=True,
    )
    scores = np.mean(cell_cosines, axis=(1, 2))
    return np.nan_to_num(scores, nan=-1.0).clip(-1.0, 1.0).astype(
        np.float32
    )


def _reference_frame_candidates(
    image: Image.Image,
    db: lancedb.DBConnection,
    config: Config,
    film_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Retrieve global image neighbors, then compare learned spatial grids."""
    if "frames" not in table_names(db):
        return []

    query_global, query_spatial = embed_spatial_images(
        [image],
        config,
        grid_size=_REFERENCE_SPATIAL_GRID_SIZE,
    )
    frame_query = (
        db.open_table("frames")
        .search(query_global[0], vector_column_name="visual_vec")
        .metric("cosine")
    )
    film_filter = _film_filter(film_ids)
    if film_filter is not None:
        frame_query = frame_query.where(film_filter)
    frame_rows = _rows_in_film_scope(
        frame_query.limit(_REFERENCE_RAW_FRAME_CANDIDATE_LIMIT).to_list(),
        film_ids,
    )
    if not frame_rows:
        return []

    valid_rows: list[dict[str, Any]] = []
    candidate_images: list[Image.Image] = []
    seen_units: set[str] = set()
    for row in frame_rows:
        unit_id = str(row.get("unit_id") or row.get("shot_id") or "")
        if not unit_id or unit_id in seen_units:
            continue
        raw_path = row.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            continue
        try:
            with Image.open(raw_path) as candidate:
                candidate_images.append(candidate.convert("RGB"))
        except (OSError, ValueError):
            continue
        valid_rows.append(dict(row))
        seen_units.add(unit_id)
        if len(valid_rows) >= _REFERENCE_FRAME_CANDIDATE_LIMIT:
            break

    if not valid_rows:
        return []

    spatial_scores: np.ndarray | None = None
    if query_spatial is not None:
        _candidate_global, candidate_spatial = embed_spatial_images(
            candidate_images,
            config,
            grid_size=_REFERENCE_SPATIAL_GRID_SIZE,
        )
        if candidate_spatial is not None:
            spatial_scores = _spatial_grid_scores(
                query_spatial[0],
                candidate_spatial,
            )

    spatial_ranks: dict[int, int] = {}
    if spatial_scores is not None:
        spatial_order = sorted(
            range(len(valid_rows)),
            key=lambda index: (
                -float(spatial_scores[index]),
                str(valid_rows[index].get("frame_id") or ""),
            ),
        )
        spatial_ranks = {
            row_index: rank
            for rank, row_index in enumerate(spatial_order, start=1)
        }

    for index, row in enumerate(valid_rows):
        try:
            semantic_distance = float(row.get("_distance", 1.0))
        except (TypeError, ValueError):
            semantic_distance = 1.0
        semantic_score = max(-1.0, min(1.0, 1.0 - semantic_distance))
        spatial_score = (
            float(spatial_scores[index])
            if spatial_scores is not None
            else None
        )
        final_score = (
            semantic_score
            if spatial_score is None
            else (
                _REFERENCE_GLOBAL_WEIGHT * semantic_score
                + _REFERENCE_SPATIAL_WEIGHT * spatial_score
            )
        )
        row["_reference_score"] = float(final_score)
        row["_semantic_score"] = float(semantic_score)
        row["_semantic_rank"] = index + 1
        row["_spatial_score"] = spatial_score
        row["_spatial_rank"] = spatial_ranks.get(index)

    valid_rows.sort(
        key=lambda row: (
            -float(row["_reference_score"]),
            str(row.get("frame_id") or ""),
        )
    )
    return valid_rows


def _is_reference_temporal_duplicate(
    candidate: dict[str, Any],
    selected: Iterable[dict[str, Any]],
) -> bool:
    """Avoid filling a result page with one short stretch of a film."""
    try:
        candidate_timestamp = float(candidate["timestamp"])
    except (KeyError, TypeError, ValueError):
        return False
    candidate_film_id = str(candidate.get("film_id") or "")
    for prior in selected:
        if str(prior.get("film_id") or "") != candidate_film_id:
            continue
        try:
            prior_timestamp = float(prior["timestamp"])
        except (KeyError, TypeError, ValueError):
            continue
        if (
            abs(candidate_timestamp - prior_timestamp)
            <= _REFERENCE_RESULT_TEMPORAL_GAP_SECONDS
        ):
            return True
    return False


def search_by_image(
    image: Image.Image,
    db: lancedb.DBConnection,
    config: Config,
    *,
    film_ids: Iterable[str] | None = None,
    exclude_unit_id: str | None = None,
) -> list[dict]:
    """Find shots with similar content in similar normalized screen positions.

    PE Core first supplies a broad semantic candidate set from the existing
    frame vectors.  Its final learned patch grid then reranks those candidates
    by corresponding screen cells.  This is a composition/layout prototype;
    it does not claim skeleton-level pose or temporal motion understanding.
    """
    scoped_film_ids = _normalise_film_ids(film_ids)
    frame_rows = _reference_frame_candidates(
        image.convert("RGB"),
        db,
        config,
        scoped_film_ids,
    )
    if not frame_rows:
        return []

    normalized_exclusion = str(exclude_unit_id or "").strip()
    eligible_frame_rows = [
        row
        for row in frame_rows
        if (
            row.get("unit_id") or row.get("shot_id")
        )
        and str(row.get("unit_id") or row.get("shot_id"))
        != normalized_exclusion
    ]
    unit_ids = tuple(
        dict.fromkeys(
            str(row.get("unit_id") or row.get("shot_id") or "")
            for row in eligible_frame_rows
        )
    )
    unit_filter = _unit_filter(unit_ids)
    if unit_filter is None:
        return []

    unit_table = db.open_table("units")
    unit_rows = (
        unit_table.search()
        .select(_CANDIDATE_COLUMNS)
        .where(_representative_filter(scoped_film_ids) & unit_filter)
        .limit(len(unit_ids))
        .to_list()
    )
    units_by_id = {
        str(row.get("unit_id") or row.get("shot_id") or ""): row
        for row in _rows_in_film_scope(unit_rows, scoped_film_ids)
        if row.get("unit_id") or row.get("shot_id")
    }

    selected_units: set[str] = set()
    selected_frames: list[dict[str, Any]] = []
    selected_candidates: list[dict[str, Any]] = []
    results: list[dict] = []

    def append_result(frame: dict[str, Any]) -> bool:
        unit_id = str(frame.get("unit_id") or frame.get("shot_id") or "")
        if (
            not unit_id
            or unit_id in selected_units
            or unit_id not in units_by_id
        ):
            return False

        row = units_by_id[unit_id]
        if _is_unrequested_junk(row, "", _NO_REQUESTED_JUNK):
            return False
        if _is_duplicate(row, selected_candidates):
            return False
        shot_id = str(row.get("shot_id") or unit_id)
        try:
            keyframe_index = int(frame.get("frame_index"))
        except (TypeError, ValueError):
            keyframe_index = _keyframe_index(row)
        keyframe_url = f"/media/keyframe/{shot_id}/{keyframe_index}"
        matched_frame = {
            "frame_id": frame.get("frame_id"),
            "frame_index": keyframe_index,
            "timestamp": frame.get("timestamp"),
        }
        image_channel = {
            **_channel_debug(
                int(frame["_semantic_rank"]),
                float(frame["_semantic_score"]),
                float(frame.get("_distance", 1.0)),
            ),
            "source": "frame",
            "matched_frame": matched_frame,
        }
        channels: dict[str, dict[str, Any]] = {"img": image_channel}
        if frame.get("_spatial_score") is not None:
            channels["spatial"] = _channel_debug(
                int(frame["_spatial_rank"]),
                float(frame["_spatial_score"]),
                1.0 - float(frame["_spatial_score"]),
            )

        result_rank = len(results) + 1
        results.append(
            {
                "unit_id": unit_id,
                "film_id": row["film_id"],
                "t_start": row["t_start"],
                "t_end": row["t_end"],
                "caption": row["caption"],
                "keyframe_url": keyframe_url,
                "preview_url": f"/media/preview/{shot_id}",
                "matched_frame_url": keyframe_url,
                "matched_frame_index": keyframe_index,
                "matched_frame_timestamp": frame.get("timestamp"),
                "rank": result_rank,
                "debug": {
                    "mode": "reference_image",
                    "final_score": float(frame["_reference_score"]),
                    "channels": channels,
                },
            }
        )
        selected_units.add(unit_id)
        selected_frames.append(frame)
        selected_candidates.append(row)
        return True

    # Prefer a temporally broad page so one sequence cannot dominate.  If that
    # pass underfills, backfill with the strongest deferred matches instead of
    # hiding useful adjacent match cuts.
    deferred_frames: list[dict[str, Any]] = []
    for frame in eligible_frame_rows:
        if _is_reference_temporal_duplicate(frame, selected_frames):
            deferred_frames.append(frame)
            continue
        append_result(frame)
        if len(results) >= _RESULT_LIMIT:
            break

    if len(results) < _RESULT_LIMIT:
        for frame in deferred_frames:
            append_result(frame)
            if len(results) >= _RESULT_LIMIT:
                break

    return results
