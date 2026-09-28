"""Editor sources carry evidence v2 (what happens, peak, measured camera, subject, fame/craft)."""

from __future__ import annotations

import json

import lancedb

from pipeline.evidence import tables
from pipeline.lab.music_planner import _attach_evidence


def _row(unit_id: str, **values):
    base = {name: None for name in tables.shot_evidence_schema().names}
    base.update(schema_version=1, unit_id=unit_id, film_id="film", t_start=0.0, t_end=4.0)
    base.update(values)
    return base


def test_editor_sources_gain_evidence_and_lose_guessed_camera(tmp_path):
    db = lancedb.connect(str(tmp_path / "db"))
    tables.replace_film(db, tables.SHOT_EVIDENCE, tables.shot_evidence_schema(), "unit_id", "film", [
        _row("a", action="Neo dodges bullets", characters='["Neo"]', peak_time=2.5, camera="push_in",
             camera_reliability=0.9, camera_segments=json.dumps([[0, 4, "push_in"]]),
             subject=json.dumps({"class": "person", "center": [0.4, 0.5], "size": 0.2, "direction": "left"}),
             fame_library=0.95, craft=0.8, iconic=True, gem=False, scene_id="film:s0001"),
        _row("b", action="A hallway", camera="unknown", camera_reliability=0.1, fame_library=0.1, craft=0.6, gem=True),
    ])
    tables.replace_film(db, tables.SCENES, tables.scenes_schema(), "scene_id", "film", [
        {"schema_version": 1, "scene_id": "film:s0001", "film_id": "film", "index": 1, "t_start": 0.0, "t_end": 9.0,
         "first_unit_id": "a", "last_unit_id": "a", "shot_count": 1, "title": "Rooftop", "summary": "Bullet time.",
         "setting": "roof", "characters": "[]", "story_context": "", "tone": "", "fame": 0.9, "iconic_count": 1,
         "profile": "p"}])
    sources = {"a": {"metadata": {"camera_motion": "tracking"}}, "b": {"metadata": {"camera_motion": "pan"}},
               "c": {"metadata": {"camera_motion": "static"}}}
    _attach_evidence(sources, db)
    a, b, c = sources["a"], sources["b"], sources["c"]
    assert a["evidence"]["peak_time"] == 2.5 and a["evidence"]["characters"] == ["Neo"]
    assert a["evidence"]["camera"]["movement"] == "push in" and a["metadata"]["camera_motion"] == "push in"
    assert a["evidence"]["scene"]["title"] == "Rooftop" and a["evidence"]["iconic"] is True
    assert "camera" not in b["evidence"] and "camera_motion" not in b["metadata"]    # unreliable: dropped, not guessed
    assert b["evidence"]["gem"] is True
    assert "evidence" not in c and c["metadata"]["camera_motion"] == "static"      # no evidence: annotations kept
