"""A long-song replacement has the same storage and failure semantics as one shot."""
from copy import deepcopy

import pytest

from pipeline.lab import direction_planner, music, music_planner
from pipeline.lab.long_generation import _merge_fixed
from pipeline.lab.models import ClipSelection, ProjectDocument
from pipeline.tests.test_lab import db, store  # noqa: F401
from pipeline.tests.test_lab_long_generation import _project, _run


def test_fixed_replacement_at_six_hundred_sources_keeps_bin_order_and_untouched_footage():
    passage = {"start": 0., "end": 120.}
    document = ProjectDocument(
        track={"id": "track", "name": "Song", "duration": 120.}, passage=passage,
        analysis={"segments": [{"start": 0., "end": 120.}]},
        clips=[ClipSelection(id=f"clip-{index}", film_id="film", source_start=index * 3.,
                             source_end=index * 3. + 3, locked=index != 10).model_dump(mode="json")
               for index in range(600)],
        music_timeline={"track_id": "track", "passage": passage,
            "slots": [{"id": f"slot-{index}", "start": index * 3., "end": index * 3. + 3,
                       "section_index": 0, "clip_id": f"clip-{index}"} for index in range(40)]},
    ).model_dump(mode="json")
    before = deepcopy(document)
    replacement = ClipSelection(id="replacement", film_id="other", source_start=10., source_end=13.).model_dump(mode="json")
    result = {
        "music_timeline": {"slots": deepcopy(document["music_timeline"]["slots"][9:12])},
        "clips": [deepcopy(document["clips"][9]), replacement, deepcopy(document["clips"][11])],
    }
    result["music_timeline"]["slots"][1]["clip_id"] = replacement["id"]

    _merge_fixed(document, result, 9, 11)

    ProjectDocument.model_validate(document)
    assert len(document["clips"]) == 600
    assert document["clips"][10] == replacement
    assert document["clips"][:10] == before["clips"][:10]
    assert document["clips"][11:] == before["clips"][11:]
    assert not any(clip["id"] == "clip-10" for clip in document["clips"])
    expected = deepcopy(before["music_timeline"])
    expected["slots"][10]["clip_id"] = "replacement"
    assert document["music_timeline"] == expected
    # The assembled proposal must not retain mutable aliases to a part result.
    replacement["source_start"] = 11.
    result["music_timeline"]["slots"][1]["reason"] = "Later scratch mutation"
    assert document["clips"][10]["source_start"] == 10.
    assert document["music_timeline"]["slots"][10]["reason"] is None


@pytest.mark.parametrize("filled", [True, False])
def test_targeted_abstention_reports_only_requested_failure_and_preserves_existing_edit(
    config, store, db, tmp_path, monkeypatch, filled,
):
    project = _project(store, db, tmp_path, duration=120., fixed=True)
    document = deepcopy(project["document"])
    target_id = "s1" if filled else "s0"
    if filled:
        next(clip for clip in document["clips"] if clip["id"] == "old1")["locked"] = False
        project = store.update_project(project["id"], project["revision"], document)
    before = deepcopy(project["document"])
    reason = "None of the offered scenes supports this requested action."
    called = []

    def plan(job, *_):
        assert job["snapshot"]["slot_ids"] == [target_id]
        return deepcopy(job["document"])

    def abstain(part, _config, _db, _progress, _job_id, slot_ids, **_options):
        assert slot_ids == [target_id]
        called.append(target_id)
        part = deepcopy(part)
        target = next(slot for slot in part["music_timeline"]["slots"] if slot["id"] == target_id)
        target["search_error"] = reason
        part["analysis"]["draft"] = {"selected_count": 0, "candidate_count": 2, "abstained_slot_ids": [target_id]}
        return part

    monkeypatch.setattr(direction_planner, "run_direction_job", plan)
    monkeypatch.setattr(music_planner, "fill_timeline", abstain)
    monkeypatch.setattr(music, "run_music_job", lambda *_: pytest.fail("Current analysis must be reused"))
    revision_count = len(store.revisions(project["id"]))

    result, _ = _run(store, config, db, project, mode="improve", slot_ids=[target_id])

    assert result["status"] == "completed", result["error"]
    assert result["result"]["applied"] is True
    assert result["result"]["message"] == (
        "No fitting replacement found; your original shot is kept." if filled else
        "No fitting scene found; the selected shot remains empty."
    )
    assert result["result"]["failed_slots"] == [{"slot_id": target_id, "error": reason}]
    assert result["result"]["requested_slot_ids"] == [target_id]
    assert result["result"]["remaining_gaps"] == 2
    assert result["result"]["selected_count"] == 0
    assert called == [target_id]
    saved = store.get_project(project["id"])["document"]
    assert saved["clips"] == before["clips"]
    expected = deepcopy(before["music_timeline"])
    next(slot for slot in expected["slots"] if slot["id"] == target_id)["search_error"] = reason
    assert saved["music_timeline"] == expected
    assert len(store.revisions(project["id"])) == revision_count + 1
