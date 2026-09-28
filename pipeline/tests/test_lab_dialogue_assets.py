"""Shared original/focused PCM, immutable ranges, source changes and overlaps."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import time
from unittest.mock import patch

import numpy as np
import pytest
from fastapi.testclient import TestClient
from filelock import FileLock

from pipeline.index.writer import create_tables, open_db
from pipeline.ingest.probe import _content_hash
from pipeline.lab.audio_mix import music_gain_at
from pipeline.lab.cleanup import collect_garbage
from pipeline.lab.dialogue_assets import PROFILE, audio_asset_path, cache_root, prepare_dialogue_audio
from pipeline.lab.media import render_from_manifest, render_manifest, run_process
from pipeline.lab.models import ProjectDocument
from pipeline.lab.store import LabStore
from pipeline.tests.test_lab_dialogue import audio_scene, _samples, _tone


@pytest.fixture
def surround(config, tmp_path):
    path = tmp_path / "surround.mkv"
    tones = "|".join(f"0.08*sin(2*PI*{frequency}*t)" for frequency in (250, 350, 1100, 50, 450, 550))
    run_process(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=s=32x32:r=24:d=4",
                 "-f", "lavfi", "-i", f"aevalsrc={tones}:s=48000:d=4:c=5.1",
                 "-c:v", "libx264", "-c:a", "flac", str(path)])
    db = open_db(config)
    create_tables(db, vector_dim=4)
    identity = _content_hash(path)
    db.open_table("films").add([{"film_id": identity, "path": str(path), "title": "Surround", "duration": 4., "fps": 24.}])
    return config, db, path, {"film_id": identity, "source_start": .5, "source_end": 2.5, "source_audio_mode": "voice_focus"}


def test_real_center_focus_removes_other_channels_and_cache_is_the_browser_pcm(surround):
    from pipeline.api.main import app
    config, db, source, request = surround
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    focused = prepare_dialogue_audio(config, db, request)
    unprocessed = prepare_dialogue_audio(config, db, {**request, "source_audio_mode": "original"})
    assert focused["channel_method"] == "center"
    assert unprocessed["channel_method"] == "original_mix"
    assert focused["asset_id"] != unprocessed["asset_id"]
    pcm = audio_asset_path(config, focused["asset_id"])
    clean, full = _samples(pcm), _samples(audio_asset_path(config, unprocessed["asset_id"]))
    assert _tone(clean, .2, 1.8, 1100) > .045
    for frequency in (250, 350, 450, 550):
        assert _tone(full, .2, 1.8, frequency) > .01
        assert _tone(clean, .2, 1.8, frequency) < .0001
    with patch("pipeline.lab.media.run_process", side_effect=AssertionError("cache hit must not encode/probe")):
        assert prepare_dialogue_audio(config, db, request) == focused
    with patch("pipeline.api.main.load_config", return_value=config), patch("pipeline.api.main.open_db", return_value=db), patch("pipeline.api.main.ensure_search_indexes"), TestClient(app) as client:
        response = client.get("/lab/dialogue-audio", params=request)
        assert response.status_code == 200, response.text
        assert response.content == pcm.read_bytes()
        assert response.history[0].status_code == 307
        ranged = client.get("/lab/dialogue-audio", params=request, headers={"Range": "bytes=0-63"})
        assert ranged.status_code == 206 and ranged.content == response.content[:64]
        assert ranged.headers["etag"] == f'"{focused["asset_id"]}"'
        assert client.get("/lab/dialogue-audio", params={**request, "source_end": 20}).status_code == 422
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original


def test_fingerprint_changes_invalidate_lookup_and_old_asset_bytes_stay_immutable(surround):
    config, db, source, request = surround
    first = prepare_dialogue_audio(config, db, request)
    first_bytes = audio_asset_path(config, first["asset_id"]).read_bytes()
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    second = prepare_dialogue_audio(config, db, request)
    assert second["identity"] != first["identity"]
    # Recreating the same original bytes yields the same immutable representation.
    assert second["asset_id"] == first["asset_id"]
    assert audio_asset_path(config, first["asset_id"]).read_bytes() == first_bytes
    with source.open("ab") as handle:
        handle.write(b"source changed")
    with pytest.raises(ValueError, match="source changed"):
        prepare_dialogue_audio(config, db, request)
    assert audio_asset_path(config, first["asset_id"]).read_bytes() == first_bytes
    with audio_asset_path(config, first["asset_id"]).open("ab") as handle:
        handle.write(b"asset changed")
    with pytest.raises(ValueError, match="asset changed"):
        audio_asset_path(config, first["asset_id"])


def test_stereo_fallback_is_explicit_and_focused_export_uses_the_same_pcm(audio_scene):
    config, db, store, document, _, _ = audio_scene
    document["dialogue_clips"][0].update(source_audio_mode="voice_focus", gain_db=6,
                                        fade_in_seconds=0, fade_out_seconds=0)
    document["music_gain_db"] = -60
    manifest = render_manifest(document, db, store)
    clip = manifest["dialogue_clips"][0]
    assert clip["audio_channel_method"] == "filtered_mix"
    asset = prepare_dialogue_audio(config, db, clip)
    browser_pcm = _samples(audio_asset_path(config, asset["asset_id"]))
    outputs = []
    for mode in ("preview", "export"):
        resolved = render_manifest(document, db, store, mode=mode)
        resolved.update(width=96, height=54)
        render_from_manifest(mode, resolved, config, db, store, lambda _: None)
        directory = config.paths.assets_dir / "lab/renders" / mode
        receipt = json.loads((directory / "dialogue-assets.json").read_text())
        assert receipt[0]["asset_id"] == asset["asset_id"]
        outputs.append(_samples(directory / "output.mp4"))
    assert np.array_equal(outputs[0], outputs[1])
    # Compare actual browser source PCM against the resulting gain-scaled export,
    # excluding the separately encoded AAC edges. There is no second filter pass.
    offset = round((clip["start"] - document["passage"]["start"]) * 48000)
    expected = browser_pcm * 10 ** (clip["gain_db"] / 20)
    encoded = outputs[0][offset:offset + len(expected)]
    assert np.abs(encoded[2400:-2400] - expected[2400:-2400]).mean() < .003


def test_overlapping_voices_mix_independently_and_duck_uses_minimum_envelope(audio_scene):
    config, db, store, document, _, _ = audio_scene
    second = {**document["dialogue_clips"][0], "id": "second", "start": 2., "source_start": .2,
              "source_end": 1.7, "music_duck_db": -6, "duck_attack_seconds": .6, "duck_release_seconds": 1.2}
    document["dialogue_clips"].append(second)
    document = ProjectDocument.model_validate(document).model_dump(mode="json")
    assert music_gain_at(document, 2.2) == pytest.approx(10 ** (-12 / 20))
    manifest = render_manifest(document, db, store)
    manifest.update(width=96, height=54)
    render_from_manifest("overlap", manifest, config, db, store, lambda _: None)
    samples = _samples(config.paths.assets_dir / "lab/renders/overlap/output.mp4")
    assert _tone(samples, 1.65, 1.95, 700) > .04
    assert _tone(samples, 1.65, 1.95, 1100) > .04
    assert _tone(samples, 1.65, 1.95, 440) / _tone(samples, .1, .4, 440) == pytest.approx(10 ** (-12 / 20), abs=.02)
    assert music_gain_at(document, 1.5) == pytest.approx(10 ** (-12 / 20))
    instant = deepcopy(document)
    instant["dialogue_clips"] = [{**second, "duck_attack_seconds": 0, "duck_release_seconds": 0}]
    assert music_gain_at(instant, 1.999) == 1
    assert music_gain_at(instant, 2) == pytest.approx(10 ** (-6 / 20))
    assert music_gain_at(instant, 3.5) == 1
    assert music_gain_at(instant, 3.501) == 1


def test_idle_collection_reclaims_only_stale_unlocked_pcm_and_rebuilds(surround):
    config, db, _, request = surround
    store = LabStore(config.paths.state_dir, assets_dir=config.paths.assets_dir)
    store.initialize()
    asset = prepare_dialogue_audio(config, db, request)
    path = audio_asset_path(config, asset["asset_id"])
    old = time.time() - 90000
    os.utime(path, (old, old))
    with FileLock(path.with_suffix(".lock"), timeout=0, preserve_lock_file=True):
        assert collect_garbage(store, apply=True)["files"] == []
    result = collect_garbage(store, apply=True)
    assert not result["errors"] and not path.exists()
    assert result["files"][0]["path"] == str(path)
    rebuilt = prepare_dialogue_audio(config, db, request)
    assert rebuilt["asset_id"] == asset["asset_id"] and path.is_file()
    assert collect_garbage(store, apply=True)["files"] == []


def test_queued_legacy_audio_defaults_do_not_count_as_generated_changes(config):
    store = LabStore(config.paths.state_dir)
    store.initialize()
    document = ProjectDocument(track={"id": "song", "name": "Song", "duration": 30},
                               dialogue_clips=[{"id": "line", "film_id": "film", "source_start": 1,
                                                "source_end": 3, "start": 2}]).model_dump(mode="json")
    project = store.create_project("Legacy", "music-sketch", document)
    queued = store.enqueue("draft", project["id"], 1)
    snapshot = store.get_job(queued["id"], private=True)["snapshot"]
    for field in ("source_audio_mode", "duck_attack_seconds", "duck_release_seconds"):
        snapshot["document"]["dialogue_clips"][0].pop(field)
    with store.connection() as con:
        con.execute("UPDATE jobs SET snapshot=? WHERE id=?", (json.dumps(snapshot), queued["id"]))
    store.claim()
    result = store.finish(queued["id"], document={**snapshot["document"], "brief": "Picture changes only"})
    assert result["result"]["applied"] is True
    clip = store.get_project(project["id"])["document"]["dialogue_clips"][0]
    assert clip["source_audio_mode"] == "original"
    assert (clip["duck_attack_seconds"], clip["duck_release_seconds"]) == (.25, .5)
