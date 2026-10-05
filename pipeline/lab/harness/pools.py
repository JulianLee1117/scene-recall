"""Footage pools: per-act candidates from search v2, carrying the evidence assembly needs.

Each act's queries run through ordinary search (its ranking preset follows the
act's fame target). Candidates are merged per act by best rank, then joined
with compiled shot evidence: action peak, measured camera segments, main
subject at the shot's start and end, motion, hidden cuts, fame/craft and scene.
Shots without evidence stay usable with neutral values.

Shots that would read wrong under music never enter a pool: burned-in
captions, subtitles, credits or title cards (the unit's recognized on-screen
text, ``reads_as_text``), and shots the understanding pass describes as a
dissolve or superimposition that the cut detector did not split
(``describes_dissolve``).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, replace
from typing import Any, Callable

_RRF_K = 10
_EVIDENCE_COLUMNS = ["unit_id", "scene_id", "action", "characters", "peak_time", "camera", "camera_reliability",
                     "brightness", "saturation", "warmth",
                     "camera_segments", "motion_energy", "hidden_cuts", "dark_spans", "subject", "subject_size", "fame_library",
                     "craft", "iconic", "gem", "sharpness", "hero_time", "famous_line", "focus_start", "focus_end"]
# Detector classes that make a reliable main subject for continuity.
_SUBJECT_CLASSES = {"person", "car", "motorcycle", "bicycle", "bus", "truck", "train", "airplane", "boat", "horse",
                    "dog", "cat", "bird", "cow", "sheep", "elephant", "bear", "zebra", "giraffe"}
_MIN_SUBJECT_SIZE = 0.02
PRESET_FOR_FAME = {"anchor": "famous", "fresh": "gems", "any": "balanced"}
_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")   # kana, CJK ideographs, hangul
_DISSOLVE = re.compile(r"\b(dissolv\w*|cross-?fad\w*|superimpos\w*|double exposure|overlapping images|fades? (?:in)?to (?!black|darkness|white)\w|fades? from)", re.I)


@dataclass
class Candidate:
    unit_id: str
    film_id: str
    film_title: str
    t_start: float
    t_end: float
    caption: str = ""
    relevance: float = 0.0                  # best reciprocal rank in the act (1 = top), plus agreement
    rank: int = 0                           # best search rank within the act (1 = top)
    queries: list[str] = field(default_factory=list)
    action: str = ""
    characters: list[str] = field(default_factory=list)
    peak_time: float | None = None
    camera: str | None = None
    camera_reliability: float = 0.0
    camera_segments: list[tuple[float, float, str]] = field(default_factory=list)
    motion: float = 0.0                     # subject motion energy (measured), raw
    hidden_cuts: list[float] = field(default_factory=list)
    dark_spans: list[tuple[float, float]] = field(default_factory=list)   # near-black stretches (fades)
    focus: tuple[float, float] | None = None   # the stretch showing the same picture as the peak
    subject_start: tuple[float, float] | None = None
    subject_end: tuple[float, float] | None = None
    subject_size: float | None = None
    fame: float = 0.0
    craft: float = 0.0
    iconic: bool = False
    gem: bool = False
    sharpness: float | None = None
    hero_time: float | None = None
    scene_id: str | None = None
    famous_line: str | None = None
    vector: Any = field(default=None, repr=False, compare=False)   # unit-normalized image embedding
    aspect: float | None = None             # the film's display aspect ratio (width / height)
    grade: tuple[float, float, float] | None = None   # measured brightness, saturation, warmth
    cast_rank: int | None = None            # position in its act's cast (``cast``), None when not cast
    on_screen_text: str = ""                # recognized text in the picture (subtitles, titles, signs)
    cast_peak: bool = False                 # cast to land the act's biggest musical moment

    @property
    def duration(self) -> float:
        return self.t_end - self.t_start

    def camera_at(self, left: float, right: float) -> str | None:
        """Dominant measured camera label over a source window (reliable labels only)."""
        if self.camera_reliability < 0.5:
            return None
        overlap: dict[str, float] = {}
        for start, end, label in self.camera_segments:
            share = min(end, right) - max(start, left)
            if share > 0 and label not in ("unknown",):
                overlap[label] = overlap.get(label, 0.0) + share
        return max(overlap, key=overlap.get) if overlap else None

    def summary(self) -> dict[str, Any]:
        return {"unit_id": self.unit_id, "film": self.film_title, "t_start": round(self.t_start, 3),
                "t_end": round(self.t_end, 3), "action": self.action or self.caption[:160],
                "peak_time": self.peak_time, "camera": self.camera if self.camera_reliability >= 0.5 else None,
                "fame": round(self.fame, 2), "craft": round(self.craft, 2), "iconic": self.iconic, "gem": self.gem}


def reads_as_text(text: str) -> bool:
    """On-screen text that would read as a caption over music: subtitles, credits, title cards, long signs.

    Short signs (TAXI, EXIT, a neon word) stay; sentence-like or multi-word text, all-caps title lines of
    three or more words and runs of CJK characters do not.
    """
    text = (text or "").strip()
    if not text:
        return False
    if len(_CJK.findall(text)) >= 4:
        return True
    words = _WORD.findall(text)
    if not words:
        return False
    lower = any(word != word.upper() for word in words)
    sentence = len(words) >= 2 or text[-1:] in "!?.…"
    return (lower and sentence) or (len(words) >= 3 and not lower)


def describes_dissolve(candidate: "Candidate") -> bool:
    """The understanding pass saw a dissolve or superimposition inside this unit."""
    return bool(_DISSOLVE.search(candidate.action or ""))


def usable(candidate: "Candidate") -> bool:
    return not reads_as_text(candidate.on_screen_text) and not describes_dissolve(candidate)


def _center(box: Any) -> tuple[float, float] | None:
    if isinstance(box, (list, tuple)) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
        return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    return None


def _json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def apply_evidence(candidate: Candidate, row: dict[str, Any]) -> None:
    """Copy one compiled ``shot_evidence`` row onto a candidate."""
    candidate.scene_id = row.get("scene_id")
    candidate.action = row.get("action") or ""
    candidate.characters = [str(name) for name in _json(row.get("characters"), [])][:8]
    peak = row.get("peak_time")
    if isinstance(peak, (int, float)) and candidate.t_start <= peak <= candidate.t_end:
        candidate.peak_time = float(peak)
    candidate.camera = row.get("camera")
    candidate.camera_reliability = float(row.get("camera_reliability") or 0.0)
    candidate.camera_segments = [(float(a), float(b), str(label)) for a, b, label in _json(row.get("camera_segments"), [])
                                 if isinstance(a, (int, float)) and isinstance(b, (int, float))]
    candidate.motion = float(row.get("motion_energy") or 0.0)
    candidate.hidden_cuts = [float(t) for t in _json(row.get("hidden_cuts"), []) if isinstance(t, (int, float))]
    candidate.dark_spans = [(float(a), float(b)) for a, b in _json(row.get("dark_spans"), [])
                            if isinstance(a, (int, float)) and isinstance(b, (int, float)) and b > a]
    low, high = row.get("focus_start"), row.get("focus_end")
    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
        low, high = max(float(low), candidate.t_start), min(float(high), candidate.t_end)
        candidate.focus = (low, high) if high > low else None
    subject = _json(row.get("subject"), None)
    size = row.get("subject_size")
    if isinstance(subject, dict) and isinstance(size, (int, float)) and (
            (subject.get("class") in _SUBJECT_CLASSES and size >= _MIN_SUBJECT_SIZE) or size >= 0.08):
        candidate.subject_start = _center(subject.get("box_start")) or tuple(subject.get("center") or ()) or None
        candidate.subject_end = _center(subject.get("box_end")) or candidate.subject_start
        candidate.subject_size = float(size)
    candidate.fame = float(row.get("fame_library") or 0.0)
    candidate.craft = float(row.get("craft") or 0.0)
    candidate.iconic, candidate.gem = bool(row.get("iconic")), bool(row.get("gem"))
    candidate.sharpness = float(row["sharpness"]) if isinstance(row.get("sharpness"), (int, float)) else None
    hero = row.get("hero_time")
    candidate.hero_time = float(hero) if isinstance(hero, (int, float)) else None
    candidate.famous_line = row.get("famous_line")
    look = [row.get(key) for key in ("brightness", "saturation", "warmth")]
    if all(isinstance(value, (int, float)) for value in look):
        candidate.grade = (float(look[0]), float(look[1]), float(look[2]))


def load_evidence(db: Any, candidates: dict[str, Candidate]) -> int:
    """Attach compiled evidence to candidates in place; returns how many had any."""
    from pipeline.evidence.tables import SHOT_EVIDENCE
    from pipeline.index.writer import table_names
    from pipeline.search.retrieve import _any_of

    if not candidates or SHOT_EVIDENCE not in table_names(db):
        return 0
    table = db.open_table(SHOT_EVIDENCE)
    available = set(table.schema.names)
    columns = [column for column in _EVIDENCE_COLUMNS if column in available]   # older compiled tables lack new columns
    identities = list(candidates)
    found = 0
    for offset in range(0, len(identities), 256):
        batch = tuple(identities[offset:offset + 256])
        for row in table.search().where(_any_of("unit_id", batch)).select(columns).limit(len(batch) + 1).to_list():
            candidate = candidates.get(row["unit_id"])
            if candidate is not None:
                apply_evidence(candidate, row)
                found += 1
    return found


def load_vectors(db: Any, candidates: dict[str, Candidate]) -> int:
    """Attach unit-normalized image embeddings (visual variety between neighbouring shots) and on-screen text."""
    import numpy as np

    from pipeline.index.writer import table_names
    from pipeline.search.retrieve import _any_of

    if not candidates or "units" not in table_names(db):
        return 0
    table = db.open_table("units")
    if "img_vec" not in table.schema.names:
        return 0
    identities = list(candidates)
    columns = ["unit_id", "img_vec"] + (["on_screen_text"] if "on_screen_text" in table.schema.names else [])
    found = 0
    for offset in range(0, len(identities), 256):
        batch = tuple(identities[offset:offset + 256])
        for row in table.search().where(_any_of("unit_id", batch)).select(columns).limit(len(batch) + 1).to_list():
            candidate, vector = candidates.get(row["unit_id"]), row.get("img_vec")
            if candidate is not None:
                candidate.on_screen_text = str(row.get("on_screen_text") or "")
            if candidate is None or vector is None:
                continue
            array = np.asarray(vector, np.float32)
            norm = float(np.linalg.norm(array))
            if norm > 0:
                candidate.vector = array / norm
                found += 1
    return found


def gather(db: Any, config: Any, acts: list[dict[str, Any]], *, film_ids: list[str] | None = None,
           per_query: int = 48, exclude_units: set[str] | None = None,
           search: Callable[..., list[dict[str, Any]]] | None = None,
           progress: Callable[[str], None] = lambda _message: None, workers: int = 3) -> list[list[Candidate]]:
    """One candidate list per act, best relevance first, with evidence attached."""
    if search is None:
        from pipeline.search.retrieve import search as search_v2
        search = search_v2
    exclude = exclude_units or set()
    jobs = [(index, query, PRESET_FOR_FAME.get(str(act.get("fame") or "any"), "balanced"))
            for index, act in enumerate(acts) for query in act.get("queries") or []]
    # Acts split around flashes and holds share their section's queries: each search runs once.
    searches = list(dict.fromkeys((query, preset) for _index, query, preset in jobs))

    def run(key: tuple[str, str]) -> list[dict[str, Any]]:
        query, preset = key
        return search(query, db, config, film_ids=film_ids or None, result_limit=per_query, preset=preset)

    # Searches are independent; a few run at once (search is thread-safe, as the API serves in parallel).
    # Progress is reported from this thread (job callbacks may not be thread-safe), and results merge
    # in job order, so pools are identical to a sequential run.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    found: dict[tuple[str, str], list[dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(searches) or 1))) as pool:
        futures = {pool.submit(run, key): key for key in searches}
        for done, future in enumerate(as_completed(futures), start=1):
            found[futures[future]] = future.result()
            progress(f"Finding footage: {done} of {len(searches)} searches")
    results = [found[(query, preset)] for _index, query, preset in jobs]
    everything: dict[str, Candidate] = {}
    relevance: list[dict[str, list[float]]] = [{} for _ in acts]
    for (index, query, _preset), rows in zip(jobs, results):
        scores = relevance[index]
        for rank, row in enumerate(rows, start=1):
            unit_id = str(row.get("unit_id") or "")
            start, end = row.get("t_start"), row.get("t_end")
            if not unit_id or unit_id in exclude or not all(
                    isinstance(v, (int, float)) and math.isfinite(v) for v in (start, end)) or end <= start:
                continue
            candidate = everything.get(unit_id)
            if candidate is None:
                candidate = everything[unit_id] = Candidate(
                    unit_id=unit_id, film_id=str(row["film_id"]), film_title=str(row.get("film_title") or row["film_id"]),
                    t_start=float(start), t_end=float(end), caption=str(row.get("caption") or ""))
            scores.setdefault(unit_id, []).append((_RRF_K + 1) / (_RRF_K + rank))
            if query not in candidate.queries:
                candidate.queries.append(query)
    hydrate(db, everything)
    unusable = {unit_id for unit_id, candidate in everything.items() if not usable(candidate)}
    # Best rank, plus a little for each other query of the act that also found the shot.
    return [sorted((replace(everything[unit_id], relevance=max(values) + 0.15 * (sum(values) - max(values)),
                            rank=round((_RRF_K + 1) / max(values) - _RRF_K))
                    for unit_id, values in scores.items() if unit_id not in unusable),
                   key=lambda item: (-item.relevance, item.unit_id))
            for scores in relevance]


def hydrate(db: Any, candidates: dict[str, Candidate]) -> None:
    """Attach evidence, image embeddings, film titles and aspect ratios to candidates in place."""
    load_evidence(db, candidates)
    load_vectors(db, candidates)
    titles = _film_titles(db)
    aspects = film_aspects(db, {candidate.film_id for candidate in candidates.values()})
    for candidate in candidates.values():
        candidate.film_title = titles.get(candidate.film_id, candidate.film_title)
        candidate.aspect = aspects.get(candidate.film_id)


def placed_candidates(db: Any, clips: list[dict[str, Any]]) -> dict[str, Candidate]:
    """Candidates for clips already in an edit (neighbours for continuity), keyed by clip ID."""
    from pipeline.index.writer import table_names
    from pipeline.search.retrieve import _any_of

    units = {clip["unit_id"]: clip for clip in clips if clip.get("unit_id")}
    if not units or "units" not in table_names(db):
        return {}
    rows = db.open_table("units").search().where(_any_of("unit_id", tuple(units))).select(
        ["unit_id", "film_id", "t_start", "t_end", "caption"]).limit(len(units) + 1).to_list()
    by_unit = {row["unit_id"]: Candidate(unit_id=row["unit_id"], film_id=str(row["film_id"]), film_title=str(row["film_id"]),
                                         t_start=float(row["t_start"]), t_end=float(row["t_end"]),
                                         caption=str(row.get("caption") or ""))
               for row in rows}
    load_evidence(db, by_unit)
    load_vectors(db, by_unit)
    titles = _film_titles(db)
    aspects = film_aspects(db, {candidate.film_id for candidate in by_unit.values()})
    for candidate in by_unit.values():
        candidate.film_title = titles.get(candidate.film_id, candidate.film_title)
        candidate.aspect = aspects.get(candidate.film_id)
    return {clip["id"]: by_unit[clip["unit_id"]] for clip in clips if clip.get("unit_id") in by_unit}


_ASPECTS: dict[tuple[str, int, int], float | None] = {}


def film_aspects(db: Any, film_ids: set[str]) -> dict[str, float]:
    """Display aspect ratio per film from its video stream (probed once per file version)."""
    import subprocess

    try:
        from pipeline.evidence.library import list_films
        films = {film.film_id: film for film in list_films(db) if film.film_id in film_ids}
    except Exception:  # noqa: BLE001 - optional continuity evidence
        return {}
    result: dict[str, float] = {}
    for film_id, film in films.items():
        try:
            stat = film.path.stat()
        except OSError:
            continue
        key = (str(film.path), int(stat.st_size), int(stat.st_mtime))
        if key not in _ASPECTS:
            probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                    "stream=width,height,sample_aspect_ratio", "-of", "csv=p=0", str(film.path)],
                                   capture_output=True, text=True, check=False)
            aspect = None
            try:
                width, height, *rest = probe.stdout.strip().splitlines()[0].split(",")
                sar = rest[0] if rest and ":" in rest[0] else "1:1"
                num, den = (int(value) for value in sar.split(":"))
                aspect = int(width) * (num / den if num and den else 1.0) / int(height)
            except (IndexError, ValueError, ZeroDivisionError):
                aspect = None
            _ASPECTS[key] = aspect
        if _ASPECTS[key]:
            result[film_id] = _ASPECTS[key]
    return result


def _film_titles(db: Any) -> dict[str, str]:
    try:
        from pipeline.evidence.library import list_films
        return {film.film_id: film.title for film in list_films(db)}
    except Exception:  # noqa: BLE001 - titles are presentation only
        return {}
