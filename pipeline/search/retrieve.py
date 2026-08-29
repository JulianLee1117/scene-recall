"""Hybrid retrieval over the LanceDB ``units`` and ``frames`` tables.

Enabled channels independently retrieve visual, semantic-text, and native
full-text candidates.  Their ranked lists are combined with weighted
reciprocal-rank fusion (RRF), so incomparable cosine distances and BM25
scores are never added together.  A zero channel weight is a true ablation:
that channel performs no retrieval (and lexical-only search loads no model).
"""

from __future__ import annotations

import json
import logging
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

from pipeline.config import (
    DEFAULT_SEARCH_CANDIDATE_LIMIT,
    DEFAULT_SEARCH_RESULT_WINDOW,
    Config,
)
from pipeline.index.text_features import (
    TEXT_VIEWS,
    TextIndexProfile,
    resolve_ready_text_profile,
)
from pipeline.index.writer import require_visual_encoder_profile, table_names
from pipeline.ingest.embed import embed_spatial_images, embed_text
from pipeline.ingest.text_embed import embed_semantic_query


_LOGGER = logging.getLogger(__name__)

_CANDIDATE_LIMIT = DEFAULT_SEARCH_CANDIDATE_LIMIT
_REFERENCE_SPATIAL_CANDIDATE_LIMIT = 96
_REFERENCE_SPATIAL_GRID_SIZE = 6
_REFERENCE_GLOBAL_WEIGHT = 0.65
_REFERENCE_SPATIAL_WEIGHT = 1.0 - _REFERENCE_GLOBAL_WEIGHT
_REFERENCE_RESULT_TEMPORAL_GAP_SECONDS = 90.0
_REFERENCE_QUERY_WEIGHT = 0.5
_TEXT_CONSTRAINT_WEIGHT = 0.5
_MAX_LEXICAL_QUERY_TERMS = 12
# The default is a ranked search window, not a frontend page size. Callers may
# request a smaller stable prefix or a deeper evaluation window explicitly.
SEARCH_RESULT_LIMIT = DEFAULT_SEARCH_RESULT_WINDOW
_RRF_K = 60
# Treat time as supporting evidence for a duplicate, never as a duplicate by
# itself: .90 within 30 seconds, or .92 at any distance/film.  Measured on a
# four-film library: same-subject repeats (one character's close-ups across a
# scene) cluster at .92-.94 cosine minutes apart, while deliberate visual
# callbacks sit at ~.91 and must survive.
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
_TEXT_FEATURE_COLUMNS = [
    "feature_id",
    "profile_id",
    "film_id",
    "unit_id",
    "view",
    "text",
    "_distance",
]


class SemanticTextProfileUnavailable(RuntimeError):
    """Raised when a view-specific clause has no complete text profile.

    Broad search can safely fall back to the legacy combined text vector, but
    a facet clause cannot: doing so would silently turn caption-, word-, or
    facet-only search back into one inseparable document.
    """

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
    *,
    candidate_limit: int = _CANDIDATE_LIMIT,
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
    return ranked[:candidate_limit]


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
    *,
    candidate_limit: int = _CANDIDATE_LIMIT,
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
            .limit(candidate_limit * 3)
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
            candidate_limit=candidate_limit,
        )

    rows = _rows_in_film_scope(rows, film_ids)
    # Unit-test doubles and legacy indexless callers may not expose Lance's
    # synthetic score column. Preserve correct behavior without weakening the
    # production requirement that startup validates the real native index.
    if rows and any("_score" not in row for row in rows):
        return _lexical_ranking(
            query,
            rows,
            candidate_limit=candidate_limit,
        )

    ranked = [(row, float(row["_score"])) for row in rows]
    ranked.sort(key=lambda item: (-item[1], _row_id(item[0])))
    return ranked[:candidate_limit]


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
    *,
    candidate_limit: int = _CANDIDATE_LIMIT,
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
    return ranked[:candidate_limit]


