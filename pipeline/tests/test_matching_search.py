"""Portable search contracts: exact evidence, bounded work and legal source edits."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from pipeline.matching import media, search
from pipeline.matching.search_cues import score_pair, rank_pair, shared_shortlist
from pipeline.matching.search_subject import select_reference_track


def sample(timestamp):
    return media.Sample(timestamp, timestamp + .04, None)


def track(times=(1.,), center=(.5, .5), silhouette=None, profile="same"):
    return {"id": 1, "profile_id": profile, "motion": [], "frames": [
        {"time": time, "end": time + .04, "visible": True, "centroid": list(center), "area": .16,
         "box": {"x": center[0] - .2, "y": center[1] - .2, "width": .4, "height": .4},
         "picture_aspect": 1., "silhouette": [1.] * 64 if silhouette is None else silhouette}
        for time in times]}


def options(**updates):
    return {"cohort_id": "cohort-0000000000000000", "reference": {"unit_id": "a", "time": 1., "region": None, "subject_point": None},
            "focus": "auto", "timing": "nearby", "film_ids": [], "include_source_film": True,
            "min_incoming_seconds": 1., "allow_reframing": False, **updates}


def unit(identity="a", film="first", start=0., end=5.):
    return {"unit_id": identity, "film_id": film, "t_start": start, "t_end": end, "caption": identity}


def test_position_is_independent_of_different_shape_size_and_unknown_motion():
    a = track(silhouette=[1.] * 8 + [0.] * 56)
    b = track(silhouette=[0.] * 56 + [1.] * 8)
    b["frames"][0]["area"] = .01
    b["frames"][0]["box"].update(width=.1, height=.1)
    cues = score_pair(sample(1), sample(1), a, b, [], [], enabled={"position", "shape", "subject", "camera"}, cache={})
    assert [cue["code"] for cue in cues] == ["position"]
    assert rank_pair(cues, "auto")["primary_cue"] == "position"
    assert rank_pair(cues, "shape") is None


def test_cue_evidence_must_exist_at_the_exact_pair_not_nearby_frames():
    a, b = track((1.,)), track((2.,))
    enabled = {"position", "shape"}
    assert score_pair(sample(1), sample(2.01), a, b, [], [], enabled=enabled, cache={}) == []
    cues = score_pair(sample(1), sample(2), a, b, [], [], enabled=enabled, cache={})
    assert {cue["code"] for cue in cues} == enabled
    assert all(cue["measurements"]["reference_frame_pts"] == 1 and cue["measurements"]["candidate_frame_pts"] == 2 for cue in cues)


def test_unknown_profiles_do_not_become_compatible_by_both_missing_identity():
    for identity in (None, "different"):
        a, b = track(profile=None if identity is None else "same"), track(profile=identity)
        with pytest.raises(ValueError, match="incompatible"):
            score_pair(sample(1), sample(1), a, b, [], [], enabled={"position"}, cache={})


def test_camera_cannot_claim_a_cut_with_a_gap_to_its_boundary():
    def camera(start):
        return [{"start": start + i / 6, "end": start + (i + 1) / 6, "camera": [.1, 0, 0, 0], "reliable": True} for i in range(6)]
    enabled = {"camera"}
    assert score_pair(sample(1), sample(2), None, None, camera(0), camera(2), enabled=enabled, cache={})
    assert score_pair(sample(1), sample(2), None, None, camera(0), camera(2.1), enabled=enabled, cache={}) == []
    assert score_pair(sample(1), sample(2), None, None, camera(-.1), camera(2), enabled=enabled, cache={}) == []


def test_subject_motion_must_touch_both_exact_frames_even_if_middle_matches(monkeypatch):
    from pipeline.matching import subjects
    monkeypatch.setattr(subjects, "similarity", lambda *args, **kwargs: {"reliable": True, "components": {"movement": 1}})
    a, b = track((1.,)), track((2.,))
    a["motion"] = [{"start": .5, "end": 1., "reliable": True}]
    b["motion"] = [{"start": 2., "end": 2.5, "reliable": True}]
    assert score_pair(sample(1), sample(2), a, b, [], [], enabled={"subject"}, cache={})
    a["motion"][0]["end"] = .9
    assert score_pair(sample(1), sample(2), a, b, [], [], enabled={"subject"}, cache={}) == []
    a["motion"][0]["end"] = 1.
    b["motion"][0]["start"] = 2.1
    assert score_pair(sample(1), sample(2), a, b, [], [], enabled={"subject"}, cache={}) == []


def test_nonfinite_centers_never_create_a_cue():
    a, b = track(), track(center=(float("nan"), .5))
    assert score_pair(sample(1), sample(1), a, b, [], [], enabled={"position", "shape"}, cache={}) == []


def test_automatic_reference_prefers_contained_subject_over_larger_edge_background():
    # Exact anchor geometry from the user's EEAAO search. Track 1 was the right
    # background; track 3 follows the centered face/hand region.
    background, person = track(), track()
    background["frames"][0].update(quality=.9905, area=.14458, centroid=[.87155, .44543],
                                   box={"x": .646875, "y": .11561, "width": .353125, "height": .61849})
    person["frames"][0].update(quality=.98616, area=.05453, centroid=[.53162, .52997],
                               box={"x": .45, "y": .2659, "width": .15625, "height": .61849})
    assert select_reference_track([background, person], 1.) is person
    assert select_reference_track([background, person], 1., explicit=True) is background


def test_reference_selection_keeps_cropped_details_and_requires_actual_anchor_evidence():
    hand = track(center=(.55, .75))
    hand["frames"][0]["box"].update(y=.5, height=.5)
    assert select_reference_track([hand], 1.) is hand
    assert select_reference_track([hand], 1.01) is None
    hand["frames"][0]["visible"] = False
    assert select_reference_track([hand], 1.) is None


def test_source_scope_includes_other_shots_in_same_film_but_never_overlaps():
    reference = unit()
    assert search.eligible_source(unit("b", start=5., end=7.), reference, options())
    assert not search.eligible_source(unit("b", start=5., end=7.), reference, options(include_source_film=False))
    assert not search.eligible_source(unit("b", start=4.9, end=7.), reference, options())
    assert not search.eligible_source(reference, reference, options())
    assert not search.eligible_source(unit("b", film="second"), reference, options(film_ids=["third"]))
    assert not search.eligible_source(unit("b", film="second", end=1.4), reference, options(min_incoming_seconds=1.5))


def test_shared_shortlist_deduplicates_across_cues_and_caps_unique_work():
    rows = [{"unit": unit(str(index)), "time": 1., "score": 1 - index / 100} for index in range(30)]
    result = shared_shortlist({"position": rows, "subject": rows[::-1], "camera": rows})
    assert len(result) == len({row["unit"]["unit_id"] for row in result}) == 10
    assert all(set(row["retrieved_cues"]) == {"position", "subject", "camera"} for row in result)
    with pytest.raises(ValueError, match="ten"):
        shared_shortlist({"position": rows}, 11)


def test_auto_common_position_and_weak_shape_do_not_beat_supported_movement():
    # Reported Stargate failure: almost identical centers, weak outline evidence.
    generic = [{"code": "position", "strength": .786}, {"code": "shape", "strength": .201}]
    movement = [{"code": "subject", "strength": .439}]
    assert rank_pair(movement, "auto")["score"] > rank_pair(generic, "auto")["score"]
    assert rank_pair(generic, "auto")["primary_cue"] == "shape"
    assert rank_pair(generic, "position")["score"] == .786


def test_correlated_cues_and_barely_supported_cues_do_not_buy_equal_bonuses():
    shape = [{"code": "shape", "strength": .8}]
    assert rank_pair(shape, "auto") == rank_pair(shape + [{"code": "position", "strength": 1.}], "auto")
    motion = [{"code": "subject", "strength": .8}]
    assert rank_pair(motion, "auto") == rank_pair(motion + [{"code": "camera", "strength": .7}], "auto")
    weak_support = rank_pair(shape + [{"code": "subject", "strength": .01}], "auto")["score"]
    good_support = rank_pair(shape + [{"code": "subject", "strength": .6}], "auto")["score"]
    assert weak_support == pytest.approx(.801)
    assert good_support == pytest.approx(.86)


def test_shortlist_does_not_promote_two_weak_static_votes_over_one_strong_pair():
    generic = {"unit": unit("generic"), "time": 1., "score": .21}
    action = {"unit": unit("action"), "time": 2., "score": .44}
    result = shared_shortlist({"position": [generic], "shape": [generic], "subject": [action]})
    assert [row["unit"]["unit_id"] for row in result] == ["action", "generic"]


def test_shortlist_seeds_strongest_exact_pair_not_best_independent_channel_rank():
    first = {"unit": unit("b"), "time": 1., "score": .2, "region": "weak-region"}
    second = {"unit": unit("b"), "time": 3., "score": .8, "region": "strong-region"}
    result = shared_shortlist({"position": [first], "subject": [second]})
    assert len(result) == 1
    assert result[0]["region"] == "strong-region" and result[0]["time"] == 3.


def test_flow_computation_is_shared_by_actual_image_pair_and_released_per_window():
    calls = []
    model = SimpleNamespace(profile={"id": "same"}, pair=lambda a, b: calls.append((a, b)) or np.zeros((2, 2, 2)))
    flow = search._SharedFlow(model)
    first, second = object(), object()
    assert flow.pair(first, second) is flow.pair(first, second)
    assert len(calls) == 1
    flow.clear()
    flow.pair(first, second)
    assert len(calls) == 2


def test_incoming_duration_filters_before_best_pair_so_invalid_winner_cannot_shadow():
    query, incoming = track((1.,)), track((1., 4.5))
    incoming["frames"][0]["centroid"] = [.52, .5]
    best = search._best_pair(query, [sample(1)], [incoming], [], [], [sample(1), sample(4.5)],
                             unit("b", film="second"), options(focus="position"), {"position"}, {})
    assert best["sample"].time == 1


def test_identity_contains_outgoing_frame_and_exact_preview_bounds_but_duration_authority_is_not_preview():
    item = {"reference": sample(1), "sample": sample(2), "outgoing_region": None, "incoming_region": None,
            "score": 1., "cues": [{"code": "position", "description": "same position"}], "primary_cue": "position"}
    first = search._candidate(item, unit("b", "second", end=40), unit(), {1.: .04}, {"contract": "v1"}, "Film")
    second = search._candidate({**item, "reference": sample(1.1)}, unit("b", "second", end=40), unit(), {1.1: .14}, {"contract": "v1"}, "Film")
    assert first["id"] != second["id"]
    assert first["incoming"]["source_end"] - first["incoming"]["source_start"] == 1
    assert first["incoming_authority"]["available_seconds"] == 38


def test_native_outgoing_handle_is_checked_before_reference_selection(monkeypatch):
    monkeypatch.setattr(media, "at", lambda *args: sample(.2))
    monkeypatch.setattr(media, "samples", lambda *args, **kwargs: [sample(.2), sample(.8), sample(1.)])
    monkeypatch.setattr(media, "outgoing_start", lambda path, frame, *args: max(0., frame.end - 1))
    _, _, selected, _ = search._references(None, unit(), options(reference={"unit_id": "a", "time": .2}), lambda: False)
    assert selected and all(row.time >= .8 for row in selected)


def test_public_validation_is_strict_and_queued_profiles_are_pinned(monkeypatch):
    subset = {"id": "cohort-0000000000000000", "units": [unit()], "motion_unit_ids": ["a"]}
    monkeypatch.setattr(search.cohort, "load", lambda *args: subset)
    monkeypatch.setattr(search.cohort, "unit", lambda *args: unit())
    subject = {"id": "subjects-1", "profile": {"id": "tracker-1", "flow_profile_id": "flow-1"}}
    motion = {"id": "motion-1", "profile": {"id": "flow-1"}}
    monkeypatch.setattr(search, "_profile", lambda config, subset, name: subject if name == "subjects" else motion)
    identity = search.validate_request(None, None, options())
    assert identity == {"search_contract": search.CONTRACT, "subjects": "subjects-1", "motion": "motion-1"}
    assert search.validate_request(None, None, {**options(), "profile_id": identity}) == identity
    with pytest.raises(ValueError, match="changed"):
        search.validate_request(None, None, {**options(), "profile_id": {"subjects": "old"}})
    with pytest.raises(ValueError):
        search.validate_request(None, None, options(arbitrary_model="anything"))
    subject["profile"]["flow_profile_id"] = "other-flow"
    with pytest.raises(ValueError, match="incompatible"):
        search.validate_request(None, None, options())


def test_corrupt_or_incomplete_profile_is_never_silently_accepted(tmp_path, monkeypatch):
    subset = {"id": "cohort-0000000000000000", "motion_unit_ids": ["a"]}
    monkeypatch.setattr(search.cohort, "cohort_path", lambda *args: tmp_path)
    (tmp_path / "subjects").mkdir()
    payload = {"cohort_id": subset["id"], "complete": True, "expected_rows": 1,
               "profile": {"id": "same"}, "windows": [{"unit_id": "a", "tracks": [track()]}]}
    import json
    def save(value):
        (tmp_path / "subjects" / "manifest.json").write_text(json.dumps({**value, "id": search.cohort.digest(value)}))
    save(payload)
    assert search._profile(None, subset, "subjects")["complete"]
    save({**payload, "windows": []})
    with pytest.raises(ValueError, match="cover"):
        search._profile(None, subset, "subjects")
    save({**payload, "complete": False})
    with pytest.raises(ValueError, match="incomplete"):
        search._profile(None, subset, "subjects")


@pytest.mark.parametrize("cancel_at_callback", [False, True])
def test_full_search_refines_each_window_once_for_all_cues_and_stops_after_five(monkeypatch, cancel_at_callback):
    reference, candidates = unit(), [unit(str(index), film="second") for index in range(20)]
    subset = {"id": options()["cohort_id"], "units": [reference, *candidates], "films": ["first", "second"]}
    evidence = {"subjects": {"windows": [{"unit_id": row["unit_id"], "tracks": [track()]} for row in candidates]}}
    monkeypatch.setattr(search, "_resolve", lambda *args: (options(focus="auto"), subset, reference, evidence, {"scorer": "fixture"}))
    monkeypatch.setattr(search.cohort, "verify", lambda *args: None)
    monkeypatch.setattr(search.cohort, "resolve_film", lambda db, identity: {"path": identity, "title": identity})
    from pipeline.ingest import probe
    monkeypatch.setattr(probe, "_content_hash", lambda *args: "first")
    monkeypatch.setattr(search, "_references", lambda *args: (sample(1), [sample(0), sample(1)], [sample(1)], {1.: .04}))
    monkeypatch.setattr(search, "_models", lambda *args: (None, None))
    calls = []
    def measured(*args, **kwargs):
        calls.append(kwargs)
        return [track((1., 1.2))], []
    monkeypatch.setattr(search, "_measure", measured)
    monkeypatch.setattr(media, "samples", lambda *args, **kwargs: [sample(1), sample(1.2)])
    emitted = []
    def on_candidate(candidate):
        assert len(calls) == len(emitted) + 2  # emitted directly after its single refinement
        emitted.append(deepcopy(candidate))
        candidate["incoming"]["source_start"] = 9000  # consumer owns its copy
    cancelled = lambda: cancel_at_callback and bool(emitted)
    if cancel_at_callback:
        from pipeline.lab.media import JobCancelled
        with pytest.raises(JobCancelled):
            search.find(None, None, options(), lambda message: None, cancelled, on_candidate=on_candidate)
        assert len(calls) == 2 and len(emitted) == 1
        return
    result = search.find(None, None, options(), lambda message: None, cancelled, on_candidate=on_candidate)
    assert result["coverage"]["refined_window_count"] == 5
    assert len(calls) == 6  # one query and five shared windows, not per-cue calls
    assert len(result["candidates"]) == 5
    assert len(emitted) == len({row["id"] for row in emitted}) == 5
    assert sorted(emitted, key=lambda row: (-row["score"], row["id"])) == result["candidates"]
    assert all(set(row["matched_channels"]) == {"position", "shape"} for row in result["candidates"])
    assert any("No clear subject movement" in notice for notice in result["notices"])
    assert all(cue["measurements"]["reference_frame_pts"] == row["reference_frame_pts"]
               and cue["measurements"]["candidate_frame_pts"] == row["candidate_frame_pts"]
               for row in result["candidates"] for cue in row["cues"])


def test_auto_unknown_subject_keeps_supported_camera_and_explains_omission(monkeypatch):
    reference, candidate = unit(), unit("b", "second")
    subset = {"id": options()["cohort_id"], "units": [reference, candidate], "films": ["first", "second"]}
    def camera(start):
        return [{"start": start + i / 6, "end": start + (i + 1) / 6, "camera": [.1, 0, 0, 0], "reliable": True} for i in range(6)]
    evidence = {"subjects": {"windows": [{"unit_id": "b", "tracks": []}]},
                "motion": {"windows": [{"unit_id": "b", "samples": camera(2.)}]}}
    monkeypatch.setattr(search, "_resolve", lambda *args: (options(), subset, reference, evidence, {"scorer": "fixture"}))
    monkeypatch.setattr(search.cohort, "verify", lambda *args: None)
    monkeypatch.setattr(search.cohort, "resolve_film", lambda db, identity: {"path": identity, "title": identity})
    from pipeline.ingest import probe
    monkeypatch.setattr(probe, "_content_hash", lambda *args: "first")
    monkeypatch.setattr(search, "_references", lambda *args: (sample(1), [sample(0), sample(1)], [sample(1)], {1.: .04}))
    monkeypatch.setattr(search, "_models", lambda *args: (None, None))
    calls = []
    def measured(*args, **kwargs):
        calls.append(kwargs)
        return [], camera(0. if len(calls) == 1 else 2.)
    monkeypatch.setattr(search, "_measure", measured)
    monkeypatch.setattr(media, "samples", lambda *args, **kwargs: [sample(2), sample(2.2)])
    result = search.find(None, None, options(), lambda message: None)
    assert result["evaluated_channels"] == ["camera"]
    assert any("No visible subject" in notice for notice in result["notices"])
    assert result["candidates"][0]["matched_channels"] == ["camera"]


def test_camera_retrieval_does_not_choose_an_unjustified_subject_prompt():
    cameras = [{"start": 2 + i / 6, "end": 2 + (i + 1) / 6, "camera": [.1, 0, 0, 0], "reliable": True} for i in range(6)]
    reference_camera = [{**row, "start": row["start"] - 2, "end": row["end"] - 2} for row in cameras]
    proposal = search._coarse([unit("b", "second")], {"b": {"tracks": [track((2.,))]}}, {"b": {"samples": cameras}},
                              track(), reference_camera, [sample(1)], {"camera"}, "camera", options(), {}, lambda: False)
    assert proposal and proposal[0]["cue"] == "camera" and proposal[0]["region"] is None
