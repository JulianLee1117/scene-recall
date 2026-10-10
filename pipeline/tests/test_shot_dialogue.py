"""Shot details read bounded, source-timed evidence without running retrieval."""
from unittest.mock import Mock

from fastapi.testclient import TestClient
import lancedb
import pytest

from pipeline.evidence.tables import dialogue_lines_schema
from pipeline.index.schema import FILMS_SCHEMA, make_units_schema
from pipeline.index.snapshot import SearchLibraryUnavailable, capture_snapshot
from pipeline.search import dialogue


@pytest.fixture
def db(tmp_path):
    database = lancedb.connect(str(tmp_path / "db"))
    database.create_table("films", schema=FILMS_SCHEMA)
    database.create_table("units", schema=make_units_schema(4))
    database.create_table("dialogue_lines", schema=dialogue_lines_schema())
    database.open_table("films").add([
        {"film_id": "film", "title": "Film", "path": "/film.mkv", "duration": 30., "fps": 24.},
    ])
    database.open_table("units").add([
        {"unit_id": "shot", "shot_id": "shot", "film_id": "film", "t_start": 10., "t_end": 20.,
         "is_representative": True},
    ])
    return database


def add_lines(db, lines):
    db.open_table("dialogue_lines").add([
        {"schema_version": 1, "line_id": str(index), "film_id": "film", "unit_id": "shot",
         "t_start": 11., "t_end": 12., "text": f"Line {index}", "norm": "line", "source": "sidecar", **line}
        for index, line in enumerate(lines)
    ])


def test_overlap_uses_film_and_time_not_quote_owner_and_keeps_source_times(db, config):
    add_lines(db, [
        {"line_id": "next", "t_start": 19., "t_end": 22., "unit_id": "next-shot", "source": "whisper"},
        {"line_id": "inside", "t_start": 14., "t_end": 16., "text": "  A line in this shot.  "},
        {"line_id": "previous", "t_start": 9., "t_end": 11., "unit_id": "previous-shot"},
        {"line_id": "ends-at-start", "t_start": 8., "t_end": 10.},
        {"line_id": "starts-at-end", "t_start": 20., "t_end": 21.},
        {"line_id": "other-film", "film_id": "other"},
    ])
    # Exercise complete residual filtering with an indexed film predicate too.
    db.open_table("dialogue_lines").create_scalar_index("film_id", index_type="BTREE")
    result = dialogue.shot_dialogue(db, config, unit_id="shot")
    assert result["status"] == "available"
    assert (result["t_start"], result["t_end"]) == (10., 20.)
    assert [(line["line_id"], line["t_start"], line["t_end"]) for line in result["lines"]] == [
        ("previous", 9., 11.), ("inside", 14., 16.), ("next", 19., 22.),
    ]
    assert result["lines"][1]["text"] == "A line in this shot."
    assert result["lines"][2]["source"] == "whisper"
    assert set(result["lines"][0]) == {"line_id", "t_start", "t_end", "text", "source"}
    assert result["truncated"] is False


def test_missing_coverage_is_distinct_from_known_film_with_no_overlapping_lines(db, config):
    assert dialogue.shot_dialogue(db, config, unit_id="shot")["status"] == "unavailable"
    add_lines(db, [{"film_id": "other"}])
    assert dialogue.shot_dialogue(db, config, unit_id="shot")["status"] == "unavailable"
    add_lines(db, [{"line_id": "elsewhere", "t_start": 1., "t_end": 2.}])
    result = dialogue.shot_dialogue(db, config, unit_id="shot")
    assert result["status"] == "available"
    assert result["lines"] == []
    db.drop_table("dialogue_lines")
    assert dialogue.shot_dialogue(db, config, unit_id="shot")["status"] == "unavailable"


@pytest.mark.parametrize("change", [
    {"is_representative": False}, {"film_id": "unpublished"},
    {"t_start": -1.}, {"t_end": 10.}, {"t_end": float("inf")},
])
def test_unpublished_and_invalid_shots_are_unavailable(db, config, change):
    if change.get("t_end") == float("inf"):
        db.open_table("units").update(where="unit_id = 'shot'", values_sql={"t_end": "CAST('Infinity' AS DOUBLE)"})
    else:
        db.open_table("units").update(where="unit_id = 'shot'", values=change)
    assert dialogue.shot_dialogue(db, config, unit_id="shot") is None
    assert dialogue.shot_dialogue(db, config, unit_id="missing") is None


def test_invalid_lines_are_omitted_and_cap_is_chronological_not_storage_order(db, config):
    add_lines(db, [
        {"line_id": f"good-{i:03d}", "t_start": 11. + i / 100, "t_end": 15.}
        for i in reversed(range(dialogue.MAX_SHOT_DIALOGUE_LINES + 5))
    ] + [
        {"line_id": "negative", "t_start": -1., "t_end": 12.},
        {"line_id": "infinite", "t_end": float("inf")},
        {"line_id": "reversed", "t_start": 12., "t_end": 11.},
        {"line_id": "blank", "text": "  "},
        {"line_id": "missing-text", "text": None},
    ])
    result = dialogue.shot_dialogue(db, config, unit_id="shot")
    assert result["truncated"] is True
    assert [line["line_id"] for line in result["lines"]] == [
        f"good-{i:03d}" for i in range(dialogue.MAX_SHOT_DIALOGUE_LINES)
    ]


def test_one_pinned_snapshot_keeps_shot_and_dialogue_consistent(db, config, monkeypatch):
    add_lines(db, [{"text": "Original line"}])
    captures = []

    def capture_then_publish(cfg, database):
        snapshot = capture_snapshot(cfg, database)
        captures.append(snapshot)
        database.open_table("units").update(where="unit_id = 'shot'", values={"t_start": 13.})
        database.open_table("dialogue_lines").update(where="film_id = 'film'", values={"text": "New line"})
        return snapshot

    monkeypatch.setattr(dialogue, "acquire_search_snapshot", capture_then_publish)
    result = dialogue.shot_dialogue(db, config, unit_id="shot")
    assert len(captures) == 1
    assert result["t_start"] == 10.
    assert result["lines"][0]["text"] == "Original line"
    monkeypatch.setattr(dialogue, "acquire_search_snapshot", Mock(side_effect=AssertionError("Already pinned")))
    assert dialogue.shot_dialogue(captures[0], config, unit_id="shot") == result


def test_api_is_typed_warmup_independent_and_propagates_snapshot_unavailability(db, config, monkeypatch):
    from pipeline.api.main import app

    monkeypatch.setattr(app.state, "db", db, raising=False)
    monkeypatch.setattr(app.state, "config", config, raising=False)
    monkeypatch.setattr(app.state, "encoder_ready", False, raising=False)
    client = TestClient(app)
    add_lines(db, [{"text": "Inspect me", "source": None}])
    response = client.get("/library/shot/shot/dialogue")
    assert response.status_code == 200
    assert response.json() == {
        "unit_id": "shot", "film_id": "film", "t_start": 10., "t_end": 20.,
        "status": "available", "truncated": False,
        "lines": [{"line_id": "0", "t_start": 11., "t_end": 12., "text": "Inspect me", "source": None}],
    }
    assert client.get("/library/shot/missing/dialogue").status_code == 404
    assert client.get("/library/shot/" + "a" * 257 + "/dialogue").status_code == 422
    monkeypatch.setattr(dialogue, "acquire_search_snapshot", Mock(side_effect=SearchLibraryUnavailable("Retry publication")))
    response = client.get("/library/shot/shot/dialogue")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
