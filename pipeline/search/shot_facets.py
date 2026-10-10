"""Shot facets for search filters: dialogue, shot size, color, camera, time, place, people.

Each facet maps a shot's stored evidence to one value, or to none when the
evidence is missing or unreliable; a shot with no value never passes a filter
on that facet. Values come from the annotation columns on ``units`` and the
measured columns of ``shot_evidence`` (ADR-0093), so nothing new is stored:
the index is derived in memory and cached per table version, like film
facets. Adding a facet is one entry in ``SHOT_FACETS``.

Filters run inside retrieval, not after it (ADR-0114): the request's
``UnitScope`` masks every resident vector channel before its top-k and is
checked again on every database path.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import threading
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np

from pipeline.evidence.tables import SHOT_EVIDENCE

Row = Mapping[str, Any]

# Below this colorfulness a shot reads as black and white, tinted monochrome
# included (8½, Andrei Rublev). Calibrated on the library: it catches 98% of
# shots in black-and-white films and 3% of shots in color films, mostly
# near-black fades.
MONOCHROME_COLORFULNESS = 0.02
# Measured camera movement below this reliability is left unknown.
CAMERA_RELIABILITY = 0.5
# A film whose shots almost never carry dialogue has no usable dialogue
# track (silent films, missing subtitles): "no dialogue" says nothing there.
SPOKEN_FILM_SHARE = 0.05


def _dialogue(unit: Row, _evidence: Row, spoken_film: bool) -> str | None:
    if not spoken_film:
        return None
    try:
        lines = json.loads(unit.get("dialogue") or "[]")
    except (TypeError, ValueError):
        return None
    return "spoken" if lines else "none"


def _size(unit: Row, _evidence: Row, _spoken: bool) -> str | None:
    return {"extreme_close_up": "close", "close_up": "close", "medium": "medium",
            "wide": "wide", "extreme_wide": "wide"}.get(str(unit.get("framing") or ""))


def _color(_unit: Row, evidence: Row, _spoken: bool) -> str | None:
    value = evidence.get("colorfulness")
    return None if value is None else ("bw" if value < MONOCHROME_COLORFULNESS else "color")


def _camera(_unit: Row, evidence: Row, _spoken: bool) -> str | None:
    label = str(evidence.get("camera") or "unknown")
    if label == "unknown" or (evidence.get("camera_reliability") or 0) < CAMERA_RELIABILITY:
        return None
    return "still" if label == "static" else "moving"


def _time(unit: Row, _evidence: Row, _spoken: bool) -> str | None:
    return {"day": "day", "dawn_dusk": "dusk", "night": "night"}.get(str(unit.get("time_of_day") or ""))


def _place(unit: Row, _evidence: Row, _spoken: bool) -> str | None:
    return {"interior": "interior", "exterior": "exterior"}.get(str(unit.get("setting") or ""))


def _people(_unit: Row, evidence: Row, _spoken: bool) -> str | None:
    """People in the shot: the detector's count, or, when it saw nobody, the characters the
    understanding pass named. The detector is COCO-trained and blind to drawn, stop-motion or
    non-human characters (Spirited Away: nobody in 86% of shots with named characters)."""
    value = evidence.get("people")
    if value is None or value < 0.5:
        try:
            named = json.loads(evidence.get("characters") or "[]")
        except (TypeError, ValueError):
            named = []
        if isinstance(named, list) and named:
            value = float(len(named))
    if value is None:
        return None
    return "none" if value < 0.5 else "one" if value < 1.5 else "two" if value < 2.5 else "group"


@dataclass(frozen=True)
class ShotFacet:
    key: str
    label: str
    values: tuple[tuple[str, str], ...]   # (value, label), in menu order
    derive: Callable[[Row, Row, bool], str | None]


SHOT_FACETS: tuple[ShotFacet, ...] = (
    ShotFacet("dialogue", "Dialogue", (("none", "None"), ("spoken", "Spoken")), _dialogue),
    ShotFacet("size", "Size", (("close", "Close"), ("medium", "Medium"), ("wide", "Wide")), _size),
    ShotFacet("people", "People", (("none", "None"), ("one", "One"), ("two", "Two"), ("group", "Group")), _people),
    ShotFacet("camera", "Camera", (("still", "Still"), ("moving", "Moving")), _camera),
    ShotFacet("color", "Color", (("color", "Color"), ("bw", "B&W")), _color),
    ShotFacet("time", "Time", (("day", "Day"), ("dusk", "Dusk"), ("night", "Night")), _time),
    ShotFacet("place", "Place", (("interior", "Inside"), ("exterior", "Outside")), _place),
)
_BY_KEY = {facet.key: facet for facet in SHOT_FACETS}

_UNIT_COLUMNS = ["unit_id", "film_id", "framing", "setting", "time_of_day", "dialogue"]
_EVIDENCE_COLUMNS = ["unit_id", "colorfulness", "camera", "camera_reliability", "people", "characters"]


def validate_shot_filters(filters: Mapping[str, Sequence[str]]) -> dict[str, tuple[str, ...]]:
    """Known facets and values only, empty facets dropped; raises ValueError otherwise."""
    cleaned: dict[str, tuple[str, ...]] = {}
    for key, values in filters.items():
        facet = _BY_KEY.get(key)
        if facet is None:
            raise ValueError(f"unknown shot filter {key!r}")
        allowed = {value for value, _ in facet.values}
        chosen = tuple(dict.fromkeys(values))
        unknown = [value for value in chosen if value not in allowed]
        if unknown:
            raise ValueError(f"unknown {key} value {unknown[0]!r}")
        if chosen:
            cleaned[key] = chosen
    return cleaned


@dataclass
class ShotFacetIndex:
    """Each representative shot's facet values as small integer codes (-1: unknown)."""

    unit_ids: list[str]
    position: dict[str, int]
    codes: dict[str, np.ndarray]
    _positions: dict[tuple, np.ndarray]

    def passing(self, filters: Mapping[str, Sequence[str]]) -> np.ndarray:
        """Boolean per shot: within each facet any chosen value, across facets all."""
        mask = np.ones(len(self.unit_ids), dtype=bool)
        for key, values in filters.items():
            facet = _BY_KEY[key]
            codes = [index for index, (value, _) in enumerate(facet.values) if value in values]
            mask &= np.isin(self.codes[key], codes)
        return mask

    def positions_for(self, unit_ids: Sequence[str], key: tuple) -> np.ndarray:
        """Index position of each given unit (-1 when absent), cached per caller's table version."""
        cached = self._positions.get(key)
        if cached is None or len(cached) != len(unit_ids):
            cached = np.fromiter((self.position.get(unit_id, -1) for unit_id in unit_ids), dtype=np.int64,
                                 count=len(unit_ids))
            self._positions[key] = cached
        return cached

    def counts(self) -> dict[str, dict[str, int]]:
        return {facet.key: {value: int(np.count_nonzero(self.codes[facet.key] == index))
                            for index, (value, _) in enumerate(facet.values)} for facet in SHOT_FACETS}


