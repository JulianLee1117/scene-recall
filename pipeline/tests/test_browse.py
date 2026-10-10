"""Movie-only browsing is scoped, chronological, playable, and inference free."""
import json
from unittest.mock import Mock

from fastapi.testclient import TestClient
import lancedb
import pytest

from pipeline.index.schema import FILMS_SCHEMA, make_frames_schema, make_units_schema
from pipeline.index.snapshot import SearchLibraryUnavailable, capture_snapshot
from pipeline.search import browse


@pytest.fixture
def db(tmp_path):
    database = lancedb.connect(str(tmp_path / "db"))
    database.create_table("films", schema=FILMS_SCHEMA)
    database.create_table("units", schema=make_units_schema(4))
    database.create_table("frames", schema=make_frames_schema(4))
    return database


def add_film(db, film_id="film", count=4, *, registered=True, reverse=False):
    if registered:
        db.open_table("films").add([{
            "film_id": film_id, "title": f"Title {film_id}",
            "path": f"/{film_id}.mkv", "duration": float(count * 10 + 10), "fps": 24.,
        }])
    units, frames = [], []
    for i in range(count):
        identity = f"{film_id}-{i:04d}"
        paths = [f"/{identity}/{j}.webp" for j in range(3)]
        units.append({
            "unit_id": identity, "shot_id": identity, "film_id": film_id,
            "t_start": i * 10., "t_end": i * 10. + 10.,
            "is_representative": True, "caption": f"People in scene {i}",
            "keyframe_paths": json.dumps(paths),
        })
        for j in range(3):
            frames.append({
                "schema_version": 1, "frame_id": f"{identity}::{j}", "film_id": film_id,
                "unit_id": identity, "shot_id": identity, "frame_index": j,
                "timestamp": i * 10. + j * 3. + 1., "path": paths[j],
                "is_representative": True,
            })
    if units:
        db.open_table("units").add(list(reversed(units)) if reverse else units)
        db.open_table("frames").add(list(reversed(frames)) if reverse else frames)


@pytest.mark.parametrize("scope", [None, [], [""], ["  "], ["film", ""], "film"])
def test_empty_or_malformed_scope_never_opens_library(config, scope):
    database = Mock()
    with pytest.raises(ValueError, match="Select at least one movie"):
        browse.browse_scenes(database, config, film_ids=scope)
    assert not database.mock_calls


def test_scoped_chronology_is_independent_of_scan_order_and_similarity(db, config, monkeypatch):
    add_film(db, "other", 270)
    add_film(db, "selected", 90, reverse=True)
    db.open_table("units").create_scalar_index("film_id", index_type="BTREE")
    db.open_table("frames").create_scalar_index("film_id", index_type="BTREE")
    for name in ("embed_text", "embed_semantic_query", "embed_pil_images", "embed_spatial_images"):
        monkeypatch.setattr("pipeline.search.retrieve." + name, Mock(side_effect=AssertionError("inference forbidden")))
    before = {name: db.open_table(name).version for name in ("films", "units", "frames")}
    result = browse.browse_scenes(db, config, film_ids=["selected"], result_limit=3)
    assert [row["unit_id"] for row in result] == [f"selected-{i:04d}" for i in range(3)]
    assert [row["matched_frame_timestamp"] for row in result] == [4., 14., 24.]
    assert all(row["matched_frame_index"] == 1 for row in result)
    assert result[0]["keyframe_url"] == result[0]["matched_frame_url"] == "/media/keyframe/selected-0000/1"
    assert result[0]["preview_url"] == "/media/preview/selected-0000"
    assert result[0]["debug"] == {"mode": "browse", "final_score": 0., "channels": {}}
    assert before == {name: db.open_table(name).version for name in before}


def test_selected_film_order_is_stable_and_unknown_or_unpublished_films_are_omitted(db, config):
    add_film(db, "a", 2)
    add_film(db, "b", 2)
    add_film(db, "unpublished", 2, registered=False)
    add_film(db, "no-units", 0)
    result = browse.browse_scenes(db, config, film_ids=["missing", "unpublished", "no-units", "b", "a", "b"])
    assert [row["unit_id"] for row in result] == ["b-0000", "b-0001", "a-0000", "a-0001"]
    assert [row["rank"] for row in result] == [1, 2, 3, 4]


def test_filters_junk_and_hidden_units_but_does_not_impose_a_duration_floor(db, config):
    add_film(db, count=5)
    units = db.open_table("units")
    units.update(where="unit_id = 'film-0000'", values={"is_representative": False})
    units.update(where="unit_id = 'film-0001'", values={"caption": "Opening credits over a black background"})
    # Dialogue mentions never establish visual junk; use an ordinary caption.
    units.update(where="unit_id = 'film-0002'", values={"caption": "Two people sit at a table", "dialogue": '["the end credits"]'})
    units.update(where="unit_id = 'film-0003'", values={"t_end": 30.2})
    db.open_table("frames").update(where="frame_id = 'film-0003::1'", values={"timestamp": 30.1})
    result = browse.browse_scenes(db, config, film_ids=["film"])
    assert [row["unit_id"] for row in result] == ["film-0002", "film-0003", "film-0004"]


