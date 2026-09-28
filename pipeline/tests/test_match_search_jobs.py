"""Audition the first grounded cut during refinement without misbinding media."""

from copy import deepcopy
from types import ModuleType
import sys

import pytest

from pipeline.lab.media import JobCancelled
from pipeline.lab.models import ProjectDocument
from pipeline.lab.store import LabStore
from pipeline.matching import jobs
from pipeline.matching.contracts import SearchRequest


def candidate(number, score=1.):
    clip = ProjectDocument(clips=[{
        "id": "reference", "film_id": "film-a", "unit_id": "shot-a",
        "source_start": 2.04, "source_end": 3.04, "reference_time": 3.,
    }]).model_dump(mode="json")["clips"][0]
    identity = f"match-{number:016x}"
    return {"id": identity, "score": score, "outgoing": clip,
            "incoming": {**clip, "id": identity, "film_id": f"film-{number}", "unit_id": f"shot-{number}",
                         "source_start": 6., "source_end": 7., "reference_time": 6.},
            "reference_frame_pts": 3., "candidate_frame_pts": 6., "crop": None,
            "preview_ready": False, "evidence": "Supported position match"}


@pytest.fixture
def adapter(config, monkeypatch):
    store = LabStore(config.paths.state_dir)
    store.initialize()
    request = SearchRequest(cohort_id="cohort-test", reference={"unit_id": "shot-a", "time": 3.}).model_dump(mode="json")
    store.enqueue_match_search(request, {"scorer": "test"}, candidate(1)["outgoing"])
    job = store.claim()
    events = []
    engine = ModuleType("pipeline.matching.search")
    monkeypatch.setitem(sys.modules, "pipeline.matching.search", engine)

    def thumbnail(_config, _db, _identity, row, _cancelled):
        events.append(("thumbnail", row["id"]))
        row["frame_url"] = f"/frame/{row['id']}"

    def render(_config, _db, _store, _identity, row, _progress, _cancelled):
        events.append(("render", row["id"], row["reference_frame_pts"]))
        row.update(preview_ready=True, preview_url=f"/preview/{row['id']}",
                   preview_sha256="verified-by-renderer", boundary_checks={"proposed": {"passed": True}})

    monkeypatch.setattr(jobs, "_thumbnail", thumbnail)
    monkeypatch.setattr(jobs, "_render", render)
    return config, store, job, engine, events


def run(adapter):
    config, store, job, _engine, _events = adapter
    return jobs.run(job, config, None, store, lambda _: None, lambda: store.is_cancelled(job["id"]))


def test_first_verified_preview_is_published_before_find_returns_and_reused_after_reordering(adapter):
    _config, store, job, engine, events = adapter
    first, second, third, fourth = [candidate(n, score=5 - n) for n in range(1, 5)]

    def find(*_args, on_candidate):
        on_candidate(deepcopy(first))
        partial = store.get_job(job["id"])["result"]
        assert partial["candidates"][0]["preview_ready"]
        assert partial["first_playable_seconds"] is not None
        events.append(("engine-still-running",))
        on_candidate(deepcopy(second))
        assert len([event for event in events if event[0] == "render"]) == 1
        return {"candidates": deepcopy([second, first, third, fourth])}

    engine.find = find
    result = run(adapter)
    assert [row["id"] for row in result["candidates"]] == [second["id"], first["id"], third["id"], fourth["id"]]
    assert [event[1] for event in events if event[0] == "render"] == [first["id"], second["id"], third["id"]]
    assert [event for event in events if event == ("thumbnail", first["id"])] == [("thumbnail", first["id"])]
    assert all(row["preview_ready"] for row in result["candidates"][:3])
    assert not result["candidates"][3]["preview_ready"]
    assert result["eager_preview_count"] == 3
    assert result["first_playable_seconds"] <= result["top_three_playable_seconds"]


def test_speculative_preview_outside_final_top_three_costs_only_one_extra_encode(adapter):
    _config, _store, _job, engine, events = adapter
    discovered = candidate(9, score=.2)
    final = [candidate(i) for i in range(1, 5)] + [discovered]

    def find(*_args, on_candidate):
        on_candidate(deepcopy(discovered))
        for row in final[:3]:
            on_candidate(deepcopy(row))
        assert len([event for event in events if event[0] == "render"]) == 1
        return {"candidates": deepcopy(final)}

    engine.find = find
    result = run(adapter)
    assert len([event for event in events if event[0] == "render"]) == 4
    assert result["eager_preview_count"] == 4
    assert result["candidates"][-1]["preview_ready"]


def test_changed_outgoing_cut_cannot_inherit_speculative_preview_with_same_id(adapter):
    _config, _store, _job, engine, events = adapter
    original = candidate(1)
    changed = deepcopy(original)
    changed["reference_frame_pts"] = 3.04
    changed["outgoing"].update(source_start=2.08, source_end=3.08, reference_time=3.04)

    def find(*_args, on_candidate):
        on_candidate(deepcopy(original))
        return {"candidates": [deepcopy(changed)]}

    engine.find = find
    result = run(adapter)
    assert [event[2] for event in events if event[0] == "render"] == [3., 3.04]
    assert result["candidates"][0]["reference_frame_pts"] == 3.04
    assert result["candidates"][0]["preview_ready"]


def test_cancellation_during_speculative_preview_stops_refinement_immediately(adapter, monkeypatch):
    _config, store, job, engine, events = adapter

    def render(*_args):
        store.cancel(job["id"])
        raise JobCancelled("Cancel while rendering the first cut")

    def find(*_args, on_candidate):
        on_candidate(candidate(1))
        pytest.fail("Cancelled audition must not resume expensive refinement")

    monkeypatch.setattr(jobs, "_render", render)
    engine.find = find
    with pytest.raises(JobCancelled):
        run(adapter)
    assert store.is_cancelled(job["id"])
    assert not any(row.get("preview_ready") for row in store.get_job(job["id"])["result"]["candidates"])
    assert len(events) == 1


def test_failed_speculative_render_does_not_erase_other_supported_candidates(adapter, monkeypatch):
    _config, _store, _job, engine, _events = adapter
    failing, working = candidate(1), candidate(2)
    original = jobs._render

    def render(*args):
        if args[4]["id"] == failing["id"]:
            raise ValueError("Preview boundary mismatch")
        return original(*args)

    def find(*_args, on_candidate):
        on_candidate(deepcopy(failing))
        return {"candidates": [deepcopy(failing), deepcopy(working)]}

    monkeypatch.setattr(jobs, "_render", render)
    engine.find = find
    result = run(adapter)
    assert not result["candidates"][0]["preview_ready"]
    assert "boundary mismatch" in result["candidates"][0]["preview_warning"]
    assert result["candidates"][1]["preview_ready"]
    assert result["first_playable_seconds"] is not None
    assert result["top_three_playable_seconds"] is None
