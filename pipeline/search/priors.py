"""Ordering after relevance: presets, bounded prior re-rank, scene grouping and presentation.

Relevance decides the candidate pool; priors only reorder inside it. A prior
scales a shot's relevance score by a bounded factor (:func:`multiplier`), so it
settles near-ties (two equally good matches: the iconic one first) but cannot
overrule a clearly stronger match, and an iconic but unrelated shot cannot
displace a relevant one. Where no relevance score exists (recipe results), the
same factor scales a rank-based relevance instead (roughly: from rank 20 to
rank 5, never from rank 200 to rank 1). Quote-like queries weaken priors: the
remembered line is the answer, famous or not.

Presets (docs/current-work.md, "Balancing famous and forgotten"):

* ``balanced`` — relevance first; iconic and well-crafted shots rise a little,
  weak coverage (transitional, unusable) sinks a little.
* ``famous`` — fame dominates within the relevant pool.
* ``gems`` — well-crafted shots people rarely see: iconic shots and weak
  craft are demoted, obscure films are favoured.

Shots without evidence get a neutral prior, so films awaiting the evidence
passes are not excluded, only not boosted.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable, TypeVar

from pipeline.evidence.tables import SCENES, SHOT_EVIDENCE

T = TypeVar("T")

PRESETS = ("balanced", "famous", "gems")
DEFAULT_PRESET = "balanced"
_RANK_OFFSET = 20            # relevance(rank) = 1 / (offset + rank): smooth near the top
_STRENGTH = {"balanced": 0.6, "famous": 1.6, "gems": 1.4}
_MIN_MULTIPLIER = 0.15
_EVIDENCE_COLUMNS = ["unit_id", "film_id", "scene_id", "fame", "fame_library", "craft", "distinctiveness", "iconic",
                     "gem", "famous_line", "iconic_note", "hero_path", "hero_time", "characters", "action", "peak_time",
                     "camera", "camera_reliability", "saturation"]
_SCENE_COLUMNS = ["scene_id", "title", "summary", "t_start", "t_end", "shot_count"]
MAX_SCENE_ALTERNATIVES = 8


def validate_preset(preset: str | None) -> str:
    preset = (preset or DEFAULT_PRESET).strip().lower()
    if preset not in PRESETS:
        raise ValueError(f"preset must be one of {PRESETS}")
    return preset


def _in_list(column: str, values: Iterable[str]) -> str:
    quoted = ", ".join("'" + value.replace("'", "''") + "'" for value in values)
    return f"{column} IN ({quoted})"


def load_evidence(db: Any, unit_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    """Compiled shot evidence for the given units (missing units simply have none)."""
    from pipeline.index.writer import table_names

    ids = [unit_id for unit_id in dict.fromkeys(unit_ids) if unit_id]
    if not ids or SHOT_EVIDENCE not in table_names(db):
        return {}
    table = db.open_table(SHOT_EVIDENCE)
    rows: dict[str, dict[str, Any]] = {}
    for start in range(0, len(ids), 400):
        chunk = ids[start:start + 400]
        for row in table.search().select(_EVIDENCE_COLUMNS).where(_in_list("unit_id", chunk)).limit(len(chunk)).to_list():
            rows[row["unit_id"]] = row
    return rows


def load_scenes(db: Any, scene_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
    from pipeline.index.writer import table_names

    ids = [scene_id for scene_id in dict.fromkeys(scene_ids) if scene_id]
    if not ids or SCENES not in table_names(db):
        return {}
    table = db.open_table(SCENES)
    return {row["scene_id"]: row for row in
            table.search().select(_SCENE_COLUMNS).where(_in_list("scene_id", ids)).limit(len(ids)).to_list()}


def prior(evidence: dict[str, Any] | None, preset: str) -> float:
    """Signed prior in about [-1, 1] for one shot; 0 when the shot has no evidence."""
    if not evidence or evidence.get("craft") is None:
        return 0.0
    fame = float(evidence.get("fame_library") or 0.0)
    craft = float(evidence["craft"])
    if preset == "famous":
        return fame + (0.2 if evidence.get("iconic") else 0.0)
    if preset == "gems":
        distinct = float(evidence.get("distinctiveness") or 0.0)
        value = craft + 0.2 * distinct - 1.3 * fame - (0.6 if evidence.get("iconic") else 0.0)
        return value - (0.8 if craft < 0.45 else 0.0)
    value = 0.55 * fame + 0.45 * craft
    return value - (0.5 if craft < 0.2 else 0.0)


def multiplier(evidence: dict[str, Any] | None, *, preset: str, specificity: float) -> float:
    """Bounded relevance factor for one shot under *preset*; 1.0 without evidence or for a fully specific query."""
    strength = _STRENGTH[preset] * max(0.0, 1.0 - specificity)
    return max(_MIN_MULTIPLIER, 1.0 + strength * prior(evidence, preset))


def rerank(items: list[T], evidence: dict[str, dict[str, Any]], *, preset: str, specificity: float,
           unit_id: Callable[[T], str]) -> list[T]:
    """Reorder rank-ordered *items* (no relevance scores) by bounded priors; stable for ties and missing evidence."""
    if _STRENGTH[preset] * max(0.0, 1.0 - specificity) <= 0.0 or not evidence:
        return list(items)
    scored = []
    for position, item in enumerate(items):
        relevance = 1.0 / (_RANK_OFFSET + position + 1)
        factor = multiplier(evidence.get(unit_id(item)), preset=preset, specificity=specificity)
        scored.append((-relevance * factor, position, item))
    scored.sort(key=lambda entry: (entry[0], entry[1]))
    return [item for _score, _position, item in scored]


def group_by_scene(items: list[T], evidence: dict[str, dict[str, Any]], *, unit_id: Callable[[T], str],
                   attach: Callable[[T, T], None]) -> list[T]:
    """Keep the best shot per dramatic scene; later shots of the scene become its alternatives."""
    representatives: dict[str, T] = {}
    grouped: list[T] = []
    for item in items:
        scene_id = (evidence.get(unit_id(item)) or {}).get("scene_id")
        if scene_id and scene_id in representatives:
            attach(representatives[scene_id], item)
            continue
        if scene_id:
            representatives[scene_id] = item
        grouped.append(item)
    return grouped


def decorate(result: dict[str, Any], evidence: dict[str, Any] | None, scene: dict[str, Any] | None) -> dict[str, Any]:
    """Add hero frame, badges, story and scene context to one API result (in place)."""
    if not evidence:
        return result
    unit_id = str(result.get("unit_id") or "")
    if evidence.get("hero_path"):
        result["hero_url"] = hero_url(unit_id, evidence)
        result["hero_time"] = evidence.get("hero_time")
        # keyframe_url/keyframe_index keep naming the exact indexed frame used
        # when a result becomes a search source; thumbnail_url is display only.
        result["thumbnail_url"] = result["keyframe_url"] if _visual_match_leads(result) else result["hero_url"]
    badges = []
    if evidence.get("iconic"):
        badges.append("iconic")
    if evidence.get("gem"):
        badges.append("gem")
    result["badges"] = badges
    if evidence.get("famous_line"):
        result["famous_line"] = evidence["famous_line"]
    if evidence.get("action"):
        result["action"] = evidence["action"]
    if evidence.get("characters"):
        try:
            result["characters"] = json.loads(evidence["characters"])
        except (TypeError, ValueError):
            pass
    if evidence.get("peak_time") is not None:
        result["peak_time"] = evidence["peak_time"]
    if scene:
        result["scene"] = {"id": scene["scene_id"], "title": scene.get("title") or "",
                           "summary": scene.get("summary") or "", "t_start": scene.get("t_start"),
                           "t_end": scene.get("t_end"), "shot_count": scene.get("shot_count")}
    return result


def hero_url(unit_id: str, evidence: dict[str, Any]) -> str:
    """Hero frame URL; the pick time versions it so browsers refetch after a re-pick."""
    return f"/media/hero/{unit_id}?t={float(evidence.get('hero_time') or 0.0):.3f}"


def _visual_match_leads(result: dict[str, Any]) -> bool:
    """Show the matched keyframe only when the visual channel is what found this shot."""
    if "matched_frame_url" not in result:
        return False
    channels = (result.get("debug") or {}).get("channels") or {}
    image_rank = (channels.get("img") or {}).get("rank")
    other_ranks = [value.get("rank") for name, value in channels.items() if name != "img" and isinstance(value, dict)]
    other_ranks = [rank for rank in other_ranks if rank is not None]
    return image_rank is not None and (not other_ranks or image_rank <= min(other_ranks))
