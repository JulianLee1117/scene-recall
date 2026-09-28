"""Compile evidence artifacts into search tables.

Every compiled table is a rebuildable view: dropping it loses nothing that
``python -m pipeline.evidence compile`` cannot recreate from the artifacts.
"""

from __future__ import annotations

import bisect
import json
import math
from pathlib import Path
from typing import Any, Callable

from pipeline.evidence import metadata, store, tables
from pipeline.evidence.library import FilmRef


def _percentiles(values: dict[str, float]) -> dict[str, float]:
    """Rank-based percentile in [0, 1]; ties share the average rank."""
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda item: item[1])
    result: dict[str, float] = {}
    index = 0
    while index < len(ordered):
        end = index
        while end + 1 < len(ordered) and ordered[end + 1][1] == ordered[index][1]:
            end += 1
        rank = (index + end) / 2
        for position in range(index, end + 1):
            result[ordered[position][0]] = rank / max(1, len(ordered) - 1)
        index = end + 1
    return result


def film_popularity(documents: dict[str, dict[str, Any]]) -> dict[str, float]:
    """Blend IMDb votes (long-run reach) with pageviews (current attention)."""
    votes = {film_id: math.log10(1 + (doc.get("popularity", {}).get("imdb_votes") or 0)) for film_id, doc in documents.items()}
    views = {film_id: math.log10(1 + (doc.get("popularity", {}).get("pageviews_12m") or 0)) for film_id, doc in documents.items()}
    vote_rank, view_rank = _percentiles(votes), _percentiles(views)
    return {film_id: round(0.75 * vote_rank.get(film_id, 0.0) + 0.25 * view_rank.get(film_id, 0.0), 4) for film_id in documents}


def compile_film_meta(config: Any, db: Any, films: list[FilmRef], progress: Callable[[str], None] = print) -> int:
    documents: dict[str, dict[str, Any]] = {}
    for film in films:
        artifact = store.serving_artifact(config.paths.assets_dir, film.film_id, metadata.PRODUCER)
        if artifact is not None:
            documents[film.film_id] = artifact["data"]
    popularity = film_popularity(documents)
    rows = []
    for film in films:
        doc = documents.get(film.film_id, {})
        wiki = doc.get("wikidata", {})
        pop = doc.get("popularity", {})
        rows.append({
            "schema_version": tables.TABLE_SCHEMA_VERSION,
            "film_id": film.film_id,
            "title": film.title,
            "name": wiki.get("label") or film.name,
            "year": film.year,
            "wikidata_id": wiki.get("id"),
            "imdb_id": wiki.get("imdb_id"),
            "wikipedia_title": doc.get("wikipedia", {}).get("title"),
            "directors": json.dumps(wiki.get("directors", []), ensure_ascii=False),
            "genres": json.dumps(wiki.get("genres", []), ensure_ascii=False),
            "countries": json.dumps(wiki.get("countries", []), ensure_ascii=False),
            "languages": json.dumps(wiki.get("languages", []), ensure_ascii=False),
            "cast": json.dumps([{"actor": row["actor"], "characters": row["characters"]} for row in wiki.get("cast", [])], ensure_ascii=False),
            "plot": doc.get("wikipedia", {}).get("plot", ""),
            "quote_count": len(doc.get("wikiquote", {}).get("quotes", [])),
            "imdb_rating": pop.get("imdb_rating"),
            "imdb_votes": pop.get("imdb_votes"),
            "pageviews_12m": pop.get("pageviews_12m"),
            "popularity": popularity.get(film.film_id),
            "metadata_profile": metadata.PRODUCER.profile_id if film.film_id in documents else None,
        })
    tables.replace_all(db, tables.FILM_META, tables.film_meta_schema(), "film_id", rows)
    progress(f"[compile] film_meta: {len(rows)} films ({len(documents)} with metadata)")
    return len(rows)


# ---------------------------------------------------------------------------
# Semantic view text (embedded by the text-feature backfill)
# ---------------------------------------------------------------------------


def _clean(text: Any) -> str:
    return " ".join(str(text or "").split())


def _sentence(text: Any) -> str:
    return _clean(text).rstrip(".") + "."