def test_missing_and_mismatched_frames_refill_the_prefix_past_a_full_chunk(db, config):
    add_film(db, count=80)
    frames = db.open_table("frames")
    # More than a candidate chunk has unusable evidence. A short page must not
    # falsely report exhaustion while later valid scenes exist.
    frames.delete("timestamp < 650")
    frames.update(where="unit_id = 'film-0065'", values={"path": "/wrong.webp"})
    frames.update(where="unit_id = 'film-0066'", values={"shot_id": "different-shot"})
    frames.update(where="unit_id = 'film-0067'", values_sql={"timestamp": "CAST('NaN' AS DOUBLE)"})
    frames.update(where="unit_id = 'film-0068'", values={"timestamp": 690.})
    frames.create_scalar_index("film_id", index_type="BTREE")
    frames.create_scalar_index("unit_id", index_type="BTREE")
    result = browse.browse_scenes(db, config, film_ids=["film"], result_limit=2)
    assert [row["unit_id"] for row in result] == ["film-0069", "film-0070"]


def test_missing_middle_frame_uses_an_actual_alternative_sample(db, config):
    add_film(db, count=1)
    db.open_table("frames").delete("frame_index = 1")
    result = browse.browse_scenes(db, config, film_ids=["film"])
    assert result[0]["matched_frame_index"] == 2
    assert result[0]["matched_frame_timestamp"] == 7.
    assert result[0]["keyframe_url"] == "/media/keyframe/film-0000/2"


def test_one_snapshot_keeps_units_and_frames_consistent_through_new_publication(db, config, monkeypatch):
    add_film(db, count=1)
    calls = []
    def capture_then_publish(cfg, database):
        snapshot = capture_snapshot(cfg, database)
        calls.append(snapshot)
        database.open_table("units").update(where="unit_id = 'film-0000'", values={"caption": "New caption"})
        database.open_table("frames").update(where="frame_index = 1", values={"timestamp": 5.})
        return snapshot
    monkeypatch.setattr(browse, "acquire_search_snapshot", capture_then_publish)
    result = browse.browse_scenes(db, config, film_ids=["film"])
    assert len(calls) == 1
    assert result[0]["caption"] == "People in scene 0"
    assert result[0]["matched_frame_timestamp"] == 4.
    monkeypatch.setattr(browse, "acquire_search_snapshot", Mock(side_effect=AssertionError("already pinned")))
    assert browse.browse_scenes(calls[0], config, film_ids=["film"]) == result


@pytest.fixture
def client(db, config, monkeypatch):
    from pipeline.api.main import app
    # Exercise routing without startup, background jobs, or the encoder warmup.
    monkeypatch.setattr(app.state, "db", db, raising=False)
    monkeypatch.setattr(app.state, "config", config, raising=False)
    monkeypatch.setattr(app.state, "encoder_ready", False, raising=False)
    monkeypatch.setattr(app.state, "film_titles_version", None, raising=False)
    monkeypatch.setattr(app.state, "film_titles", {}, raising=False)
    return TestClient(app)


def test_api_required_scope_paging_titles_and_warmup_independence(client, db):
    add_film(db, count=5)
    assert client.get("/library/scenes").status_code == 422
    assert client.get("/library/scenes", params={"film_id": ""}).status_code == 422
    first = client.get("/library/scenes", params={"film_id": "film", "limit": 2})
    assert first.status_code == 200
    payload = first.json()
    assert payload["limit"] == 2 and payload["has_more"] is True and payload["next_limit"] == 4
    assert payload["results"][0]["film_title"] == "Title film"
    second = client.get("/library/scenes", params={"film_id": "film", "limit": 4}).json()
    assert second["results"][:2] == payload["results"]
    last = client.get("/library/scenes", params={"film_id": "film", "limit": 8}).json()
    assert len(last["results"]) == 5 and last["has_more"] is False and last["next_limit"] is None
    for limit in (0, 201):
        assert client.get("/library/scenes", params={"film_id": "film", "limit": limit}).status_code == 422


def test_api_cap_and_publication_busy_are_normal_search_envelopes(client, db, config, monkeypatch):
    add_film(db, count=6)
    config.retrieval.max_result_limit = 4
    response = client.get("/library/scenes", params={"film_id": "film", "limit": 4})
    assert response.status_code == 200
    assert len(response.json()["results"]) == 4
    assert response.json()["max_limit"] == 4 and response.json()["has_more"] is False
    monkeypatch.setattr(browse, "acquire_search_snapshot", Mock(side_effect=SearchLibraryUnavailable("publishing")))
    response = client.get("/library/scenes", params={"film_id": "film", "limit": 2})
    assert response.status_code == 503 and response.headers["Retry-After"] == "1"


def test_shot_at_finds_the_shot_on_screen_as_a_result(db, config):
    add_film(db, count=4)
    shot = browse.shot_at(db, config, film_id="film", time_value=25.0)
    assert shot["unit_id"] == "film-0002" and shot["t_start"] == 20.0 and shot["t_end"] == 30.0
    assert shot["keyframe_url"].startswith("/media/keyframe/film-0002/")
    assert browse.shot_at(db, config, film_id="film", time_value=20.0)["unit_id"] == "film-0002", "a shot owns its first instant"
    assert browse.shot_at(db, config, film_id="film", time_value=40.0) is None, "past the last shot"
    assert browse.shot_at(db, config, film_id="other", time_value=5.0) is None


def test_api_shot_at_a_time(client, db):
    add_film(db, count=3)
    found = client.get("/library/shot", params={"film_id": "film", "t": 12.5})
    assert found.status_code == 200
    assert found.json()["unit_id"] == "film-0001" and found.json()["film_title"] == "Title film"
    assert client.get("/library/shot", params={"film_id": "film", "t": 99}).status_code == 404
    assert client.get("/library/shot", params={"film_id": "film", "t": -1}).status_code == 422
