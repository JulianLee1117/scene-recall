"""OpenTimelineIO export: frame-exact tracks on the original media."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.lab import otio_export


class _Store:
    def get_track(self, track_id):
        return {"id": track_id, "path": str(Path("C:/music/song.wav"))}


@pytest.fixture
def films(monkeypatch):
    monkeypatch.setattr(otio_export, "resolve_film", lambda _db, film_id: {
        "film_id": film_id, "title": f"Film {film_id}", "path": str(Path(f"C:/films/{film_id}.mkv"))})


def _document():
    return {"fps": 24, "track": {"id": "t", "name": "Song", "duration": 200.0}, "passage": {"start": 10.0, "end": 13.0},
            "clips": [{"id": "c1", "film_id": "a", "unit_id": "u1", "title": "Film a", "source_start": 100.0,
                       "source_end": 101.02, "crop": None},
                      {"id": "c2", "film_id": "b", "unit_id": "u2", "title": "a door opens", "source_start": 50.5,
                       "source_end": 51.48, "crop": {"x": 0, "y": 0, "width": 0.5, "height": 0.5}}],
            "music_timeline": {"slots": [{"id": "s1", "start": 10.0, "end": 11.02, "clip_id": "c1"},
                                         {"id": "s2", "start": 11.02, "end": 12.0, "clip_id": None},
                                         {"id": "s3", "start": 12.0, "end": 13.0, "clip_id": "c2"}]},
            "dialogue_clips": [{"id": "d", "film_id": "a", "start": 10.5, "source_start": 200.0, "source_end": 201.0,
                                "text": "hello", "gain_db": -3}]}


def test_timeline_has_frame_exact_tracks_on_the_original_media(films):
    timeline = otio_export.export_timeline(_document(), None, _Store(), name="My edit")
    video, music, dialogue = timeline["tracks"]["children"]
    frames = [item["source_range"]["duration"]["value"] for item in video["children"]]
    assert sum(frames) == 72 == timeline["metadata"]["scene_recall"]["frames"]      # 3 s at 24 fps
    assert [item["OTIO_SCHEMA"] for item in video["children"]] == ["Clip.1", "Gap.1", "Clip.1"]
    first, _gap, last = video["children"]
    assert first["name"] == "Film a" and last["name"] == "Film b: a door opens"
    assert first["source_range"]["start_time"]["value"] == pytest.approx(2400.0)
    assert first["media_reference"]["target_url"].startswith("file:///") and first["media_reference"]["target_url"].endswith("a.mkv")
    assert last["metadata"]["scene_recall"]["crop"]["width"] == 0.5
    assert music["children"][0]["source_range"]["start_time"]["value"] == pytest.approx(240.0)
    assert [item["OTIO_SCHEMA"] for item in dialogue["children"]] == ["Gap.1", "Clip.1"]
    assert dialogue["children"][0]["source_range"]["duration"]["value"] == 12


def test_export_requires_a_music_timeline(films):
    with pytest.raises(ValueError):
        otio_export.export_timeline({**_document(), "music_timeline": None}, None, _Store(), name="x")