def story_view_text(story: dict[str, Any], scene: dict[str, Any] | None, iconic_note: str, famous_line: str | None) -> str:
    """What happens in this shot, in the words people use to remember it."""
    parts = []
    if story.get("action"):
        parts.append(_sentence(story["action"]))
    if story.get("characters"):
        parts.append("Characters: " + ", ".join(story["characters"]) + ".")
    if iconic_note:
        parts.append(_sentence(iconic_note))
    if scene and scene.get("setting"):
        parts.append("Setting: " + _sentence(scene["setting"]))
    if famous_line:
        parts.append('Famous line: "' + _clean(famous_line) + '"')
    return " ".join(parts)


def scene_view_text(scene: dict[str, Any]) -> str:
    """The dramatic scene around a shot; shared by every shot in the scene."""
    parts = [_sentence(scene[key]) for key in ("title", "summary", "story_context") if scene.get(key)]
    if scene.get("setting"):
        parts.append("Setting: " + _sentence(scene["setting"]))
    if scene.get("characters"):
        parts.append("Characters: " + ", ".join(scene["characters"]) + ".")
    if scene.get("tone"):
        parts.append("Tone: " + _sentence(scene["tone"]))
    return " ".join(parts)


def mood_view_text(story: dict[str, Any], scene: dict[str, Any] | None) -> str:
    """Feeling in context (performance, music, story), replacing stills-only mood labels."""
    parts = []
    if story.get("emotion"):
        parts.append("emotion: " + _clean(story["emotion"]))
    if scene and scene.get("tone"):
        parts.append("scene tone: " + _clean(scene["tone"]))
    if story.get("audio"):
        parts.append("sound: " + _clean(story["audio"]))
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Per-film evidence tables
# ---------------------------------------------------------------------------


def _in_content(value: float, low: float, high: float) -> float:
    return round(min(1.0, max(0.0, (value - low) / max(high - low, 1e-6))), 4)


def content_subject(main: dict[str, Any] | None, content: list[float] | None) -> dict[str, Any] | None:
    """Main-subject track re-expressed in content-box coordinates (letterbox bars removed)."""
    if not main:
        return None
    x0, y0, x1, y1 = content or (0.0, 0.0, 1.0, 1.0)

    def box(values: list[float]) -> list[float]:
        return [_in_content(values[0], x0, x1), _in_content(values[1], y0, y1),
                _in_content(values[2], x0, x1), _in_content(values[3], y0, y1)]

    return {**main, "box_start": box(main["box_start"]), "box_end": box(main["box_end"]),
            "center": [_in_content(main["center"][0], x0, x1), _in_content(main["center"][1], y0, y1)],
            "size": round(float(main["size"]) / max((x1 - x0) * (y1 - y0), 1e-6), 4)}


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _scene_rows(film: FilmRef, story_data: dict[str, Any], priors: dict[str, Any],
                profile: str | None) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    shots_story = story_data.get("shots") or {}
    members: dict[int, list[str]] = {}
    for unit_id, record in shots_story.items():
        if record.get("scene") is not None:
            members.setdefault(record["scene"], []).append(unit_id)
    rows, by_index = [], {}
    for scene in story_data.get("scenes") or []:
        scene_id = f"{film.film_id}:s{scene['index']:04d}"
        by_index[scene["index"]] = {**scene, "scene_id": scene_id}
        fame = [priors[u]["fame"] for u in members.get(scene["index"], []) if (priors.get(u) or {}).get("fame") is not None]
        rows.append({
            "schema_version": tables.TABLE_SCHEMA_VERSION, "scene_id": scene_id, "film_id": film.film_id,
            "index": scene["index"], "t_start": scene["t_start"], "t_end": scene["t_end"],
            "first_unit_id": scene["first_unit"], "last_unit_id": scene["last_unit"],
            "shot_count": scene["last_shot"] - scene["first_shot"] + 1, "title": scene["title"],
            "summary": scene["summary"], "setting": scene["setting"], "characters": _json(scene["characters"]),
            "story_context": scene["story_context"], "tone": scene["tone"], "fame": max(fame) if fame else None,
            "iconic_count": sum(1 for u in members.get(scene["index"], []) if (priors.get(u) or {}).get("iconic")),
            "profile": profile,
        })
    return rows, by_index


