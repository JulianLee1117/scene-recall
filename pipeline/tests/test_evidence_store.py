"""Tests for versioned per-film evidence artifacts and title helpers."""

from __future__ import annotations

import pytest

from pipeline.evidence import store
from pipeline.evidence.library import parse_title


FILM = "a" * 64


def test_profile_id_changes_with_settings():
    first = store.Producer("metadata", "open-data", 1, {"a": 1})
    second = store.Producer("metadata", "open-data", 1, {"a": 2})
    assert first.profile_id.startswith("open-data-v1-")
    assert first.profile_id != second.profile_id


def test_producer_rejects_unsafe_names():
    with pytest.raises(ValueError):
        store.Producer("../x", "p", 1)
    with pytest.raises(ValueError):
        store.Producer("kind", "p", 0)


@pytest.mark.parametrize("compress", [False, True])
def test_artifact_round_trip_and_staleness(tmp_path, compress):
    producer = store.Producer("understanding", "probe", 2, {"model": "m"})
    path = store.write_artifact(tmp_path, FILM, producer, {"x": [1, 2]}, inputs={"shots": "abc"}, compress=compress)
    assert path.parent == tmp_path / FILM / "evidence" / "understanding"
    document = store.read_artifact(tmp_path, FILM, producer, inputs={"shots": "abc"})
    assert document["data"] == {"x": [1, 2]}
    assert store.read_artifact(tmp_path, FILM, producer, inputs={"shots": "changed"}) is None
    other = store.Producer("understanding", "probe", 2, {"model": "other"})
    assert store.read_artifact(tmp_path, FILM, other) is None


def test_read_artifact_ignores_corrupt_file(tmp_path):
    producer = store.Producer("metadata", "open-data", 1)
    path = store.artifact_path(tmp_path, FILM, producer)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert store.read_artifact(tmp_path, FILM, producer) is None


def test_invalid_film_id_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        store.artifact_path(tmp_path, "../../etc", store.Producer("metadata", "p", 1))


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("The Matrix (1999)", ("The Matrix", 1999, None)),
        ("Dune - Part Two (2024)", ("Dune: Part Two", 2024, None)),
        ("Cure (1997) [Criterion]", ("Cure", 1997, "Criterion")),
        ("8½ (1963)", ("8½", 1963, None)),
        ("Untitled", ("Untitled", None, None)),
    ],
)
def test_parse_title(title, expected):
    assert parse_title(title) == expected


def test_producer_settings_round_trip_through_json(tmp_path):
    producer = store.Producer("synthesis", "priors", 1, {"levels": (0.0, 0.5), "nested": {"pair": (1, 2)}})
    store.write_artifact(tmp_path, "a" * 64, producer, {"ok": True}, inputs={})
    assert store.read_artifact(tmp_path, "a" * 64, producer)["data"] == {"ok": True}