@dataclass(frozen=True)
class UnitScope:
    """The shots a search may return: those passing every chosen shot filter."""

    index: ShotFacetIndex
    mask: np.ndarray
    filters: tuple[tuple[str, tuple[str, ...]], ...]

    @property
    def fraction(self) -> float:
        return float(self.mask.mean()) if len(self.mask) else 0.0

    def allows(self, unit_id: str) -> bool:
        position = self.index.position.get(unit_id)
        return position is not None and bool(self.mask[position])

    def mask_for(self, unit_ids: Sequence[str], key: tuple) -> np.ndarray:
        """Boolean per given unit, aligned to *unit_ids* (a resident matrix's order)."""
        positions = self.index.positions_for(unit_ids, key)
        allowed = np.zeros(len(positions), dtype=bool)
        present = positions >= 0
        allowed[present] = self.mask[positions[present]]
        return allowed

    def depth(self, limit: int, *, cap: int = 25) -> int:
        """How deep a channel that can only filter after ranking must read to keep *limit* in scope."""
        return int(min(limit * cap, limit / max(self.fraction, 1 / cap)))


_LOCK = threading.Lock()
_CACHE: dict[str, tuple[tuple[int, int], ShotFacetIndex]] = {}


def shot_facet_index(db: Any) -> ShotFacetIndex | None:
    """The library's shot facets, rebuilt when ``units`` or ``shot_evidence`` changes."""
    from pipeline.index.writer import table_names

    names = table_names(db)
    if "units" not in names:
        return None
    units_table = db.open_table("units")
    evidence_table = db.open_table(SHOT_EVIDENCE) if SHOT_EVIDENCE in names else None
    versions = (int(units_table.version), int(evidence_table.version) if evidence_table is not None else -1)
    key = str(getattr(db, "uri", ""))
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None and cached[0] == versions:
            return cached[1]
    units = units_table.to_lance().to_table(columns=_UNIT_COLUMNS, filter="is_representative = true").to_pylist()
    evidence: dict[str, Row] = {}
    if evidence_table is not None:
        for row in evidence_table.to_lance().to_table(columns=_EVIDENCE_COLUMNS).to_pylist():
            evidence[str(row["unit_id"])] = row
    index = _build_index(units, evidence)
    with _LOCK:
        _CACHE[key] = (versions, index)
    return index


