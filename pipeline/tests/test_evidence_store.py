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


def test_prune_removes_only_superseded_profiles_when_current_exists(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from pipeline.evidence import maintenance
    from pipeline.evidence.library import FilmRef

    monkeypatch.setattr(maintenance, "current_profiles",
                        lambda: {"synthesis": "priors-v2-aaaaaaaaaa", "understanding": "gemini-shots-v1-bbbbbbbbbb"})
    film = "a" * 64
    synthesis = tmp_path / film / "evidence" / "synthesis"
    synthesis.mkdir(parents=True)
    (synthesis / "priors-v2-aaaaaaaaaa.json").write_text("{}")
    (synthesis / "priors-v1-cccccccccc.json").write_text("{}")
    understanding = tmp_path / film / "evidence" / "understanding"
    (understanding / "gemini-shots-v1-dddddddddd").mkdir(parents=True)            # only an old profile exists
    (understanding / "gemini-shots-v1-dddddddddd.json").write_text("{}")
    config = SimpleNamespace(paths=SimpleNamespace(assets_dir=tmp_path))
    films = [FilmRef(film_id=film, title="Film (2000)", path=tmp_path / "f.mkv", duration=1.0, fps=24.0)]
    report = maintenance.prune(config, films)
    assert report["kinds"] == {"synthesis": {"entries": 1, "bytes": 2}} and not report["applied"]
    assert (synthesis / "priors-v1-cccccccccc.json").exists()
    maintenance.prune(config, films, apply=True)
    assert not (synthesis / "priors-v1-cccccccccc.json").exists()
    assert (synthesis / "priors-v2-aaaaaaaaaa.json").exists()
    assert (understanding / "gemini-shots-v1-dddddddddd.json").exists()           # protected: no current profile yet


def test_serving_artifact_falls_back_to_the_newest_earlier_profile_of_the_same_producer(tmp_path):
    import os
    film = "f" * 64
    old = store.Producer("understanding", "gemini-shots", 1, {"proxy_height": 360})
    older = store.Producer("understanding", "gemini-shots", 1, {"proxy_height": 480})
    other = store.Producer("understanding", "other-model", 1)
    current = store.Producer("understanding", "gemini-shots", 1, {"proxy_height": 240})
    for age, producer in enumerate((old, older, other)):
        path = store.write_artifact(tmp_path, film, producer, {"from": producer.profile_id}, inputs={})
        os.utime(path, (1_000_000 - age * 100, 1_000_000 - age * 100))
    assert store.read_artifact(tmp_path, film, current) is None
    served = store.serving_artifact(tmp_path, film, current)
    assert served["profile_id"] == old.profile_id               # newest of the same producer name
    store.write_artifact(tmp_path, film, current, {"from": "current"}, inputs={})
    assert store.serving_artifact(tmp_path, film, current)["data"] == {"from": "current"}
    assert store.serving_artifact(tmp_path, film, store.Producer("understanding", "absent", 1)) is None
