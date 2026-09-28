"""Entering an experiment and auditioning music do not create saved projects."""

import hashlib
import io
import json
import sqlite3
import wave
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pipeline.index.writer import create_tables, open_db
from pipeline.lab.api import router
from pipeline.lab.models import ProjectDocument
from pipeline.lab.store import LabStore


@pytest.fixture
def store(tmp_path):
    result = LabStore(tmp_path / "state")
    result.initialize()
    return result


@pytest.fixture
def client(config, store):
    app = FastAPI()
    app.state.lab = store
    app.state.db = open_db(config)
    create_tables(app.state.db, vector_dim=4)
    app.state.db.open_table("films").add([
        {"film_id": "film", "title": "Film", "path": "unavailable.mkv", "duration": 10., "fps": 24.},
    ])
    app.include_router(router)
    with TestClient(app) as result:
        yield result


def ledger_counts(store):
    with store.connection() as con:
        return {table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("projects", "revisions", "jobs", "tracks")}


@pytest.mark.parametrize("experiment,name", [
    ("music-sketch", "Untitled music edit"),
    ("visual-rhymes", "Untitled Match Cuts"),
])
def test_draft_defaults_are_read_only_and_do_not_require_any_store(experiment, name):
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        first = client.get(f"/lab/experiments/{experiment}/draft")
        second = client.get(f"/lab/experiments/{experiment}/draft")
    assert first.status_code == 200
    assert second.json() == first.json() == {
        "id": "", "revision": 0, "experiment_id": experiment, "name": name,
        "document": ProjectDocument().model_dump(mode="json"), "created_at": 0, "updated_at": 0,
    }


def test_opening_drafts_preserves_existing_project_and_job_ledger(client, store):
    saved = store.create_project("Existing edit", "music-sketch")
    store.enqueue("rhythm", saved["id"], 1)
    before = ledger_counts(store)
    for experiment in ("music-sketch", "visual-rhymes", "unavailable-experiment"):
        response = client.get(f"/lab/experiments/{experiment}/draft")
        assert response.status_code == (404 if experiment.startswith("unavailable") else 200)
    assert ledger_counts(store) == before
    assert store.get_project(saved["id"]) == saved


def test_registry_separates_entry_routes_from_saved_project_routes(client):
    experiments = {row["id"]: row for row in client.get("/lab/experiments").json()["experiments"]}
    assert experiments["music-sketch"]["route"] == experiments["music-sketch"]["project_route"] == "/lab/music-sketch"
    assert experiments["music-sketch"]["persistence"] == "project"
    assert experiments["visual-rhymes"]["route"] == "/match"
    assert experiments["visual-rhymes"]["project_route"] == "/lab/visual-rhymes"
    assert experiments["visual-rhymes"]["persistence"] == "session"


@pytest.mark.parametrize("document,status", [
    ({"clips": [{"id": "clip", "film_id": "missing", "source_start": 0, "source_end": 3}]}, 422),
    ({"clips": [{"id": "clip", "film_id": "film", "source_start": 0, "source_end": 11}]}, 422),
    ({"clips": [{"id": "clip", "film_id": "film", "source_start": 3, "source_end": 2}]}, 422),
    ({"track": {"id": "invented", "name": "Track", "duration": 30}}, 404),
    ({"server_path": "untrusted"}, 422),
])
def test_invalid_initial_document_never_creates_project_or_revision(client, store, document, status):
    before = ledger_counts(store)
    result = client.post("/lab/projects", json={"name": "First save", "document": document})
    assert result.status_code == status
    assert ledger_counts(store) == before


def test_first_save_is_one_complete_revision_and_omitted_document_remains_supported(client, store):
    document = ProjectDocument(
        brief="Two people crossing the frame",
        clips=[{"id": "clip", "film_id": "film", "source_start": 1, "source_end": 4}],
        aspect_ratio="9:16",
    ).model_dump(mode="json")
    result = client.post("/lab/projects", json={
        "name": "First meaningful edit", "experiment_id": "visual-rhymes", "document": document,
    })
    assert result.status_code == 200
    project = result.json()
    assert project["revision"] == 1
    assert project["document"] == document
    assert store.get_project(project["id"]) == project
    with store.connection() as con:
        revisions = con.execute("SELECT * FROM revisions WHERE project_id=?", (project["id"],)).fetchall()
    assert len(revisions) == 1
    assert revisions[0]["name"] == project["name"]
    assert json.loads(revisions[0]["document"]) == document
    assert ledger_counts(store) == {"projects": 1, "revisions": 1, "jobs": 0, "tracks": 0}
    old_client = client.post("/lab/projects", json={"name": "Explicit blank creation"})
    assert old_client.status_code == 200
    assert old_client.json()["document"] == ProjectDocument().model_dump(mode="json")


def test_initial_revision_failure_rolls_back_project_together(store):
    with store.connection() as con:
        con.executescript("""
            CREATE TRIGGER reject_revision BEFORE INSERT ON revisions
            BEGIN SELECT RAISE(ABORT, 'revision rejected'); END;
        """)
    with pytest.raises(sqlite3.IntegrityError, match="revision rejected"):
        store.create_project("Must not survive", "music-sketch", {"brief": "Initial work"})
    assert ledger_counts(store) == {"projects": 0, "revisions": 0, "jobs": 0, "tracks": 0}


def audio_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\x00\x00" * 8000)
    return output.getvalue()


def test_projectless_audio_import_preserves_original_and_creates_no_edit(client, store):
    content = audio_bytes()
    response = client.post("/lab/tracks", files={"file": ("Song.wav", content, "audio/wav")})
    assert response.status_code == 200
    track = response.json()
    assert track == {"id": hashlib.sha256(content).hexdigest(), "name": "Song.wav", "duration": 1}
    assert ledger_counts(store) == {"projects": 0, "revisions": 0, "jobs": 0, "tracks": 1}
    source = store.get_track(track["id"])
    assert Path(source["path"]).read_bytes() == content
    assert client.get(f"/lab/tracks/{track['id']}/audio").content == content
    assert not list((store.root / "tracks").glob("*.upload"))
    # Saving the applied passage writes that complete state as revision one.
    result = client.post("/lab/projects", json={"document": {"track": track, "passage": {"start": 0, "end": 1}}})
    assert result.status_code == 200
    assert result.json()["revision"] == 1
    assert result.json()["document"]["track"] == track


@pytest.mark.parametrize("failure,expected", [("empty", 422), ("oversize", 413), ("invalid", 422)])
def test_failed_projectless_track_import_cleans_temporary_and_preserves_originals(client, store, monkeypatch, failure, expected):
    original = store.root / "tracks" / "preserve.wav"
    original.write_bytes(b"Original evidence")
    store.add_track("preserve", "Prior track", 60, original)
    before = ledger_counts(store)
    content = b""
    if failure == "oversize":
        monkeypatch.setattr("pipeline.lab.api.MAX_TRACK_BYTES", 3)
        content = b"1234"
    elif failure == "invalid":
        monkeypatch.setattr("pipeline.lab.media.probe_media", lambda _: {"streams": [{"codec_type": "video"}]})
        content = b"Not audio"
    result = client.post("/lab/tracks", files={"file": ("Track.wav", content, "audio/wav")})
    assert result.status_code == expected
    assert ledger_counts(store) == before
    assert original.read_bytes() == b"Original evidence"
    assert not list((store.root / "tracks").glob("*.upload"))
