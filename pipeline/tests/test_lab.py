"""Creative workflow boundaries: durability, concurrency, source bounds and media."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from filelock import FileLock, Timeout

from pipeline.index.writer import create_tables, open_db
from pipeline.lab.media import import_track, probe_media, render_manifest, render_reel, run_process
from pipeline.lab.models import ProjectDocument
from pipeline.lab.store import LabStore, RevisionConflict
from pipeline.lab.worker import run_worker


@pytest.fixture
def store(config):
    store = LabStore(config.paths.state_dir)
    store.initialize()
    return store


@pytest.fixture
def db(config):
    db = open_db(config)
    create_tables(db, vector_dim=4)
    return db


def test_project_listing_summarizes_without_documents(store):
    document = {
        "track": {"id": "t", "name": "Song.wav", "duration": 30},
        "clips": [{"id": f"c{i}", "film_id": "f", "unit_id": unit, "source_start": 0, "source_end": 1}
                  for i, unit in enumerate(["u1", "gen-1", "u1", "u2", "u3", "u4", "u5"])],
    }
    reel = store.create_project("Reel", "music-sketch", document)
    store.create_project("Empty", "music-sketch")
    listed = store.list_projects()
    assert [item["name"] for item in listed] == ["Empty", "Reel"]
    assert all("document" not in item for item in listed)
    summary = listed[1]
    assert (summary["id"], summary["revision"], summary["active_job_count"]) == (reel["id"], 1, 0)
    assert (summary["track_name"], summary["clip_count"]) == ("Song.wav", 7)
    assert summary["sheet_unit_ids"] == ["u1", "u2", "u3", "u4"], "distinct real shots only, four at most"
    assert (listed[0]["track_name"], listed[0]["clip_count"], listed[0]["sheet_unit_ids"]) == (None, 0, [])
    assert store.get_project(reel["id"])["document"]["clips"][1]["unit_id"] == "gen-1", "the document is intact"


def test_revisions_restore_and_stale_write_preserve_user_choices(store):
    project = store.create_project("My reel", "music-sketch")
    modified = {**project["document"], "brief": "Melancholy becoming hopeful"}
    saved = store.update_project(project["id"], 1, modified)
    with pytest.raises(RevisionConflict):
        store.update_project(project["id"], 1, project["document"])
    assert store.get_project(project["id"])["document"]["brief"] == modified["brief"]
    restored = store.restore(project["id"], saved["revision"], 1)
    assert restored["revision"] == 3
    assert restored["document"] == project["document"]
    assert len(store.revisions(project["id"])) == 3
    assert LabStore(store.root.parent).get_project(project["id"]) == restored


def test_completed_generation_retains_stale_proposal_without_overwriting(store):
    project = store.create_project("Reel", "music-sketch")
    queued = store.enqueue("analyze", project["id"], 1)
    job = store.claim()
    proposed = {**job["document"], "analysis": {"summary": "Model interpretation"}}
    edited = store.update_project(project["id"], 1, {**project["document"], "brief": "My intent"})
    result = store.finish(queued["id"], document=proposed)
    assert result["status"] == "completed"
    assert result["result"]["applied"] is False
    assert result["result"]["document"] == proposed
    assert store.get_project(project["id"]) == edited


def test_generated_revision_cannot_move_trim_or_unlock_locked_clip(store):
    project = store.create_project("Reel", "music-sketch")
    document = project["document"]
    document["clips"] = [{"id": "clip1", "film_id": "film", "source_start": 2, "source_end": 5, "locked": True}]
    project = store.update_project(project["id"], 1, document)
    store.enqueue("draft", project["id"], 2)
    job = store.claim()
    proposed = deepcopy(job["document"])
    proposed["clips"][0]["source_start"] = 3
    with pytest.raises(ValueError, match="locked"):
        store.finish(job["id"], document=proposed)
    assert store.get_project(project["id"]) == project
    result = store.finish(job["id"], document=job["document"])
    assert result["result"]["applied"] is True


def test_restart_marks_running_interrupted_and_never_replays(store):
    project = store.create_project("Reel", "music-sketch")
    interrupted = store.enqueue("analyze", project["id"], 1)
    queued = store.enqueue("render", project["id"], 1)
    store.claim()
    assert store.recover_interrupted() == 1
    assert store.get_job(interrupted["id"])["status"] == "interrupted"
    assert store.claim()["id"] == queued["id"]
    assert store.claim() is None


def test_cancelled_job_does_not_apply_hosted_result(store):
    project = store.create_project("Reel", "music-sketch")
    job = store.enqueue("analyze", project["id"], 1)
    store.claim()
    store.cancel(job["id"])
    result = store.finish(job["id"], document={**project["document"], "analysis": {"summary": "Late result"}})
    assert result["status"] == "cancelled"
    assert store.get_project(project["id"])["revision"] == 1
    other = store.enqueue("analyze", project["id"], 1)
    assert store.cancel(other["id"])["status"] == "cancelled"
    assert store.claim() is None


def test_worker_singleton_lock_prevents_competing_recovery(config, store):
    with FileLock(store.root / ".worker.lock", preserve_lock_file=True):
        with pytest.raises(Timeout):
            run_worker(config, once=True, db=MagicMock())


@pytest.mark.parametrize("change", [
    {"passage": {"start": 0, "end": 600.001}},
    {"clips": [{"id": "c", "film_id": "f", "source_start": 5, "source_end": 3}]},
    {"clips": [{"id": "c", "film_id": "f", "source_start": 0, "source_end": 3, "crop": {"x": .8, "y": 0, "width": .5, "height": 1}}]},
    {"clips": [{"id": "c", "film_id": "f", "source_start": float('nan'), "source_end": 3}]},
    {"audio_fade_in_seconds": -0.1},
    {"audio_fade_in_seconds": float('nan')},
    {"passage": {"start": 10, "end": 12}, "audio_fade_in_seconds": 2.1},
])
def test_project_rejects_invalid_media_ranges(change):
    with pytest.raises(ValueError):
        ProjectDocument.model_validate(change)


def test_api_source_bounds_stale_saves_and_missing_source_preservation(config, db):
    from pipeline.api.main import app
    db.open_table("films").add([{"film_id": "film", "title": "Film", "path": "missing.mkv", "duration": 10., "fps": 24.}])
    with patch("pipeline.api.main.load_config", return_value=config), patch("pipeline.api.main.open_db", return_value=db), patch("pipeline.api.main.ensure_search_indexes"), TestClient(app) as client:
        project = client.post("/lab/projects", json={"name": "Rhymes", "experiment_id": "visual-rhymes"}).json()
        url = f"/lab/projects/{project['id']}"
        doc = {**project["document"], "clips": [{"id": "c", "film_id": "film", "source_start": 1, "source_end": 11}]}
        assert client.put(url, json={"base_revision": 1, "document": doc}).status_code == 422
        doc["clips"][0]["source_end"] = 5
        saved = client.put(url, json={"base_revision": 1, "document": doc})
        assert saved.status_code == 200
        assert client.put(url, json={"base_revision": 1, "document": doc}).status_code == 409
        assert client.post(url + "/jobs", json={"base_revision": 2, "kind": "render"}).status_code == 422
        # Missing indexes do not erase or prevent editing existing durable anchors.
        db.open_table("films").delete("film_id = 'film'")
        doc["brief"] = "Keep this source selection"
        assert client.put(url, json={"base_revision": 2, "document": doc}).status_code == 200
        assert client.get(url + "/revisions").json()["revisions"][0]["revision"] == 3
        assert client.post(url + "/restore", json={"base_revision": 3, "revision": 1}).json()["revision"] == 4


def _fixture_media(directory):
    video, music = directory / "film.mp4", directory / "track.wav"
    # Red/blue halves, then green after one second, with no boundary keyframe.
    # This distinguishes correct decoded seeks and crops from stream-copy trims.
    picture = "color=c=red:s=160x90:r=30:d=3,drawbox=x=80:y=0:w=80:h=90:color=blue:t=fill,drawbox=x=0:y=0:w=160:h=90:color=green:t=fill:enable='gte(t,1)'"
    run_process(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", picture, "-f", "lavfi", "-i", "sine=frequency=700:duration=3", "-c:v", "libx264", "-g", "90", "-sc_threshold", "0", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)])
    run_process(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", str(music)])
    return video, music


@pytest.mark.parametrize("fade", [0, 0.5])
def test_real_render_uses_source_ranges_crop_music_and_frame_duration(config, store, db, tmp_path, fade):
    video, audio = _fixture_media(tmp_path)
    track = import_track(store, audio, "Music.wav")
    assert Path(track["path"]).is_file()
    db.open_table("films").add([{"film_id": "film", "title": "Film", "path": str(video), "duration": 3., "fps": 30.}])
    project = store.create_project("Reel", "music-sketch")
    doc = {**project["document"], "audio_fade_in_seconds": fade, "track": {key: track[key] for key in ("id", "name", "duration")}, "passage": {"start": .5, "end": 1.5}, "clips": [
        {"id": "a", "film_id": "film", "source_start": .25, "source_end": .75, "crop": {"x": .25, "y": 0, "width": .5, "height": 1}},
        {"id": "b", "film_id": "film", "source_start": 1.25, "source_end": 1.75},
    ]}
    project = store.update_project(project["id"], 1, doc)
    store.enqueue("render", project["id"], 2)
    job = store.claim()
    result = render_reel(job, config, db, store, lambda _: None)
    output = config.paths.assets_dir / "lab" / "renders" / job["id"] / "output.mp4"
    probe = probe_media(output)
    streams = {stream["codec_type"]: stream for stream in probe["streams"]}
    assert streams["video"]["r_frame_rate"] == "24/1"
    assert int(streams["video"]["nb_frames"]) == 24
    assert (streams["video"]["width"], streams["video"]["height"]) == (1280, 720)
    assert abs(float(streams["audio"]["duration"]) - 1) < .06
    assert result["manifest"]["clips"][0]["source_start"] == .25
    assert result["manifest"]["music"]["start"] == .5
    assert result["manifest"]["music"]["fade_in_seconds"] == fade
    assert result["manifest"]["duration"] == 1
    # Inspect decoded pixels at the cut: crop/letterbox, red/blue source, then green.
    from io import BytesIO
    from PIL import Image
    def pixels(frame):
        raw = run_process(["ffmpeg", "-v", "error", "-i", str(output), "-vf", f"select=eq(n\\,{frame})", "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "pipe:1"])
        return Image.open(BytesIO(raw)).convert("RGB")
    first, second = pixels(0), pixels(12)
    assert max(first.getpixel((100, 360))) < 10
    assert first.getpixel((500, 360))[0] > 220
    assert first.getpixel((780, 360))[2] > 220
    assert second.getpixel((500, 360))[1] > 100
    assert second.getpixel((500, 360))[0] < 20
    # Sources remain intact and derived renders contain the imported music tone.
    import numpy as np
    raw = run_process(["ffmpeg", "-v", "error", "-i", str(output), "-map", "0:a:0", "-ac", "1", "-ar", "8000", "-f", "f32le", "pipe:1"])
    samples = np.frombuffer(raw, dtype=np.float32)
    dominant = np.fft.rfftfreq(samples.size, 1 / 8000)[np.abs(np.fft.rfft(samples)).argmax()]
    assert abs(dominant - 440) < 3
    # Fade begins at the selected passage's zero, not source-track second zero.
    early = np.sqrt(np.mean(samples[160:640] ** 2))
    late = np.sqrt(np.mean(samples[6000:6800] ** 2))
    assert early / late < .25 if fade else early / late > .8
    from pipeline.lab.music import content_hash
    assert content_hash(Path(track["path"])) == track["id"]
    assert video.is_file() and Path(track["path"]).is_file()
    portrait = render_manifest(project["document"] | {"aspect_ratio": "9:16"}, db, store, mode="export")
    assert (portrait["width"], portrait["height"]) == (1080, 1920)


def test_render_requires_filled_music_passage_but_allows_silent_rhymes(config, store, db, tmp_path):
    source = tmp_path / "source.mp4"
    source.touch()
    db.open_table("films").add([{"film_id": "film", "title": "Film", "path": str(source), "duration": 10., "fps": 24.}])
    doc = ProjectDocument(clips=[{"id": "c", "film_id": "film", "source_start": 1, "source_end": 3}]).model_dump()
    with pytest.raises(ValueError, match="music track"):
        render_manifest(doc, db, store)
    assert render_manifest(doc, db, store, experiment_id="visual-rhymes")["duration"] == 2


def test_subprocess_cancel_stops_media_operation():
    import sys
    from pipeline.lab.media import JobCancelled
    with pytest.raises(JobCancelled):
        run_process([sys.executable, "-c", "import time; time.sleep(10)"], cancelled=lambda: True)


def test_upload_hash_identity_audio_range_and_job_reload(config, db, tmp_path):
    from pipeline.api.main import app
    audio = tmp_path / "upload.wav"
    run_process(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=1", str(audio)])
    evidence = audio.read_bytes()
    with patch("pipeline.api.main.load_config", return_value=config), patch("pipeline.api.main.open_db", return_value=db), patch("pipeline.api.main.ensure_search_indexes"), TestClient(app) as client:
        project = client.post("/lab/projects", json={"name": "Music"}).json()
        url = f"/lab/projects/{project['id']}"
        response = client.post(url + "/track", data={"base_revision": 1}, files={"file": ("track.wav", evidence, "audio/wav")})
        assert response.status_code == 200
        imported = response.json()
        assert imported["document"]["passage"] == {"start": 0, "end": 1}
        track_id = imported["document"]["track"]["id"]
        import hashlib
        assert track_id == hashlib.sha256(evidence).hexdigest()
        ranged = client.get(f"/lab/tracks/{track_id}/audio", headers={"Range": "bytes=0-9"})
        assert ranged.status_code == 206 and ranged.content == evidence[:10]
        invented = deepcopy(imported["document"])
        invented["track"]["duration"] = 100
        assert client.put(url, json={"base_revision": 2, "document": invented}).status_code == 422
        assert client.post(url + "/track", data={"base_revision": 1}, files={"file": ("track.wav", evidence)}).status_code == 409
        job = client.post(url + "/jobs", json={"kind": "analyze", "base_revision": 2})
        assert job.status_code == 200
        assert client.get(url + "/jobs").json()["jobs"][0]["id"] == job.json()["id"]
        assert client.post(url + "/jobs", json={"kind": "analyze", "base_revision": 2}).status_code == 409
        assert client.post(f"/lab/jobs/{job.json()['id']}/cancel").json()["status"] == "cancelled"
        assert client.get(f"/lab/jobs/{job.json()['id']}/output").status_code == 409
        assert client.post(url + "/track", data={"base_revision": 2}, files={"file": ("bad.wav", b"not audio")}).status_code == 422
        assert client.get(url).json()["document"]["track"]["id"] == track_id
        assert not list((config.paths.state_dir / "lab" / "tracks").glob("*.upload"))


@pytest.mark.parametrize("fade", [.25, 2.0])
def test_shorter_track_replacement_clamps_fade_and_preserves_original_revision(config, db, tmp_path, fade):
    from pipeline.api.main import app
    from pipeline.tests.test_lab_music import _audio
    source = tmp_path / "track.wav"
    _audio(source, 3)
    original = source.read_bytes()
    _audio(source, 1)
    replacement = source.read_bytes()
    with patch("pipeline.api.main.load_config", return_value=config), patch("pipeline.api.main.open_db", return_value=db), patch("pipeline.api.main.ensure_search_indexes"), TestClient(app) as client:
        project = client.post("/lab/projects", json={"name": "Music"}).json()
        url = f"/lab/projects/{project['id']}"
        first = client.post(url + "/track", data={"base_revision": 1}, files={"file": ("original.wav", original)}).json()
        doc = {**first["document"], "audio_fade_in_seconds": fade}
        assert client.put(url, json={"base_revision": 2, "document": doc}).status_code == 200
        response = client.post(url + "/track", data={"base_revision": 3}, files={"file": ("shorter.wav", replacement)})
        assert response.status_code == 200
        current = response.json()
        assert current["document"]["passage"] == {"start": 0, "end": 1}
        assert current["document"]["audio_fade_in_seconds"] == min(fade, 1)
        assert current["revision"] == 4
        assert client.get(f"/lab/tracks/{first['document']['track']['id']}/audio").content == original
        restored = client.post(url + "/restore", json={"base_revision": 4, "revision": 3}).json()
        assert restored["document"]["audio_fade_in_seconds"] == fade
        assert restored["document"]["passage"] == {"start": 0, "end": 3}


@pytest.mark.parametrize("explicit_timeline", [False, True])
def test_replacing_music_resets_legacy_and_timeline_arrangements_but_retains_locked_revision(config, db, tmp_path, explicit_timeline):
    from pipeline.api.main import app
    from pipeline.lab.timeline import ensure_timeline
    from pipeline.tests.test_lab_music import _audio, _interpretation
    source = tmp_path / "track.wav"
    _audio(source, 3); original = source.read_bytes()
    _audio(source, 1); replacement = source.read_bytes()
    db.open_table("films").add([{"film_id": "film", "title": "Film", "path": "saved-film.mkv", "duration": 100., "fps": 24.}])
    with patch("pipeline.api.main.load_config", return_value=config), patch("pipeline.api.main.open_db", return_value=db), patch("pipeline.api.main.ensure_search_indexes"), TestClient(app) as client:
        project = client.post("/lab/projects", json={"name": "Music"}).json()
        url = f"/lab/projects/{project['id']}"
        first = client.post(url + "/track", data={"base_revision": 1}, files={"file": ("original.wav", original)}).json()
        document = {**first["document"], "brief": "Keep my creative direction", "analysis": _interpretation(0, 3),
                    "rhythm": {"beats": [0, 1, 2]}, "clips": [{"id": "locked", "film_id": "film",
                    "source_start": 20, "source_end": 23, "locked": True}]}
        document.update(
            planner_settings={"pacing": "patient", "lyric_treatment": "counterpoint"},
            song_context={"track_id": first["document"]["track"]["id"], "notes": "A return after loss",
                          "lyrics": [{"id": "last-line", "start": 2, "end": 3,
                                      "text": "A supplied paraphrase", "meaning": "Acceptance"}]},
            visual_plan={"arc": "Confinement to open space", "motifs": "Windows", "source": "user"},
        )
        if explicit_timeline:
            ensure_timeline(document)
        saved = client.put(url, json={"base_revision": 2, "document": document})
        assert saved.status_code == 200, saved.text
        before = saved.json()["document"]
        failed = client.post(url + "/track", data={"base_revision": 3}, files={"file": ("bad.wav", b"invalid audio")})
        assert failed.status_code == 422
        assert client.get(url).json()["document"] == before
        response = client.post(url + "/track", data={"base_revision": 3}, files={"file": ("shorter.wav", replacement)})
        assert response.status_code == 200, response.text
        current = response.json()["document"]
        assert current["passage"] == {"start": 0, "end": 1}
        assert current["song_context"] is None and current["visual_plan"] is None
        assert current["planner_settings"] == before["planner_settings"]
        assert current["clips"] == []
        assert current["music_timeline"] is None and current["analysis"] is None and current["rhythm"] is None
        assert current["brief"] == before["brief"]
        assert client.get(f"/lab/tracks/{before['track']['id']}/audio").content == original
        restored = client.post(url + "/restore", json={"base_revision": 4, "revision": 3})
        assert restored.status_code == 200
        assert restored.json()["document"] == before


def test_worker_applies_music_proposal_without_loading_real_models(config, db, store):
    from pipeline.lab.worker import execute_job
    project = store.create_project("Music", "music-sketch")
    store.enqueue("analyze", project["id"], 1)
    job = store.claim()
    proposal = deepcopy(job["document"])
    proposal["analysis"] = {"summary": "Tender and uncertain"}
    with patch("pipeline.lab.music.run_music_job", return_value=proposal):
        result = execute_job(job, config, db, store)
    assert result["status"] == "completed"
    assert result["result"]["applied"] is True
    assert store.get_project(project["id"])["document"]["analysis"] == proposal["analysis"]


def test_render_endpoint_has_separate_inline_and_attachment_delivery(config, db):
    from pipeline.api.main import app
    with patch("pipeline.api.main.load_config", return_value=config), patch("pipeline.api.main.open_db", return_value=db), patch("pipeline.api.main.ensure_search_indexes"), TestClient(app) as client:
        store = app.state.lab
        project = store.create_project("Reel", "visual-rhymes")
        job = store.enqueue("render", project["id"], 1)
        store.claim()
        store.finish(job["id"], result={"output_url": f"/lab/jobs/{job['id']}/output"})
        output = config.paths.assets_dir / "lab" / "renders" / job["id"] / "output.mp4"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"test-render")
        inline = client.get(f"/lab/jobs/{job['id']}/output")
        attachment = client.get(f"/lab/jobs/{job['id']}/output?download=true")
        assert inline.content == attachment.content == b"test-render"
        assert inline.headers["content-disposition"].startswith("inline;")
        assert attachment.headers["content-disposition"].startswith("attachment;")


def test_music_job_respects_external_ingest_resource_lock(config, db, store):
    from pipeline.ingest.locks import global_ingest_lock
    from pipeline.lab.worker import execute_job
    project = store.create_project("Music", "music-sketch")
    store.enqueue("analyze", project["id"], 1)
    job = store.claim()
    with global_ingest_lock(config.paths.assets_dir), patch("pipeline.lab.music.run_music_job") as provider:
        result = execute_job(job, config, db, store)
    assert result["status"] == "failed"
    assert "shared resources" in result["error"]
    provider.assert_not_called()


def test_reimport_missing_original_restores_registered_path_with_other_suffix(store, tmp_path):
    audio = tmp_path / "upload.wav"
    run_process(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=duration=0.2", str(audio)])
    original = audio.read_bytes()
    first = import_track(store, audio, "original.wav")
    Path(first["path"]).unlink()
    replacement = tmp_path / "replacement.upload"
    replacement.write_bytes(original)
    restored = import_track(store, replacement, "renamed.mp3")
    assert restored["path"] == first["path"]
    assert Path(restored["path"]).read_bytes() == original
