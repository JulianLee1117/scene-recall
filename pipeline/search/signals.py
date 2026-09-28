"""Deterministic query signals: named characters, actors, films, shot scale, camera, time and colour.

Embeddings match descriptions loosely; names and measured facts are exact. A
query that names a character ("trinity kicks the cop"), an actor, a film, a
shot scale ("close-up"), a camera move ("slow push in"), a time of day or a
colour lifts candidates whose evidence satisfies it, and slightly lowers those
whose known evidence contradicts it. Signals only reorder the retrieved pool,
like priors, and unknown evidence is neutral.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
import threading
from typing import Any, Callable, TypeVar

from pipeline.evidence.tables import FILM_META, SHOT_EVIDENCE
from pipeline.evidence.textnorm import normalize_line

T = TypeVar("T")

_SCALE = [
    ("extreme close up", "extreme_close_up"), ("extreme closeup", "extreme_close_up"), ("macro shot", "extreme_close_up"),
    ("close up", "close_up"), ("closeup", "close_up"), ("medium shot", "medium"), ("mid shot", "medium"),
    ("extreme wide", "extreme_wide"), ("establishing shot", "extreme_wide"), ("wide shot", "wide"), ("long shot", "wide"),
]
_CAMERA = [
    ("push in", {"push_in"}), ("dolly in", {"push_in"}), ("zoom in", {"push_in"}), ("pull out", {"pull_out"}),
    ("pull back", {"pull_out"}), ("dolly out", {"pull_out"}), ("zoom out", {"pull_out"}), ("whip pan", {"pan_left", "pan_right"}),
    ("pan left", {"pan_left"}), ("pan right", {"pan_right"}), ("panning", {"pan_left", "pan_right"}),
    ("tilt up", {"tilt_up"}), ("tilt down", {"tilt_down"}), ("tracking shot", {"pan_left", "pan_right", "diagonal"}),
    ("handheld", {"handheld"}), ("shaky cam", {"handheld"}), ("static shot", {"static"}), ("locked off", {"static"}),
]
_TIME = [("at night", "night"), ("nighttime", "night"), ("night", "night"), ("daylight", "day"), ("daytime", "day"),
         ("sunset", "dawn_dusk"), ("sunrise", "dawn_dusk"), ("dusk", "dawn_dusk"), ("dawn", "dawn_dusk"),
         ("golden hour", "dawn_dusk")]
_COLOURS = ("red", "blue", "green", "yellow", "orange", "purple", "pink", "teal", "amber", "gold", "white", "neon")
_GENERIC_ROLES = frozenset("""man woman boy girl child kid baby father mother dad mom son daughter brother sister husband
wife friend stranger waiter waitress driver police policeman officer cop guard soldier doctor nurse agent clerk
colleague staff hotel bartender customer crowd people person student teacher priest judge lawyer boss worker
passenger neighbor neighbour landlady landlord maid servant old young mr mrs ms dr the a an of and""".split())


@dataclass
class Signals:
    characters: set[tuple[str, str]] = field(default_factory=set)   # (film_id, normalized character)
    films: set[str] = field(default_factory=set)
    scale: str | None = None
    camera: set[str] = field(default_factory=set)
    time: str | None = None
    colours: set[str] = field(default_factory=set)
    black_and_white: bool = False

    def __bool__(self) -> bool:
        return bool(self.characters or self.films or self.scale or self.camera or self.time or self.colours
                    or self.black_and_white)


@dataclass
class Vocabulary:
    key: tuple
    phrases: dict[str, list[tuple[str, str, str]]]   # phrase -> [(kind, film_id, value)]
    longest: int


_LOCK = threading.Lock()
_VOCABULARY: dict[str, Vocabulary] = {}


def _phrases_for_name(name: str, common: set[str], *, parts: bool = True) -> list[str]:
    """The full name, plus distinctive parts ("bickle", "morpheus") that are not everyday words."""
    tokens = normalize_line(name).split()
    if not tokens or all(token in _GENERIC_ROLES for token in tokens):
        return []
    phrases = [" ".join(tokens)]
    if parts and len(tokens) > 1:
        phrases += [token for token in tokens if len(token) >= 4 and token not in _GENERIC_ROLES and token not in common]
    return list(dict.fromkeys(phrases))


def _common_words(db: Any) -> set[str]:
    """Words frequent in the library's visual captions: never matched as a name part."""
    from collections import Counter

    counts: Counter[str] = Counter()
    rows = db.open_table("units").search().select(["caption"]).limit(None).to_list()
    for row in rows:
        counts.update(set(normalize_line(row.get("caption") or "").split()))
    threshold = max(20, len(rows) // 2000)
    return {word for word, count in counts.items() if count >= threshold}


def vocabulary(db: Any) -> Vocabulary | None:
    """Names from film metadata (titles, cast) and understood characters, cached per table versions."""
    from pipeline.index.writer import table_names

    names = table_names(db)
    if FILM_META not in names:
        return None
    versions = tuple(int(db.open_table(name).version) if name in names else -1
                     for name in (FILM_META, SHOT_EVIDENCE, "units"))
    key = (str(getattr(db, "uri", "")), *versions)
    with _LOCK:
        cached = _VOCABULARY.get(key[0])
        if cached is not None and cached.key == key:
            return cached
    phrases: dict[str, list[tuple[str, str, str]]] = {}
    common = _common_words(db)

    def add(phrase: str, kind: str, film_id: str, value: str) -> None:
        entry = (kind, film_id, value)
        bucket = phrases.setdefault(phrase, [])
        if entry not in bucket:
            bucket.append(entry)

    for row in db.open_table(FILM_META).search().select(["film_id", "name", "cast"]).limit(None).to_list():
        film_id = row["film_id"]
        title = normalize_line(row.get("name") or "")
        title = re.sub(r"^(the|a|an) ", "", title)
        if len(title.split()) >= 2 or (len(title) >= 5 and title not in common):
            add(title, "film", film_id, film_id)
        for member in json.loads(row.get("cast") or "[]"):
            characters = [normalize_line(c) for c in member.get("characters") or [] if c]
            for phrase in _phrases_for_name(member.get("actor") or "", common, parts=False):
                for character in characters:
                    add(phrase, "character", film_id, character)
            for character, raw in zip(characters, member.get("characters") or []):
                for phrase in _phrases_for_name(raw, common):
                    add(phrase, "character", film_id, character)
    if SHOT_EVIDENCE in names:
        seen: set[tuple[str, str]] = set()
        for row in (db.open_table(SHOT_EVIDENCE).search().select(["film_id", "characters"])
                    .where("characters IS NOT NULL").limit(None).to_list()):
            for raw in json.loads(row["characters"] or "[]"):
                if (row["film_id"], raw) in seen:
                    continue
                seen.add((row["film_id"], raw))
                for phrase in _phrases_for_name(raw, common):
                    add(phrase, "character", row["film_id"], normalize_line(raw))
    built = Vocabulary(key=key, phrases=phrases, longest=max((len(p.split()) for p in phrases), default=1))
    with _LOCK:
        _VOCABULARY[key[0]] = built
    return built


def parse(query: str, vocab: Vocabulary | None) -> Signals:
    text = " " + normalize_line(query).replace("-", " ") + " "
    signals = Signals()
    for phrase, scale in _SCALE:
        if f" {phrase} " in text:
            signals.scale = scale
            break
    for phrase, labels in _CAMERA:
        if f" {phrase} " in text:
            signals.camera |= labels
    for phrase, value in _TIME:
        if f" {phrase} " in text:
            signals.time = value
            break
    signals.black_and_white = " black and white " in text or " monochrome " in text
    if not signals.black_and_white:
        signals.colours = {colour for colour in _COLOURS if f" {colour} " in text}
    if vocab is not None:
        tokens = text.split()
        taken = [False] * len(tokens)            # longest phrases claim their tokens first
        for size in range(min(vocab.longest, 4), 0, -1):
            for start in range(len(tokens) - size + 1):
                if any(taken[start:start + size]):
                    continue
                matches = vocab.phrases.get(" ".join(tokens[start:start + size]), [])
                if matches:
                    taken[start:start + size] = [True] * size
                for kind, film_id, value in matches:
                    if kind == "film":
                        signals.films.add(film_id)
                    else:
                        signals.characters.add((film_id, value))
    return signals


def multiplier(signals: Signals, unit: dict[str, Any], evidence: dict[str, Any] | None) -> float:
    """Relevance factor for one candidate: >1 when its evidence satisfies the query, <1 when it contradicts."""
    factor = 1.0
    film_id = str(unit.get("film_id") or "")
    if signals.films:
        factor *= 1.5 if film_id in signals.films else 1.0
    if signals.characters:
        present = {normalize_line(name) for name in json.loads((evidence or {}).get("characters") or "[]")}
        wanted = {name for film, name in signals.characters if film == film_id}
        if wanted & present:
            factor *= 1.8
    framing = str(unit.get("framing") or "")
    if signals.scale and framing and framing != "unknown":
        factor *= 1.3 if framing == signals.scale else 0.8
    if signals.camera and evidence and evidence.get("camera") and float(evidence.get("camera_reliability") or 0) >= 0.5:
        if evidence["camera"] in signals.camera:
            factor *= 1.3
        elif evidence["camera"] != "unknown":
            factor *= 0.85
    time_of_day = str(unit.get("time_of_day") or "")
    if signals.time and time_of_day and time_of_day != "unknown":
        factor *= 1.2 if time_of_day == signals.time else 0.85
    if signals.black_and_white:
        palette = " ".join(json.loads(unit.get("palette") or "[]"))
        saturation = (evidence or {}).get("saturation")
        if "black and white" in palette or (saturation is not None and saturation < 0.06):
            factor *= 1.4
        elif saturation is not None and saturation > 0.2:
            factor *= 0.8
    elif signals.colours:
        palette = " ".join(json.loads(unit.get("palette") or "[]"))
        if any(colour in palette for colour in signals.colours):
            factor *= 1.2
    return factor


def reorder(items: list[T], signals: Signals, units: dict[str, dict[str, Any]], evidence: dict[str, dict[str, Any]],
            *, unit_id: Callable[[T], str], offset: int = 20) -> list[T]:
    """Stable, bounded reorder of relevance-ranked items by their signal multipliers."""
    scored = []
    for position, item in enumerate(items):
        key = unit_id(item)
        factor = multiplier(signals, units.get(key) or {}, evidence.get(key))
        scored.append((-(factor / (offset + position + 1)), position, item))
    scored.sort(key=lambda entry: (entry[0], entry[1]))
    return [item for _score, _position, item in scored]