def _line_rows(config: Any, film: FilmRef, units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from pipeline.evidence.subtitles import accepted_download, current_dialogue
    from pipeline.evidence.textnorm import normalize_line

    assets = Path(config.paths.assets_dir)
    manifest = store.read_json(assets / film.film_id / "dialogue.manifest.json") or {}
    source = "downloaded_srt" if accepted_download(config, film.film_id) else str(manifest.get("kind") or "unknown")
    starts = [float(unit["t_start"]) for unit in units]
    rows = []
    for index, line in enumerate(current_dialogue(config, film.film_id)):
        start, end = float(line["start"]), float(line["end"])
        text = _clean(line.get("text"))
        norm = normalize_line(text)
        if not norm:
            continue
        position = max(0, bisect.bisect_right(starts, start) - 1)
        best, best_overlap = units[position]["unit_id"] if units else None, 0.0
        for candidate in units[position:position + 12]:
            if float(candidate["t_start"]) >= end:
                break
            overlap = min(end, float(candidate["t_end"])) - max(start, float(candidate["t_start"]))
            if overlap > best_overlap:
                best, best_overlap = candidate["unit_id"], overlap
        rows.append({"schema_version": tables.TABLE_SCHEMA_VERSION, "line_id": f"{film.film_id}:l{index:05d}",
                     "film_id": film.film_id, "unit_id": best, "t_start": start, "t_end": end, "text": text,
                     "norm": norm, "source": source})
    return rows


def compile_film_evidence(config: Any, db: Any, film: FilmRef) -> dict[str, int]:
    """Rebuild one film's shot_evidence, scenes and dialogue_lines rows from its artifacts."""
    from pipeline.evidence import hero, measure, synthesis, understanding
    from pipeline.evidence.library import film_units

    assets = config.paths.assets_dir
    units = film_units(db, film.film_id, columns=["unit_id", "t_start", "t_end"])
    docs = {kind: store.serving_artifact(assets, film.film_id, producer) for kind, producer in (
        ("understanding", understanding.producer()), ("measure", measure.PRODUCER),
        ("synthesis", synthesis.PRODUCER), ("hero", hero.PRODUCER))}
    sources = {kind: doc["profile_id"] for kind, doc in docs.items() if doc}
    if docs["measure"]:
        sources["camera_labels"] = f"v{measure.CAMERA_LABELS_VERSION}"
    story_data = (docs["understanding"] or {}).get("data") or {}
    measure_data = (docs["measure"] or {}).get("data") or {}
    priors = ((docs["synthesis"] or {}).get("data") or {}).get("shots") or {}
    heroes = ((docs["hero"] or {}).get("data") or {}).get("shots") or {}
    content = measure_data.get("content_box")
    scene_rows, scenes = _scene_rows(film, story_data, priors, sources.get("understanding"))
    iconic_notes = {row["unit_id"]: row["description"] for row in story_data.get("iconic") or []}
    hero_root = hero.directory(config, film.film_id, sources.get("hero")).relative_to(assets).as_posix()

    shot_rows = []
    for unit in units if sources else []:
        unit_id = unit["unit_id"]
        story = (story_data.get("shots") or {}).get(unit_id) or {}
        scene = scenes.get(story.get("scene")) if story else None
        m = (measure_data.get("shots") or {}).get(unit_id) or {}
        camera = measure.camera_from_series(m["flow"]) if m.get("flow") else (m.get("camera") or {})
        look, subjects = m.get("look") or {}, m.get("subjects") or {}
        subject = content_subject(subjects.get("main"), content)
        prior = priors.get(unit_id) or {}
        pick = heroes.get(unit_id) or {}
        shot_rows.append({
            "schema_version": tables.TABLE_SCHEMA_VERSION, "unit_id": unit_id, "film_id": film.film_id,
            "t_start": float(unit["t_start"]), "t_end": float(unit["t_end"]),
            "scene_id": scene["scene_id"] if scene else None,
            "characters": _json(story.get("characters")) if story else None,
            "action": story.get("action") or None, "peak_time": story.get("peak_time"),
            "emotion": story.get("emotion") or None, "line": story.get("line") or None,
            "speaker": story.get("speaker") or None, "audio_cue": story.get("audio") or None,
            "camera_hint": story.get("camera_hint"), "cut_hint": story.get("cut_inside"),
            "iconic_note": iconic_notes.get(unit_id),
            "fame": prior.get("fame"), "fame_library": prior.get("fame_library"), "craft": prior.get("craft"),
            "distinctiveness": prior.get("distinctiveness"), "iconic": prior.get("iconic"), "gem": prior.get("gem"),
            "famous_line": prior.get("famous_line"),
            "camera": camera.get("dominant"), "camera_slow": camera.get("slow") if m else None,
            "camera_moving": camera.get("moving"),
            "camera_reliability": camera.get("reliability"),
            "camera_segments": _json(camera.get("segments")) if m else None,
            "motion_energy": m.get("motion_energy"), "hidden_cuts": _json(m.get("cuts")) if m else None,
            "people": subjects.get("people_median"), "people_max": subjects.get("people_max"),
            "subject": _json(subject),
            "subject_x": subject["center"][0] if subject else None, "subject_y": subject["center"][1] if subject else None,
            "subject_size": subject["size"] if subject else None,
            "brightness": look.get("brightness"), "contrast": look.get("contrast"), "saturation": look.get("saturation"),
            "colorfulness": look.get("colorfulness"), "warmth": look.get("warmth"),
            "palette": _json(m.get("palette")), "sharpness": m.get("sharpness"),
            "hero_path": f"{hero_root}/{pick['file']}" if pick.get("file") else None,
            "hero_time": pick.get("time") if pick.get("file") else None,
            "story_text": story_view_text(story, scene, iconic_notes.get(unit_id, ""), prior.get("famous_line")) if story else None,
            "scene_text": scene_view_text(scene) if scene else None,
            "mood_text": mood_view_text(story, scene) if story else None,
            "sources": _json(sources),
        })

    line_rows = _line_rows(config, film, units)
    tables.replace_film(db, tables.SCENES, tables.scenes_schema(), "scene_id", film.film_id, scene_rows)
    tables.replace_film(db, tables.SHOT_EVIDENCE, tables.shot_evidence_schema(), "unit_id", film.film_id, shot_rows)
    tables.replace_film(db, tables.DIALOGUE_LINES, tables.dialogue_lines_schema(), "line_id", film.film_id, line_rows)
    return {"shots": len(shot_rows), "scenes": len(scene_rows), "lines": len(line_rows)}


def index_tables(db: Any) -> None:
    """(Re)build compiled-table indexes: key lookups and the quote index.

    The quote index keeps positions for phrase queries, does not stem and keeps
    stop words, because remembered lines are mostly stop words.
    """
    from pipeline.index.writer import table_names

    names = table_names(db)
    with tables._PUBLICATION_LOCK, tables._database_write_lock(db):
        for name, key in ((tables.SHOT_EVIDENCE, "unit_id"), (tables.SCENES, "scene_id"),
                          (tables.DIALOGUE_LINES, "line_id")):
            if name in names:
                db.open_table(name).create_scalar_index(key, index_type="BTREE", replace=True)
        if tables.DIALOGUE_LINES in names:
            db.open_table(tables.DIALOGUE_LINES).create_fts_index(
                "norm", replace=True, base_tokenizer="simple", lower_case=True, stem=False, remove_stop_words=False,
                ascii_folding=True, with_position=True, max_token_length=40)


def compile_films(config: Any, db: Any, films: list[FilmRef], progress: Callable[[str], None] = print) -> dict[str, int]:
    """Recompile per-film evidence tables, then refresh the quote index."""
    totals = {"films": 0, "shots": 0, "scenes": 0, "lines": 0}
    for film in films:
        counts = compile_film_evidence(config, db, film)
        totals["films"] += 1
        for key, value in counts.items():
            totals[key] += value
        if counts["shots"]:
            progress(f"[compile] {film.title}: {counts['shots']} shots, {counts['scenes']} scenes, {counts['lines']} lines")
    index_tables(db)
    progress(f"[compile] {totals}")
    return totals