def _frame_search_rows(
    vector: np.ndarray,
    db: lancedb.DBConnection,
    unit_table: Any,
    film_ids: tuple[str, ...],
    *,
    candidate_limit: int = _CANDIDATE_LIMIT,
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
    frame_rows = _stable_vector_ranking(
        _rows_in_film_scope(
            frame_query.limit(candidate_limit * 3).to_list(),
            film_ids,
        ),
        candidate_limit=candidate_limit * 3,
    )
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
    return _frame_image_ranking(
        frame_rows,
        unit_rows,
        candidate_limit=candidate_limit,
    )


def _validated_semantic_views(
    allowed_views: Iterable[str] | None,
) -> tuple[str, ...]:
    """Return a stable non-empty subset of the indexed semantic views."""
    requested_views = tuple(
        dict.fromkeys(
            str(view).strip()
            for view in (TEXT_VIEWS if allowed_views is None else allowed_views)
            if str(view).strip()
        )
    )
    invalid_views = set(requested_views) - set(TEXT_VIEWS)
    if not requested_views or invalid_views:
        raise ValueError(
            "semantic text views must be a non-empty subset of "
            f"{TEXT_VIEWS}; got {requested_views}"
        )
    return requested_views


def _semantic_text_search_rows(
    vector: np.ndarray,
    db: lancedb.DBConnection,
    unit_table: Any,
    profile: TextIndexProfile,
    film_ids: tuple[str, ...],
    *,
    candidate_limit: int = _CANDIDATE_LIMIT,
    allowed_views: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Rank independent text views, then collapse them to one vote per unit."""
    requested_views = _validated_semantic_views(allowed_views)
    feature_filter = col("is_representative") == lit(True)
    film_filter = _film_filter(film_ids)
    if film_filter is not None:
        feature_filter = feature_filter & film_filter
    view_filter = _any_of("view", requested_views)
    assert view_filter is not None
    feature_filter = feature_filter & view_filter
    feature_rows = (
        db.open_table(profile.table_name)
        .search(vector, vector_column_name="vector")
        .metric("cosine")
        # Ask for scoring evidence explicitly without materializing hundreds
        # of unused 1024-float document vectors into Python for every query.
        .select(_TEXT_FEATURE_COLUMNS)
        .where(feature_filter)
        .limit(candidate_limit * len(requested_views))
        .to_list()
    )
    feature_rows = _rows_in_film_scope(feature_rows, film_ids)
    allowed_view_set = set(requested_views)
    feature_rows = [
        row
        for row in feature_rows
        if str(row.get("view") or "") in allowed_view_set
    ]
    feature_rows.sort(
        key=lambda row: (
            float(row.get("_distance", 1.0)),
            str(row.get("feature_id") or ""),
        )
    )

    best_by_unit: dict[str, dict[str, Any]] = {}
    for feature in feature_rows:
        unit_id = str(feature.get("unit_id") or "")
        if unit_id and unit_id not in best_by_unit:
            best_by_unit[unit_id] = feature
        if len(best_by_unit) >= candidate_limit:
            break
    unit_ids = tuple(best_by_unit)
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
    units_by_id = {
        _row_id(row): row
        for row in _rows_in_film_scope(unit_rows, film_ids)
        if _row_id(row)
    }
    ranked: list[dict[str, Any]] = []
    for unit_id, feature in best_by_unit.items():
        unit = units_by_id.get(unit_id)
        if unit is None:
            continue
        row = dict(unit)
        row["_distance"] = float(feature.get("_distance", 1.0))
        row["_matched_text"] = {
            "feature_id": feature.get("feature_id"),
            "view": feature.get("view"),
            "text": feature.get("text"),
            "profile_id": feature.get("profile_id"),
        }
        ranked.append(row)
    ranked.sort(key=lambda row: (float(row["_distance"]), _row_id(row)))
    return ranked[:candidate_limit]


def _ready_text_profile(
    config: Config,
    db: lancedb.DBConnection,
) -> TextIndexProfile | None:
    """Treat optional derived-profile failures as a legacy fallback cue."""
    try:
        return resolve_ready_text_profile(config, db)
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return None


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


def _reference_film_scope(
    db: lancedb.DBConnection,
    film_ids: Iterable[str] | None,
    exclude_film_id: str | None,
) -> tuple[tuple[str, ...], bool]:
    """Return the effective reference scope and whether to prefer diversity.

    An empty film scope means "all films" throughout normal retrieval, so an
    unscoped cross-film request must be expanded before removing its source
    film.  Reading the small films table keeps that exclusion ahead of ANN
    candidate generation instead of filtering an already source-heavy window.
    """
    requested = _normalise_film_ids(film_ids)
    excluded = str(exclude_film_id or "").strip()
    if not excluded:
        return requested, not bool(requested)
    if requested:
        remaining = tuple(
            film_id for film_id in requested if film_id != excluded
        )
        # An explicit one-film scope is a stronger instruction than the
        # cross-film convenience default.  Keep that scope usable instead of
        # turning the request into an ambiguous empty tuple ("all films").
        return (remaining or requested), False
    if "films" not in table_names(db):
        return (), True
    rows = (
        db.open_table("films")
        .search()
        .select(["film_id"])
        .limit(None)
        .to_list()
    )
    available = tuple(
        sorted(
            {
                str(row.get("film_id") or "").strip()
                for row in rows
                if str(row.get("film_id") or "").strip()
                and str(row.get("film_id") or "").strip() != excluded
            }
        )
    )
    return available, True


def resolve_reference_result_scope(
    db: lancedb.DBConnection,
    film_ids: Iterable[str] | None,
    exclude_film_id: str | None,
) -> tuple[tuple[str, ...], bool] | None:
    """Resolve a reusable reference scope before any bounded retriever runs.

    ``None`` means source-film exclusion left no eligible published film. The
    distinction matters because an empty tuple normally spells "all films".
    The boolean preserves whether unscoped results should receive the normal
    final film-diversity preference after the positive scope is expanded.
    """
    scoped_film_ids, apply_film_diversity = _reference_film_scope(
        db,
        film_ids,
        exclude_film_id,
    )
    if str(exclude_film_id or "").strip() and not scoped_film_ids:
        return None
    return scoped_film_ids, apply_film_diversity


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


def _stable_vector_ranking(
    rows: Iterable[dict[str, Any]],
    *,
    candidate_limit: int,
) -> list[dict[str, Any]]:
    """Stabilize dense ranks when two rows have the same distance."""
    ranked = list(rows)

    def sort_key(row: dict[str, Any]) -> tuple[float, str]:
        try:
            distance = float(row.get("_distance", 1.0))
        except (TypeError, ValueError):
            distance = 1.0
        return distance, str(row.get("frame_id") or _row_id(row))

    ranked.sort(key=sort_key)
    return ranked[:candidate_limit]


def _validated_search_limits(
    config: Config,
    result_limit: int | None,
) -> tuple[int, int]:
    """Return configured candidate depth and a safe requested result prefix."""
    if result_limit is None:
        result_limit = int(config.retrieval.result_window)
    if isinstance(result_limit, bool) or not isinstance(result_limit, int):
        raise ValueError("result_limit must be an integer")
    max_result_limit = int(config.retrieval.max_result_limit)
    if result_limit < 1 or result_limit > max_result_limit:
        raise ValueError(
            f"result_limit must be between 1 and {max_result_limit}"
        )
    candidate_limit = int(config.retrieval.candidate_limit)
    if candidate_limit < result_limit:
        raise ValueError(
            "retrieval.candidate_limit must be at least result_limit"
        )
    return candidate_limit, result_limit


def _progressive_film_diversity(
    candidates: list[dict[str, Any]],
    *,
    result_limit: int,
    page_size: int,
    per_film_target: int,
) -> list[dict[str, Any]]:
    """Prefer film variety page by page without ever suppressing relevance.

    Each deterministic page first takes candidates whose film has not reached
    that page's cumulative soft target. If those preferred rows cannot fill
    the page, the strongest deferred rows backfill it in original relevance
    order. This produces stable prefixes: asking for 12 and later for 48 gives
    the same first twelve results.
    """
    if result_limit <= 0 or not candidates:
        return []
    if page_size <= 0 or per_film_target <= 0:
        return candidates[:result_limit]

    remaining = list(candidates)
    selected: list[dict[str, Any]] = []
    film_counts: Counter[str] = Counter()
    page_number = 1

    while remaining and len(selected) < result_limit:
        slots = min(page_size, result_limit - len(selected))
        cumulative_target = per_film_target * page_number
        chosen_indices: list[int] = []
        page_counts: Counter[str] = Counter()

        for index, candidate in enumerate(remaining):
            film_id = str(candidate["row"].get("film_id") or "")
            if (
                film_counts[film_id] + page_counts[film_id]
                >= cumulative_target
            ):
                continue
            chosen_indices.append(index)
            page_counts[film_id] += 1
            if len(chosen_indices) >= slots:
                break

        # Diversity is a preference, never an exclusion. Fill any remaining
        # slots from the earliest deferred relevance positions.
        if len(chosen_indices) < slots:
            chosen = set(chosen_indices)
            for index in range(len(remaining)):
                if index in chosen:
                    continue
                chosen_indices.append(index)
                chosen.add(index)
                if len(chosen_indices) >= slots:
                    break

        chosen_set = set(chosen_indices)
        page = [remaining[index] for index in chosen_indices]
        selected.extend(page)
        film_counts.update(
            str(candidate["row"].get("film_id") or "")
            for candidate in page
        )
        remaining = [
            candidate
            for index, candidate in enumerate(remaining)
            if index not in chosen_set
        ]
        page_number += 1

    return selected


def search(
    query: str,
    db: lancedb.DBConnection,
    config: Config,
    *,
    film_ids: Iterable[str] | None = None,
    result_limit: int | None = None,
    apply_film_diversity: bool | None = None,
    _defer_result_preferences: bool = False,
) -> list[dict]:
    """Return a stable hybrid result prefix.

    ``_defer_result_preferences`` is an internal recipe boundary: it preserves
    the bounded channel-fused ranking so product-level filtering can run once
    after independent clause rankings are fused. Public callers retain the
    established junk, visual-deduplication, and diversity defaults.
    """
    candidate_limit, result_limit = _validated_search_limits(
        config,
        result_limit,
    )
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

    text_rows: list[dict[str, Any]] = []
    text_profile = _ready_text_profile(config, db) if "txt" in enabled else None
    pe_vector: np.ndarray | None = None
    if "img" in enabled or ("txt" in enabled and text_profile is None):
        # PE's text tower remains the correct query encoder for image-space
        # retrieval and the complete rollback path for the legacy txt_vec.
        require_visual_encoder_profile(db, config)
        pe_vector = embed_text([query], config)[0]

    if "txt" in enabled:
        if text_profile is not None:
            try:
                text_rows = _semantic_text_search_rows(
                    embed_semantic_query(query, config),
                    db,
                    table,
                    text_profile,
                    scoped_film_ids,
                    candidate_limit=candidate_limit,
                )
            except Exception as exc:
                # The manifest proves stored coverage, not that local weights
                # can always load (cache eviction, OOM, device failure) or the
                # optional derived table can always be read. Fall back as one
                # complete channel rather than returning a 500 or mixing rows.
                _LOGGER.warning(
                    "Semantic text profile %s failed; using the legacy text "
                    "channel for this query: %s",
                    text_profile.profile_id,
                    exc,
                )
                text_profile = None
        if text_profile is None:
            if pe_vector is None:
                require_visual_encoder_profile(db, config)
                pe_vector = embed_text([query], config)[0]
            assert pe_vector is not None
            text_rows = _stable_vector_ranking(
                _rows_in_film_scope(
                    table.search(pe_vector, vector_column_name="txt_vec")
                    .metric("cosine")
                    .where(representative_filter)
                    .limit(candidate_limit)
                    .to_list(),
                    scoped_film_ids,
                ),
                candidate_limit=candidate_limit,
            )

    lexical_ranked: list[tuple[dict[str, Any], float]] = []
    if "lex" in enabled:
        lexical_ranked = _attach_image_vectors(
            _native_lexical_ranking(
                query,
                table,
                representative_filter,
                scoped_film_ids,
                candidate_limit=candidate_limit,
            ),
            table,
            scoped_film_ids,
        )

    image_rows: list[dict[str, Any]] = []
    if "img" in enabled:
        assert pe_vector is not None
        image_rows = _frame_search_rows(
            pe_vector,
            db,
            table,
            scoped_film_ids,
            candidate_limit=candidate_limit,
        )
        if not image_rows:
            image_rows = _stable_vector_ranking(
                _rows_in_film_scope(
                    table.search(pe_vector, vector_column_name="img_vec")
                    .metric("cosine")
                    .where(representative_filter)
                    .limit(candidate_limit)
                    .to_list(),
                    scoped_film_ids,
                ),
                candidate_limit=candidate_limit,
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
            elif channel == "txt":
                matched_text = row.get("_matched_text")
                if isinstance(matched_text, dict):
                    channel_evidence["source"] = str(
                        matched_text.get("view") or "semantic_text"
                    )
                    channel_evidence["matched_text"] = matched_text
                else:
                    channel_evidence["source"] = "legacy_combined_text"
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

    if _defer_result_preferences:
        eligible = ordered
    else:
        eligible = []
        dedup_candidates: list[dict[str, Any]] = []
        requested_junk = _requested_junk_categories(query)
        for candidate in ordered:
            row = candidate["row"]
            if _is_unrequested_junk(row, query, requested_junk):
                continue
            if _is_duplicate(row, dedup_candidates):
                continue
            eligible.append(candidate)
            dedup_candidates.append(row)

    # A selected movie is an explicit relevance constraint, so film balancing
    # is disabled. Unscoped browsing gets a page-wise soft preference whose
    # relevance backfill can never reduce the number of available results.
    if _defer_result_preferences:
        apply_film_diversity = False
    elif apply_film_diversity is None:
        apply_film_diversity = not bool(scoped_film_ids)
    ranked_candidates = (
        _progressive_film_diversity(
            eligible,
            result_limit=result_limit,
            page_size=int(config.retrieval.diversity.page_size),
            per_film_target=int(
                config.retrieval.diversity.film_results_per_page_target
            ),
        )
        if apply_film_diversity
        else eligible[:result_limit]
    )

    selected: list[dict[str, Any]] = []
    for candidate in ranked_candidates:
        row = candidate["row"]
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
            "keyframe_index": keyframe_index,
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
        matched_text = row.get("_matched_text")
        if isinstance(matched_text, dict):
            result["matched_text_view"] = matched_text.get("view")
            result["matched_text"] = matched_text.get("text")
        selected.append(result)

    return selected


def _clause_results_from_rows(
    rows: Iterable[dict[str, Any]],
    *,
    channel: str,
    mode: str,
    result_limit: int,
) -> list[dict[str, Any]]:
    """Format one facet adapter's ranked unit rows as source-backed results."""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        unit_id = str(row.get("unit_id") or row.get("shot_id") or "")
        if not unit_id or unit_id in seen:
            continue
        seen.add(unit_id)
        shot_id = str(row.get("shot_id") or unit_id)
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
        try:
            distance = float(row.get("_distance", 1.0))
        except (TypeError, ValueError):
            distance = 1.0
        channel_evidence = _channel_debug(
            len(results) + 1,
            1.0 - distance,
            distance,
        )
        if isinstance(matched_frame, dict):
            channel_evidence["source"] = "frame"
            channel_evidence["matched_frame"] = dict(matched_frame)
        matched_text = row.get("_matched_text")
        if isinstance(matched_text, dict):
            channel_evidence["source"] = str(
                matched_text.get("view") or "semantic_text"
            )
            channel_evidence["matched_text"] = dict(matched_text)
        result: dict[str, Any] = {
            "unit_id": unit_id,
            "film_id": row["film_id"],
            "t_start": row["t_start"],
            "t_end": row["t_end"],
            "caption": row["caption"],
            "keyframe_url": keyframe_url,
            "keyframe_index": keyframe_index,
            "preview_url": f"/media/preview/{shot_id}",
            "rank": len(results) + 1,
            "debug": {
                "mode": mode,
                "final_score": 1.0 - distance,
                "channels": {channel: channel_evidence},
            },
        }
        if isinstance(matched_frame, dict):
            result["matched_frame_url"] = keyframe_url
            result["matched_frame_index"] = keyframe_index
            result["matched_frame_timestamp"] = matched_frame.get("timestamp")
        if isinstance(matched_text, dict):
            result["matched_text_view"] = matched_text.get("view")
            result["matched_text"] = matched_text.get("text")
        results.append(result)
        if len(results) >= result_limit:
            break
    return results


def search_semantic_views(
    query: str,
    views: Iterable[str],
    db: lancedb.DBConnection,
    config: Config,
    *,
    film_ids: Iterable[str] | None = None,
    result_limit: int | None = None,
) -> list[dict[str, Any]]:
    """Search only explicitly selected semantic-text evidence views.

    Unlike broad :func:`search`, this operation cannot safely fall back to the
    legacy combined text vector because that would erase the requested facet
    boundary.
    """
    candidate_limit, resolved_result_limit = _validated_search_limits(
        config,
        result_limit,
    )
    requested_views = _validated_semantic_views(views)
    scoped_film_ids = _normalise_film_ids(film_ids)
    profile = _ready_text_profile(config, db)
    if profile is None:
        raise SemanticTextProfileUnavailable(
            "Facet search requires the complete semantic-text profile; "
            "general search remains available"
        )
    try:
        rows = _semantic_text_search_rows(
            embed_semantic_query(query, config),
            db,
            db.open_table("units"),
            profile,
            scoped_film_ids,
            candidate_limit=candidate_limit,
            allowed_views=requested_views,
        )
    except Exception as exc:
        raise SemanticTextProfileUnavailable(
            "Facet search could not use the active semantic-text profile; "
            "general search remains available"
        ) from exc
    return _clause_results_from_rows(
        rows,
        channel="txt",
        mode="semantic_view",
        result_limit=resolved_result_limit,
    )


def search_look_by_vector(
    vector: np.ndarray,
    db: lancedb.DBConnection,
    config: Config,
    *,
    film_ids: Iterable[str] | None = None,
    result_limit: int | None = None,
) -> list[dict[str, Any]]:
    """Search global frame appearance without spatial-grid reranking."""
    candidate_limit, resolved_result_limit = _validated_search_limits(
        config,
        result_limit,
    )
    require_visual_encoder_profile(db, config)
    scoped_film_ids = _normalise_film_ids(film_ids)
    rows = _frame_search_rows(
        np.asarray(vector, dtype=np.float32),
        db,
        db.open_table("units"),
        scoped_film_ids,
        candidate_limit=candidate_limit,
    )
    return _clause_results_from_rows(
        rows,
        channel="img",
        mode="global_look",
        result_limit=resolved_result_limit,
    )


def search_look_by_text(
    query: str,
    db: lancedb.DBConnection,
    config: Config,
    *,
    film_ids: Iterable[str] | None = None,
    result_limit: int | None = None,
) -> list[dict[str, Any]]:
    """Search frames through the visual encoder's paired text tower only."""
    require_visual_encoder_profile(db, config)
    return search_look_by_vector(
        embed_text([query], config)[0],
        db,
        config,
        film_ids=film_ids,
        result_limit=result_limit,
    )


def apply_recipe_result_preferences(
    results: Iterable[dict[str, Any]],
    db: lancedb.DBConnection,
    config: Config,
    *,
    film_ids: Iterable[str] | None = None,
    requested_text: str = "",
    result_limit: int | None = None,
    apply_reference_temporal_spread: bool = False,
    apply_film_diversity: bool | None = None,
) -> list[dict[str, Any]]:
    """Apply normal junk, visual-dedup, and diversity preferences once."""
    _candidate_limit, resolved_result_limit = _validated_search_limits(
        config,
        result_limit,
    )
    ordered = list(results)
    unit_ids = tuple(
        dict.fromkeys(
            str(result.get("unit_id") or "")
            for result in ordered
            if str(result.get("unit_id") or "")
        )
    )
    unit_filter = _unit_filter(unit_ids)
    if unit_filter is None:
        return []
    scoped_film_ids = _normalise_film_ids(film_ids)
    rows = (
        db.open_table("units")
        .search()
        .select(_CANDIDATE_COLUMNS)
        .where(_representative_filter(scoped_film_ids) & unit_filter)
        .limit(len(unit_ids))
        .to_list()
    )
    units_by_id = {
        _row_id(row): row
        for row in _rows_in_film_scope(rows, scoped_film_ids)
        if _row_id(row)
    }
    requested_junk = _requested_junk_categories(requested_text)
    dedup_rows: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    for result in ordered:
        unit_id = str(result.get("unit_id") or "")
        row = units_by_id.get(unit_id)
        if row is None or _is_unrequested_junk(
            row,
            requested_text,
            requested_junk,
        ):
            continue
        if _is_duplicate(row, dedup_rows):
            continue
        dedup_rows.append(row)
        eligible.append(result)

    if apply_reference_temporal_spread:
        return _reapply_reference_result_preferences(
            eligible,
            config,
            scoped_film_ids=scoped_film_ids,
            result_limit=resolved_result_limit,
            apply_film_diversity=apply_film_diversity,
        )
    if apply_film_diversity is None:
        apply_film_diversity = not bool(scoped_film_ids)
    selected = (
        [
            candidate["row"]
            for candidate in _progressive_film_diversity(
                [{"row": result} for result in eligible],
                result_limit=resolved_result_limit,
                page_size=int(config.retrieval.diversity.page_size),
                per_film_target=int(
                    config.retrieval.diversity.film_results_per_page_target
                ),
            )
        ]
        if apply_film_diversity
        else eligible[:resolved_result_limit]
    )
    for rank, result in enumerate(selected, start=1):
        result["rank"] = rank
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
    *,
    candidate_limit: int = _CANDIDATE_LIMIT,
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
    frame_rows = _stable_vector_ranking(
        _rows_in_film_scope(
            frame_query.limit(candidate_limit * 3).to_list(),
            film_ids,
        ),
        candidate_limit=candidate_limit * 3,
    )
    if not frame_rows:
        return []

    valid_rows: list[dict[str, Any]] = []
    candidate_images: list[Image.Image] = []
    spatial_shortlist_limit = min(
        candidate_limit,
        _REFERENCE_SPATIAL_CANDIDATE_LIMIT,
    )
    seen_units: set[str] = set()
    for row in frame_rows:
        unit_id = str(row.get("unit_id") or row.get("shot_id") or "")
        if not unit_id or unit_id in seen_units:
            continue
        if len(valid_rows) < spatial_shortlist_limit:
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
        if len(valid_rows) >= candidate_limit:
            break

    if not valid_rows:
        return []

    spatial_scores: np.ndarray | None = None
    if query_spatial is not None and candidate_images:
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
            range(len(spatial_scores)),
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
            if spatial_scores is not None and index < len(spatial_scores)
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

    score_key = lambda row: (  # noqa: E731 - shared deterministic key
        -float(row["_reference_score"]),
        str(row.get("frame_id") or ""),
    )
    if spatial_scores is None:
        valid_rows.sort(key=score_key)
        return valid_rows

    # Scores that include spatial evidence are not calibrated against the
    # semantic-only tail. Keep the spatial shortlist ahead of its backfill so
    # missing spatial evidence can never become an accidental ranking bonus.
    spatial_count = min(len(spatial_scores), len(valid_rows))
    spatial_shortlist = sorted(valid_rows[:spatial_count], key=score_key)
    semantic_backfill = sorted(
        valid_rows[spatial_count:],
        key=lambda row: (
            -float(row["_semantic_score"]),
            str(row.get("frame_id") or ""),
        ),
    )
    return [*spatial_shortlist, *semantic_backfill]


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


def _search_by_image_only(
    image: Image.Image,
    db: lancedb.DBConnection,
    config: Config,
    *,
    film_ids: Iterable[str] | None = None,
    exclude_unit_id: str | None = None,
    result_limit: int | None = None,
    requested_text: str = "",
    deduplicate_visual: bool = True,
    apply_film_diversity: bool | None = None,
    _defer_result_preferences: bool = False,
) -> list[dict]:
    """Find shots with similar content in similar normalized screen positions.

    PE Core first supplies a broad semantic candidate set from the existing
    frame vectors.  Its final learned patch grid then reranks those candidates
    by corresponding screen cells.  This is a composition/layout prototype;
    it does not claim skeleton-level pose or temporal motion understanding.
    """
    candidate_limit, result_limit = _validated_search_limits(
        config,
        result_limit,
    )
    scoped_film_ids = _normalise_film_ids(film_ids)
    if _defer_result_preferences:
        apply_film_diversity = False
    elif apply_film_diversity is None:
        apply_film_diversity = not bool(scoped_film_ids)
    frame_rows = _reference_frame_candidates(
        image.convert("RGB"),
        db,
        config,
        scoped_film_ids,
        candidate_limit=candidate_limit,
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

    seen_units: set[str] = set()
    dedup_candidates: list[dict[str, Any]] = []
    eligible_candidates: list[dict[str, Any]] = []
    requested_junk = (
        set()
        if _defer_result_preferences
        else _requested_junk_categories(requested_text)
    )
    for frame in eligible_frame_rows:
        unit_id = str(frame.get("unit_id") or frame.get("shot_id") or "")
        if (
            not unit_id
            or unit_id in seen_units
            or unit_id not in units_by_id
        ):
            continue
        row = units_by_id[unit_id]
        if not _defer_result_preferences and _is_unrequested_junk(
            row,
            requested_text,
            requested_junk,
        ):
            continue
        if (
            not _defer_result_preferences
            and deduplicate_visual
            and _is_duplicate(row, dedup_candidates)
        ):
            continue
        seen_units.add(unit_id)
        if not _defer_result_preferences:
            dedup_candidates.append(row)
        eligible_candidates.append({"row": row, "frame": frame})

    # Preserve the existing soft temporal spread first, then apply the same
    # film-level page preference as text search. Both stages only reorder
    # eligible rows; neither can discard a relevant candidate.
    temporally_preferred: list[dict[str, Any]] = []
    temporally_deferred: list[dict[str, Any]] = []
    selected_frames: list[dict[str, Any]] = []
    for candidate in eligible_candidates:
        frame = candidate["frame"]
        if (
            not _defer_result_preferences
            and _is_reference_temporal_duplicate(frame, selected_frames)
        ):
            temporally_deferred.append(candidate)
            continue
        temporally_preferred.append(candidate)
        selected_frames.append(frame)
    temporal_order = [*temporally_preferred, *temporally_deferred]
    ranked_candidates = (
        _progressive_film_diversity(
            temporal_order,
            result_limit=result_limit,
            page_size=int(config.retrieval.diversity.page_size),
            per_film_target=int(
                config.retrieval.diversity.film_results_per_page_target
            ),
        )
        if apply_film_diversity
        else temporal_order[:result_limit]
    )

    results: list[dict] = []
    for candidate in ranked_candidates:
        row = candidate["row"]
        frame = candidate["frame"]
        unit_id = str(row.get("unit_id") or row.get("shot_id") or "")
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
                "keyframe_index": keyframe_index,
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

    return results


def _fuse_reference_and_text_results(
    reference_results: Iterable[dict[str, Any]],
    text_results: Iterable[dict[str, Any]],
    *,
    result_limit: int,
    exclude_unit_id: str | None = None,
) -> list[dict[str, Any]]:
    """Rerank reference candidates with independent text evidence.

    The reference shortlist is mandatory: text-only candidates cannot enter a
    composition-constrained query.  Equal-weight RRF promotes agreement while
    preserving reference order when no candidates have text support.  The
    reference result remains the display source, including its exact frame.
    """
    normalized_exclusion = str(exclude_unit_id or "").strip()
    text_by_unit: dict[str, tuple[int, dict[str, Any]]] = {}
    for rank, result in enumerate(text_results, start=1):
        unit_id = str(result.get("unit_id") or "")
        if unit_id and unit_id not in text_by_unit:
            text_by_unit[unit_id] = (rank, result)

    candidates: list[dict[str, Any]] = []
    seen_reference: set[str] = set()
    for reference_rank, reference_result in enumerate(
        reference_results,
        start=1,
    ):
        unit_id = str(reference_result.get("unit_id") or "")
        if (
            not unit_id
            or unit_id == normalized_exclusion
            or unit_id in seen_reference
        ):
            continue
        seen_reference.add(unit_id)
        score = _REFERENCE_QUERY_WEIGHT / (_RRF_K + reference_rank)
        query_ranks = {"reference": reference_rank}
        text_result: dict[str, Any] | None = None
        text_match = text_by_unit.get(unit_id)
        if text_match is not None:
            text_rank, text_result = text_match
            score += _TEXT_CONSTRAINT_WEIGHT / (_RRF_K + text_rank)
            query_ranks["text"] = text_rank
        candidates.append(
            {
                "reference": reference_result,
                "text": text_result,
                "score": score,
                "query_ranks": query_ranks,
            }
        )

    ordered = sorted(
        candidates,
        key=lambda candidate: (
            -float(candidate["score"]),
            int(candidate["query_ranks"]["reference"]),
        ),
    )

    fused_results: list[dict[str, Any]] = []
    for candidate in ordered[:result_limit]:
        reference_result = candidate["reference"]
        text_result = candidate["text"]
        base = dict(reference_result)
        reference_debug = reference_result.get("debug", {})
        text_debug = (
            text_result.get("debug", {})
            if isinstance(text_result, dict)
            else {}
        )
        base["debug"] = {
            "mode": "reference_image_text",
            "final_score": float(candidate["score"]),
            # Keep the old top-level channel contract image-specific.  The
            # clause records below preserve identically named channels from
            # both searches without silently overwriting either one.
            "channels": dict(reference_debug.get("channels") or {}),
            "clauses": {
                "reference": reference_debug,
                **({"text": text_debug} if text_result is not None else {}),
            },
            "query_ranks": dict(candidate["query_ranks"]),
        }
        if isinstance(text_result, dict):
            for key in ("matched_text_view", "matched_text"):
                if key in text_result:
                    base[key] = text_result[key]
        base["rank"] = len(fused_results) + 1
        fused_results.append(base)
    return fused_results


def _reapply_reference_result_preferences(
    results: list[dict[str, Any]],
    config: Config,
    *,
    scoped_film_ids: tuple[str, ...],
    result_limit: int,
    apply_film_diversity: bool | None = None,
) -> list[dict[str, Any]]:
    """Restore temporal spread and unscoped film variety after text reranking."""
    temporally_preferred: list[dict[str, Any]] = []
    temporally_deferred: list[dict[str, Any]] = []
    selected_frames: list[dict[str, Any]] = []
    for result in results:
        frame = {
            "film_id": result.get("film_id"),
            "timestamp": result.get(
                "matched_frame_timestamp",
                result.get("t_start"),
            ),
        }
        if _is_reference_temporal_duplicate(frame, selected_frames):
            temporally_deferred.append(result)
            continue
        temporally_preferred.append(result)
        selected_frames.append(frame)
    temporal_order = [*temporally_preferred, *temporally_deferred]

    if apply_film_diversity is None:
        apply_film_diversity = not bool(scoped_film_ids)
    if apply_film_diversity:
        wrapped = [{"row": result} for result in temporal_order]
        selected = [
            candidate["row"]
            for candidate in _progressive_film_diversity(
                wrapped,
                result_limit=result_limit,
                page_size=int(config.retrieval.diversity.page_size),
                per_film_target=int(
                    config.retrieval.diversity.film_results_per_page_target
                ),
            )
        ]
    else:
        selected = temporal_order[:result_limit]
    for rank, result in enumerate(selected, start=1):
        result["rank"] = rank
    return selected


def search_by_image(
    image: Image.Image,
    db: lancedb.DBConnection,
    config: Config,
    *,
    film_ids: Iterable[str] | None = None,
    exclude_unit_id: str | None = None,
    exclude_film_id: str | None = None,
    result_limit: int | None = None,
    text_query: str | None = None,
    _defer_result_preferences: bool = False,
) -> list[dict]:
    """Search by composition, optionally constrained by a text clause.

    Image-only behavior remains the established PE spatial search.  When text
    is present, the normal hybrid text pipeline runs independently and the two
    ranked lists are fused.  This keeps both retrieval contracts replaceable
    and avoids inventing a prematurely unified multimodal vector.
    """
    candidate_limit, resolved_result_limit = _validated_search_limits(
        config,
        result_limit,
    )
    require_visual_encoder_profile(db, config)
    normalized_film_exclusion = str(exclude_film_id or "").strip()
    scope = resolve_reference_result_scope(
        db,
        film_ids,
        normalized_film_exclusion,
    )
    if scope is None:
        return []
    scoped_film_ids, apply_film_diversity = scope
    normalized_text = str(text_query or "").strip()
    candidate_window = (
        resolved_result_limit
        if not normalized_text
        else max(
            resolved_result_limit,
            min(candidate_limit, int(config.retrieval.max_result_limit)),
        )
    )
    reference_results = _search_by_image_only(
        image,
        db,
        config,
        film_ids=scoped_film_ids,
        exclude_unit_id=exclude_unit_id,
        result_limit=candidate_window,
        requested_text=normalized_text,
        # Preserve visually repeated moments until independent text evidence
        # has had a chance to distinguish or promote one of them.
        deduplicate_visual=not bool(normalized_text),
        apply_film_diversity=apply_film_diversity,
        **(
            {"_defer_result_preferences": True}
            if _defer_result_preferences
            else {}
        ),
    )
    if not normalized_text:
        return reference_results

    text_results = search(
        normalized_text,
        db,
        config,
        film_ids=scoped_film_ids,
        result_limit=candidate_window,
        **(
            {"_defer_result_preferences": True}
            if _defer_result_preferences
            else {}
        ),
    )
    fused_results = _fuse_reference_and_text_results(
        reference_results,
        text_results,
        result_limit=candidate_window,
        exclude_unit_id=exclude_unit_id,
    )
    if _defer_result_preferences:
        return fused_results[:resolved_result_limit]
    return _reapply_reference_result_preferences(
        fused_results,
        config,
        scoped_film_ids=scoped_film_ids,
        result_limit=resolved_result_limit,
        apply_film_diversity=apply_film_diversity,
    )
