"""Independent source audio survives picture edits, revisions and rendered excerpts."""

from copy import deepcopy
import hashlib
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from pipeline.index.writer import create_tables, open_db
from pipeline.ingest.probe import _content_hash
from pipeline.lab.audio_mix import music_gain_at
from pipeline.lab.media import import_track, render_from_manifest, render_manifest, run_process, validate_sources
from pipeline.lab.models import ProjectDocument
from pipeline.lab.next_scene_media import preview_manifest
from pipeline.lab.store import LabStore


def _dialogue(**updates):
    return {"id": "line", "film_id": "film", "source_start": 1., "source_end": 2., "start": 11., **updates}


def _document(**updates):
    return ProjectDocument(track={"id": "song", "name": "Song", "duration": 20},
                           passage={"start": 10, "end": 14}, **updates).model_dump(mode="json")


@pytest.mark.parametrize("change", [
    {"dialogue_clips": [_dialogue(source_end=1)]},
    {"dialogue_clips": [_dialogue(start=9.9)]},
    {"dialogue_clips": [_dialogue(start=13.5)]},
    {"dialogue_clips": [_dialogue(duck_attack_seconds=-.1)]},
    {"dialogue_clips": [_dialogue(duck_release_seconds=5.1)]},
    {"dialogue_clips": [_dialogue(), _dialogue(start=12)]},
    {"dialogue_clips": [_dialogue(gain_db=24.1)]},
    {"dialogue_clips": [_dialogue(music_duck_db=-61)]},
    {"dialogue_clips": [_dialogue(source_start=float("nan"))]},
    {"dialogue_clips": [_dialogue(path="invented.mp4")]},
    {"audio_fade_out_seconds": 4.1},
    {"music_gain_db": 1},
])
def test_invalid_audio_arrangements_are_rejected(change):
    with pytest.raises(ValueError):
        _document(**change)


def test_duck_envelopes_use_song_clock_minimum_gain_and_multiplicative_fades():
    doc = _document(dialogue_clips=[_dialogue(music_duck_db=-12), _dialogue(id="second", start=12.1, music_duck_db=-6)],
                    music_gain_db=-6, audio_fade_in_seconds=.5, audio_fade_out_seconds=1)
    base, first, second = 10 ** (-6 / 20), 10 ** (-12 / 20), 10 ** (-6 / 20)
    assert music_gain_at(doc, 10.25) == pytest.approx(base * .5)
    assert music_gain_at(doc, 10.875) == pytest.approx(base * (1 + first) / 2)
    assert music_gain_at(doc, 11.5) == pytest.approx(base * first)
    # First release overlaps the second attack: choose the quieter envelope.
    assert music_gain_at(doc, 12.05) == pytest.approx(base * min(first + (1 - first) * .1, second + (1 - second) * .2))
    assert music_gain_at(doc, 13.75) == pytest.approx(base * .25)
    assert music_gain_at(doc, 14) == 0


