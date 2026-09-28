"""Quote channel: find remembered dialogue in subtitle lines, with exact line times.

Lines come from the compiled ``dialogue_lines`` table (see
``pipeline.evidence.compile``), indexed on normalized text with positions, no
stemming and stop words kept, because quotes are mostly stop words ("you
talkin' to me"). Candidates from phrase and term queries are re-scored in
Python by ordered token overlap against the line and its neighbours, so a quote
split across two subtitle cues still matches as one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from pipeline.evidence.tables import DIALOGUE_LINES
from pipeline.evidence.textnorm import ordered_match as score_window
from pipeline.evidence.textnorm import tokens as normalized_tokens

MIN_QUERY_TOKENS = 2
MIN_SCORE = 0.5            # weaker matches are incidental word overlap, not the remembered line
STRONG_SCORE = 0.8         # the query is very likely a quote
_LINE_COLUMNS = ["line_id", "film_id", "unit_id", "t_start", "t_end", "text", "norm"]


@dataclass(frozen=True)
class LineHit:
    film_id: str
    unit_id: str
    t_start: float
    t_end: float
    text: str
    score: float           # 0..1 ordered overlap weighted by how much of the window is the quote


def _film_filter(film_ids: tuple[str, ...]) -> str | None:
    if not film_ids:
        return None
    quoted = ", ".join("'" + film_id.replace("'", "''") + "'" for film_id in film_ids)
    return f"film_id IN ({quoted})"


def _line_index(line_id: str) -> tuple[str, int]:
    film_id, _, suffix = line_id.rpartition(":l")
    return film_id, int(suffix)


def find_lines(db: Any, query: str, *, film_ids: Iterable[str] = (), limit: int = 100) -> list[LineHit]:
    """Best-matching subtitle windows for *query*, strongest first (one per unit)."""
    from lancedb.query import FullTextOperator, MatchQuery, PhraseQuery

    from pipeline.index.writer import table_names

    query_tokens = normalized_tokens(query)
    if len(query_tokens) < MIN_QUERY_TOKENS or DIALOGUE_LINES not in table_names(db):
        return []
    table = db.open_table(DIALOGUE_LINES)
    scope = _film_filter(tuple(film_ids))
    text = " ".join(query_tokens)
    candidates: dict[str, dict[str, Any]] = {}
    for fts_query, depth in ((PhraseQuery(text, "norm", slop=1), limit),
                             (MatchQuery(text, "norm", operator=FullTextOperator.OR), limit * 3)):
        search = table.search(fts_query, query_type="fts").select(_LINE_COLUMNS)
        if scope:
            search = search.where(scope)
        try:
            rows = search.limit(depth).to_list()
        except (RuntimeError, ValueError):       # index missing or query unsupported: no quote evidence
            rows = []
        for row in rows:
            candidates.setdefault(row["line_id"], row)
    if not candidates:
        return []

    # Neighbouring cues let a quote that spans a subtitle break match as one window.
    neighbour_ids = set()
    for line_id in candidates:
        film_id, index = _line_index(line_id)
        neighbour_ids.update(f"{film_id}:l{index + offset:05d}" for offset in (-1, 1))
    neighbour_ids -= candidates.keys()
    lines = dict(candidates)
    if neighbour_ids:
        quoted = ", ".join("'" + line_id.replace("'", "''") + "'" for line_id in sorted(neighbour_ids))
        for row in table.search().select(_LINE_COLUMNS).where(f"line_id IN ({quoted})").limit(len(neighbour_ids)).to_list():
            lines[row["line_id"]] = row

    best: dict[str, LineHit] = {}
    for line_id, row in candidates.items():
        film_id, index = _line_index(line_id)
        before = lines.get(f"{film_id}:l{index - 1:05d}")
        after = lines.get(f"{film_id}:l{index + 1:05d}")
        windows = [[row]]
        if before is not None and float(row["t_start"]) - float(before["t_end"]) < 2.0:
            windows.append([before, row])
        if after is not None and float(after["t_start"]) - float(row["t_end"]) < 2.0:
            windows.append([row, after])
        for window in windows:
            score = score_window(query_tokens, [token for part in window for token in part["norm"].split()])
            if score < MIN_SCORE or not row.get("unit_id"):
                continue
            hit = LineHit(film_id=row["film_id"], unit_id=row["unit_id"], t_start=float(window[0]["t_start"]),
                          t_end=float(window[-1]["t_end"]), text=" ".join(part["text"] for part in window),
                          score=round(score, 4))
            current = best.get(hit.unit_id)
            if current is None or hit.score > current.score:
                best[hit.unit_id] = hit
    return sorted(best.values(), key=lambda hit: (-hit.score, hit.film_id, hit.t_start))[:limit]


def quote_strength(hits: list[LineHit]) -> float:
    """How strongly the query reads as a quote (0 = not at all)."""
    return hits[0].score if hits else 0.0