def _build_index(units: Iterable[Row], evidence: Mapping[str, Row]) -> ShotFacetIndex:
    units = list(units)
    shots: dict[str, int] = {}
    spoken: dict[str, int] = {}
    for unit in units:
        film = str(unit.get("film_id") or "")
        shots[film] = shots.get(film, 0) + 1
        if (unit.get("dialogue") or "[]") not in ("[]", ""):
            spoken[film] = spoken.get(film, 0) + 1
    spoken_film = {film: spoken.get(film, 0) / count >= SPOKEN_FILM_SHARE for film, count in shots.items()}
    unit_ids = [str(unit["unit_id"]) for unit in units]
    codes = {facet.key: np.full(len(units), -1, dtype=np.int8) for facet in SHOT_FACETS}
    lookup = {facet.key: {value: index for index, (value, _) in enumerate(facet.values)} for facet in SHOT_FACETS}
    for position, unit in enumerate(units):
        shot_evidence = evidence.get(unit_ids[position], {})
        has_dialogue_track = spoken_film.get(str(unit.get("film_id") or ""), False)
        for facet in SHOT_FACETS:
            value = facet.derive(unit, shot_evidence, has_dialogue_track)
            if value is not None:
                codes[facet.key][position] = lookup[facet.key][value]
    return ShotFacetIndex(unit_ids, {unit_id: i for i, unit_id in enumerate(unit_ids)}, codes, {})


def unit_scope(db: Any, filters: Mapping[str, Sequence[str]] | None) -> UnitScope | None:
    """The scope for validated *filters*, or None when there are none."""
    if not filters:
        return None
    index = shot_facet_index(db)
    if index is None:
        raise ValueError("Shot filters are unavailable until the library is indexed")
    ordered = tuple((facet.key, tuple(filters[facet.key])) for facet in SHOT_FACETS if filters.get(facet.key))
    return UnitScope(index, index.passing(dict(ordered)), ordered)


def shot_facet_vocabulary(db: Any) -> list[dict[str, Any]]:
    """Facets, values and library-wide shot counts, in menu order, for the Filter menu."""
    index = shot_facet_index(db)
    counts = index.counts() if index is not None else {}
    return [
        {"key": facet.key, "label": facet.label,
         "values": [{"value": value, "label": label, "count": counts.get(facet.key, {}).get(value, 0)}
                    for value, label in facet.values]}
        for facet in SHOT_FACETS
    ]