@pytest.fixture
def audio_scene(config, tmp_path):
    film, song = tmp_path / "film.mkv", tmp_path / "song.wav"
    # Speech stand-in changes frequency at source second2; incorrect trim offsets
    # or automatic picture audio would therefore yield the wrong frequency.
    run_process(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=96x54:r=24:d=6",
                 "-f", "lavfi", "-i", "aevalsrc=0.15*sin(2*PI*if(lt(t\\,2)\\,700\\,1100)*t):s=48000:d=6",
                 "-c:v", "libx264", "-c:a", "pcm_s16le", str(film)])
    run_process(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6", str(song)])
    identity = _content_hash(film)
    db = open_db(config)
    create_tables(db, vector_dim=4)
    db.open_table("films").add([{"film_id": identity, "title": "Source", "path": str(film), "duration": 6., "fps": 24.}])
    store = LabStore(config.paths.state_dir)
    store.initialize()
    track = import_track(store, song, "song.wav")
    passage = {"start": .5, "end": 4.5}
    clips = [{"id": f"picture{index}", "film_id": identity, "source_start": 0., "source_end": 1.} for index in range(4)]
    slots = [{"id": f"slot{index}", "start": .5 + index, "end": 1.5 + index, "section_index": 0,
              "clip_id": clip["id"]} for index, clip in enumerate(clips)]
    doc = ProjectDocument(track={key: track[key] for key in ("id", "name", "duration")}, passage=passage,
                          clips=clips, music_timeline={"track_id": track["id"], "passage": passage, "slots": slots},
                          audio_fade_out_seconds=.5,
                          dialogue_clips=[_dialogue(film_id=identity, source_start=2.2, source_end=3.7, start=1.25, music_duck_db=-12)]).model_dump()
    return config, db, store, doc, film, Path(track["path"])


def _samples(path):
    return np.frombuffer(run_process(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0", "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1"]), dtype=np.float32)


def _tone(samples, start, end, frequency):
    window = samples[round(start * 48000):round(end * 48000)]
    spectrum = np.abs(np.fft.rfft(window))
    frequencies = np.fft.rfftfreq(len(window), 1 / 48000)
    return max(spectrum[np.abs(frequencies - frequency) < 10]) / len(window)


@pytest.mark.parametrize("voice_gain", [0, 6])
def test_real_mix_preserves_source_timing_crosses_cuts_and_matches_export_and_excerpt(audio_scene, voice_gain):
    config, db, store, doc, film, song = audio_scene
    doc["dialogue_clips"][0]["gain_db"] = voice_gain
    source_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (film, song)}
    manifests = {"preview": render_manifest(doc, db, store), "export": render_manifest(doc, db, store, mode="export"),
                 "pair": preview_manifest(doc, "slot1", db, store)}
    assert manifests["preview"]["music"] == manifests["export"]["music"]
    assert manifests["preview"]["dialogue_clips"] == manifests["export"]["dialogue_clips"] == manifests["pair"]["dialogue_clips"]
    result = {}
    for name, manifest in manifests.items():
        # Geometry is independently covered by reel tests; audio parity does not
        # require repeatedly encoding large pictures in this regression.
        manifest.update(width=96, height=54)
        render_from_manifest(name, manifest, config, db, store, lambda _: None)
        result[name] = _samples(config.paths.assets_dir / "lab" / "renders" / name / "output.mp4")
    full = result["preview"]
    assert np.array_equal(full, result["export"])
    # Source1100Hz spans the picture cut at1s and2s, and never leaks beforehand.
    assert _tone(full, .05, .4, 1100) < .0001
    assert _tone(full, .95, 1.2, 1100) > .04
    assert _tone(full, 1.95, 2.1, 1100) > .04
    assert _tone(full, 1.3, 1.7, 700) < .0001
    assert _tone(full, 1.3, 1.7, 1100) == pytest.approx(.075 * 10 ** (voice_gain / 20), abs=.003)
    before, under = _tone(full, .1, .4, 440), _tone(full, 1.3, 1.7, 440)
    assert under / before == pytest.approx(10 ** (-12 / 20), abs=.02)
    assert _tone(full, 3.85, 3.95, 440) < before * .3
    # Pair is passage seconds1..3, retaining dialogue already underway at1s.
    expected, excerpt = full[48000:144000], result["pair"][:96000]
    assert np.abs(expected[2400:-2400] - excerpt[2400:-2400]).mean() < .003
    assert np.corrcoef(expected[2400:-2400], excerpt[2400:-2400])[0, 1] > .995
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in source_hashes.items())


def test_source_bounds_and_missing_audio_fail_before_render(audio_scene, tmp_path):
    _, db, store, doc, _, _ = audio_scene
    invalid = deepcopy(doc)
    invalid["dialogue_clips"][0].update(source_start=5, source_end=6.5)
    with pytest.raises(ValueError, match="beyond"):
        validate_sources(invalid, db, store)
    silent = tmp_path / "silent.mp4"
    run_process(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=s=32x32:d=4", "-an", "-c:v", "libx264", str(silent)])
    identity = _content_hash(silent)
    db.open_table("films").add([{"film_id": identity, "title": "Silent", "path": str(silent), "duration": 4., "fps": 25.}])
    doc["dialogue_clips"][0]["film_id"] = identity
    with pytest.raises(ValueError, match="has no audio"):
        render_manifest(doc, db, store)


def test_replaced_source_and_changed_prepared_identity_are_rejected(audio_scene):
    config, db, store, doc, film, _ = audio_scene
    manifest = render_manifest(doc, db, store)
    with film.open("ab") as handle:
        handle.write(b"changed")
    with pytest.raises(ValueError, match="changed after"):
        render_from_manifest("changed", manifest, config, db, store, lambda _: None)
    with pytest.raises(ValueError, match="source changed"):
        render_manifest(doc, db, store)


def test_audio_revisions_are_durable_and_generation_cannot_rewrite_them(config):
    store = LabStore(config.paths.state_dir)
    store.initialize()
    doc = _document(dialogue_clips=[_dialogue()])
    project = store.create_project("Dialogue", "music-sketch", doc)
    saved = store.update_project(project["id"], 1, {**doc, "music_gain_db": -6})
    assert LabStore(config.paths.state_dir).get_project(project["id"])["document"]["music_gain_db"] == -6
    store.enqueue("draft", project["id"], saved["revision"])
    job = store.claim()
    changed = {**job["document"], "dialogue_clips": []}
    with pytest.raises(ValueError, match="audio arrangement"):
        store.finish(job["id"], document=changed)
    result = store.finish(job["id"], document={**job["document"], "brief": "New picture idea"})
    assert result["result"]["applied"]
    restored = store.restore(project["id"], 3, 1)
    assert restored["document"]["dialogue_clips"] == doc["dialogue_clips"]
    assert restored["document"]["music_gain_db"] == 0


def test_long_generation_work_scopes_keep_audio_only_on_final_document():
    from pipeline.lab.long_generation import _part_document
    doc = _document(dialogue_clips=[_dialogue()], audio_fade_out_seconds=4)
    part = _part_document(doc, {"start": 12, "end": 14}, {})
    assert ProjectDocument.model_validate(part).dialogue_clips == []
    assert part["audio_fade_out_seconds"] == 0
    assert doc["dialogue_clips"] and doc["audio_fade_out_seconds"] == 4


def test_audio_source_offset_is_preserved_instead_of_pulled_to_clip_start(audio_scene, tmp_path):
    config, db, store, doc, _, _ = audio_scene
    film = tmp_path / "offset.mkv"
    run_process(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=s=32x32:r=24:d=4",
                 "-itsoffset", "1", "-f", "lavfi", "-i", "sine=frequency=1100:sample_rate=48000:duration=3",
                 "-c:v", "libx264", "-c:a", "pcm_s16le", str(film)])
    identity = _content_hash(film)
    db.open_table("films").add([{"film_id": identity, "title": "Offset", "path": str(film), "duration": 4., "fps": 24.}])
    doc["music_gain_db"] = -60
    doc["dialogue_clips"] = [_dialogue(film_id=identity, source_start=.5, source_end=2., start=.5, fade_in_seconds=0, fade_out_seconds=0)]
    manifest = render_manifest(doc, db, store)
    manifest.update(width=96, height=54)
    render_from_manifest("offset", manifest, config, db, store, lambda _: None)
    samples = _samples(config.paths.assets_dir / "lab" / "renders" / "offset" / "output.mp4")
    assert _tone(samples, .1, .4, 1100) < .0001
    assert _tone(samples, .7, .9, 1100) > .025


def test_api_preserves_unavailable_saved_dialogue_and_track_replacement_is_undoable(audio_scene):
    from pipeline.api.main import app
    config, db, store, doc, _, song = audio_scene
    with patch("pipeline.api.main.load_config", return_value=config), patch("pipeline.api.main.open_db", return_value=db), patch("pipeline.api.main.ensure_search_indexes"), TestClient(app) as client:
        created = client.post("/lab/projects", json={"name": "Dialogue", "document": doc})
        assert created.status_code == 200, created.text
        project = created.json()
        url = f"/lab/projects/{project['id']}"
        db.open_table("films").delete(f"film_id = '{doc['dialogue_clips'][0]['film_id']}'")
        changed = {**project["document"], "music_gain_db": -4}
        saved = client.put(url, json={"base_revision": 1, "document": changed})
        assert saved.status_code == 200, saved.text
        invalid = deepcopy(changed)
        invalid["dialogue_clips"][0]["source_start"] += .1
        assert client.put(url, json={"base_revision": 2, "document": invalid}).status_code == 422
        replaced = client.post(url + "/track", data={"base_revision": 2}, files={"file": ("song.wav", song.read_bytes(), "audio/wav")})
        assert replaced.status_code == 200, replaced.text
        assert replaced.json()["document"]["dialogue_clips"] == []
        restored = client.post(url + "/restore", json={"base_revision": 3, "revision": 2})
        assert restored.status_code == 200, restored.text
        assert restored.json()["document"]["dialogue_clips"] == project["document"]["dialogue_clips"]
