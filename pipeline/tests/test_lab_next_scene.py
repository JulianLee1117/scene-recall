"""Next-scene proposals are bounded choices, never automatic project edits."""
from copy import deepcopy
import json
from unittest.mock import MagicMock

import pytest

from pipeline.lab import music, next_scene, next_scene_media
from pipeline.lab.limits import MAX_SAVED_CLIPS
from pipeline.lab.media import JobCancelled
from pipeline.lab.models import ClipSelection, MusicDirection, ProjectDocument
from pipeline.tests.test_lab import db, store  # noqa: F401
from pipeline.tests.test_lab_music import _audio, _audio_interpretation, _interpretation
from pipeline.tests.test_lab_search_plans import _caps


def _direction(query="a figure steps into sunlight"):
    return {**MusicDirection(query=query).model_dump(mode="json"), "purpose": "Open the image into light",
            "music_cue": "Use the supplied musical context", "timing_note": "Let the image read before cutting"}


@pytest.fixture
def project(config, store, db, tmp_path):
    audio = tmp_path / "song.wav"; _audio(audio, 36)
    identity = music.content_hash(audio)
    store.add_track(identity, "Song", 36, audio)
    video = tmp_path / "film.mp4"; video.touch()
    db.open_table("films").add([{"film_id": "film", "title": "Film", "path": str(video), "duration": 150., "fps": 23.976}])
    db.open_table("units").add([
        {"unit_id": "anchor-unit", "film_id": "film", "t_start": 8., "t_end": 18., "caption": "An isolated figure"},
        {"unit_id": "short", "film_id": "film", "t_start": 30., "t_end": 36., "caption": "A brief shaft of light"},
        *[{"unit_id": f"source-{i}", "film_id": "film", "t_start": 60. + i, "t_end": 72. + i,
           "caption": f"A hopeful open space {i}"} for i in range(30)],
    ])
    passage = {"start": 10.03, "end": 34.03}
    cuts = [10.03, 14.17, 20.03, 28.03, 34.03]
    clips = [ClipSelection(id="before", film_id="film", source_start=0, source_end=4.14, locked=True).model_dump(mode="json"),
             ClipSelection(id="anchor", film_id="film", unit_id="anchor-unit", source_start=10, source_end=15.86).model_dump(mode="json"),
             ClipSelection(id="after", film_id="film", source_start=40, source_end=46, locked=True).model_dump(mode="json")]
    slots = [{"id": f"slot-{i}", "start": cuts[i], "end": cuts[i + 1], "section_index": 0,
              "clip_id": ["before", "anchor", None, "after"][i]} for i in range(4)]
    document = ProjectDocument(track={"id": identity, "name": "Song", "duration": 36}, passage=passage, clips=clips,
                               music_timeline={"track_id": identity, "passage": passage, "slots": slots},
                               analysis={**_interpretation(10.03, 34.03), "provenance": {"track": identity, "passage": passage}}).model_dump(mode="json")
    created = store.create_project("Next scene", "music-sketch")
    return store.update_project(created["id"], 1, document)


def _scope(document, db, *, flexible=False):
    return next_scene._anchor_handles(next_scene.validate_request(document, {"anchor_slot_id": "slot-1", "flexible_cut": flexible}), db)


def _candidate(document, db, *, flexible=False, short=False):
    scope = _scope(document, db, flexible=flexible)
    authority = next_scene._authority(next_scene._unit(db, "short" if short else "source-0"))
    cut = 22.03 if short else scope["current_cut"]
    incoming = ClipSelection(id="next-0123456789abcdef-clip", film_id="film", unit_id=authority["unit_id"],
                             source_start=authority["t_start"], source_end=authority["t_start"] + scope["t2"] - cut).model_dump(mode="json")
    candidate = {"id": "next-0123456789abcdef", "cut": cut, "outgoing": deepcopy(scope["anchor"]), "incoming": incoming,
                 "incoming_authority": authority, "direction": _direction(), "reason": "The enclosed image opens into space",
                 "search_evidence": None, "resolved_search": None, "preview_ready": False}
    return scope, candidate


@pytest.mark.parametrize("mutation,match", [
    (lambda d: d.update(music_timeline=None), "Choose music"),
    (lambda d: d["music_timeline"]["slots"][1].update(clip_id=None), "cross a gap"),
    (lambda d: d["music_timeline"]["slots"][2].update(clip_id="after"), "unique"),
])
def test_invalid_anchor_and_pair_are_rejected_before_work(project, mutation, match):
    document = deepcopy(project["document"]); mutation(document)
    with pytest.raises(ValueError, match=match):
        next_scene.validate_request(document, {"anchor_slot_id": "slot-1"})


def test_next_scene_uses_full_saved_clip_budget_and_reserves_room_for_placement(project, db):
    document = deepcopy(project["document"])
    document["clips"].extend(
        ClipSelection(id=f"saved-{index}", film_id="film", source_start=0, source_end=1).model_dump(mode="json")
        for index in range(MAX_SAVED_CLIPS - 1 - len(document["clips"]))
    )
    scope, candidate = _candidate(document, db)
    proposed = next_scene.proposal_document(document, scope, candidate, db)
    assert len(proposed["clips"]) == MAX_SAVED_CLIPS
    assert proposed["music_timeline"]["slots"][2]["clip_id"] == candidate["incoming"]["id"]
    with pytest.raises(ValueError, match=f"limited to {MAX_SAVED_CLIPS} sources"):
        next_scene.validate_request(proposed, {"anchor_slot_id": "slot-1"})


def test_locked_anchor_allows_fixed_search_but_never_rolls(project, db):
    document = deepcopy(project["document"]); document["clips"][1]["locked"] = True
    scope, candidate = _candidate(document, db)
    proposed = next_scene.proposal_document(document, scope, candidate, db)
    assert proposed["clips"][1] == document["clips"][1]
    with pytest.raises(ValueError, match="unlocked anchor"):
        _scope(document, db, flexible=True)


def test_following_placement_replacement_is_fixed_and_its_lock_is_protected(project, db):
    document = deepcopy(project["document"])
    selected = ClipSelection(id="old-next", film_id="film", source_start=70, source_end=78).model_dump(mode="json")
    document["clips"].append(selected); document["music_timeline"]["slots"][2]["clip_id"] = selected["id"]
    with pytest.raises(ValueError, match="empty following"):
        _scope(document, db, flexible=True)
    scope, candidate = _candidate(document, db)
    proposed = next_scene.proposal_document(document, scope, candidate, db)
    assert selected in proposed["clips"]  # The displaced source remains in the bin.
    document["clips"][-1]["locked"] = True
    with pytest.raises(ValueError, match="Unlock the following"):
        _scope(document, db)


def test_short_source_is_admitted_only_when_permitted_handles_can_fit_it(project, db):
    document = project["document"]
    fixed = _scope(document, db)
    flexible = _scope(document, db, flexible=True)
    short = next_scene._authority(next_scene._unit(db, "short"))
    assert next_scene.feasible_cuts(fixed, short) is None
    assert next_scene.feasible_cuts(flexible, short) == pytest.approx({"min": 22.03, "max": 22.03})
    scope, candidate = _candidate(document, db, flexible=True, short=True)
    proposed = next_scene.proposal_document(document, scope, candidate, db)
    assert proposed["music_timeline"]["slots"][1]["end"] == pytest.approx(22.03)
    assert proposed["clips"][-1]["source_end"] == 36
    assert sum(slot["end"] - slot["start"] for slot in proposed["music_timeline"]["slots"]) == pytest.approx(24)


def test_quantization_uses_whole_passage_origin_not_non_grid_pair_start(project, db):
    scope, candidate = _candidate(project["document"], db, flexible=True)
    adjusted = next_scene.adjusted_candidate(project["document"], scope, candidate, db, {"cut_time": 20.13, "source_start": 60.123456})
    assert (adjusted["cut"] - 10.03) * 24 == pytest.approx(round((20.13 - 10.03) * 24))
    assert (adjusted["cut"] - scope["t0"]) * 24 != pytest.approx(round((adjusted["cut"] - scope["t0"]) * 24))
    assert adjusted["incoming"]["source_start"] == 60.123456  # Source timestamps do not become 24-fps input frames.


def test_only_unchanged_non_grid_cut_remains_feasible_for_exact_source_capacity(project, db):
    document = deepcopy(project["document"])
    document["music_timeline"]["slots"][1]["end"] = 20.045
    document["music_timeline"]["slots"][2]["start"] = 20.045
    document["clips"][1].update(source_start=12.125, source_end=18.)
    scope = _scope(document, db, flexible=True)
    authority = {"film_id": "film", "unit_id": "exact", "t_start": 100., "t_end": 107.985}
    assert next_scene.feasible_cuts(scope, authority) == {"min": 20.045, "max": 20.045}
    options = next_scene._cut_options(scope, authority, {})
    assert len(options) == 1 and options[0]["cut"] == 20.045
    assert options[0]["source_start_max"] == pytest.approx(100.)
    assert next_scene._cut(scope, authority, 20.045) == 20.045
    # A shortfall larger than numerical tolerance cannot fabricate a handle.
    authority["t_end"] -= .001
    assert next_scene.feasible_cuts(scope, authority) is None


def test_cut_options_preserve_current_and_offer_only_exact_nearby_guides(project, db):
    document = deepcopy(project["document"])
    document["music_timeline"]["slots"][1]["end"] = 20.045
    document["music_timeline"]["slots"][2]["start"] = 20.045
    document["clips"][1]["source_end"] += .015
    scope = _scope(document, db, flexible=True)
    authority = next_scene._authority(next_scene._unit(db, "source-0"))
    measured = {"downbeats": [19.793], "beats": [18.1, 19.0, 19.5, 19.793, 20.4, 20.9, 21.4, 22., 28.]}
    options = next_scene._cut_options(scope, authority, measured)
    assert options[0]["basis"] == "current-cut" and options[0]["cut"] == 20.045
    assert len(options) <= 7 and len({option["cut"] for option in options}) == len(options)
    guides = [option for option in options if "guide_time" in option]
    assert len(guides) == 4 and any(option["basis"] == "estimated-downbeat" for option in guides)
    for option in options:
        assert next_scene._cut(scope, authority, option["cut"]) == option["cut"]
        assert option["duration"] == scope["t2"] - option["cut"]
        assert option["source_start_max"] + option["duration"] == authority["t_end"]
        if option["basis"] != "current-cut":
            assert (option["cut"] - scope["passage_start"]) * 24 == pytest.approx(round((option["cut"] - scope["passage_start"]) * 24))


def test_unresolved_anchor_handles_keep_cut_fixed(project, db):
    document = deepcopy(project["document"])
    document["clips"][1].update(source_start=20., source_end=25.86)
    scope = _scope(document, db, flexible=True)
    assert scope["cut_min"] == scope["cut_max"] == scope["current_cut"]
    assert scope["anchor_offer"] is None


def test_apply_changes_only_pair_preserves_written_intent_and_clears_stale_anchor_metadata(project, db):
    document = deepcopy(project["document"])
    anchor, following = document["music_timeline"]["slots"][1:3]
    anchor.update(direction=_direction("my anchor"), direction_source="user", reason="old reason", search_evidence={"rank": 1})
    following.update(direction=_direction("my next image"), direction_source="user")
    document["clips"][1].update(reference_time=15.8, window_start=15., window_end=15.86)
    scope, candidate = _candidate(document, db, flexible=True)
    candidate["cut"] = 19.53
    before = deepcopy(document)
    proposed = next_scene.proposal_document(document, scope, candidate, db)
    assert document == before
    assert proposed["music_timeline"]["slots"][0] == before["music_timeline"]["slots"][0]
    assert proposed["music_timeline"]["slots"][3] == before["music_timeline"]["slots"][3]
    assert proposed["clips"][0] == before["clips"][0] and proposed["clips"][2] == before["clips"][2]
    assert proposed["music_timeline"]["slots"][1]["direction"] == anchor["direction"]
    assert proposed["music_timeline"]["slots"][2]["direction"] == following["direction"]
    assert proposed["clips"][1]["reference_time"] is None and proposed["clips"][1]["window_start"] is None
    assert proposed["music_timeline"]["slots"][1]["reason"] is None
    assert proposed["music_timeline"]["slots"][1]["search_evidence"] is None
    for key in ("analysis", "rhythm", "passage", "brief", "planner_settings", "direction_plan"):
        assert proposed[key] == before[key]


@pytest.mark.parametrize("adjustments", [{"cut_time": 90}, {"source_start": 71.}, {"source_start": float("nan")}, {"source_start": True}, {"crop": {"width": 0}}])
def test_infeasible_or_unpermitted_adjustments_never_apply(project, db, adjustments):
    scope, candidate = _candidate(project["document"], db, flexible=True)
    with pytest.raises(ValueError):
        next_scene.proposal_document(project["document"], scope, candidate, db, adjustments)


def test_current_unit_authority_is_checked_beyond_film_bounds(project, db):
    scope, candidate = _candidate(project["document"], db)
    candidate["incoming_authority"]["t_end"] = 100  # Fits film, but was never offered by this unit.
    with pytest.raises(ValueError, match="source range changed"):
        next_scene.proposal_document(project["document"], scope, candidate, db)
    scope, candidate = _candidate(project["document"], db)
    changed = deepcopy(project["document"]); changed["brief"] = "My newer edit"
    with pytest.raises(ValueError, match="edit changed"):
        next_scene.proposal_document(changed, scope, candidate, db)


def test_legacy_scope_hash_survives_new_null_timing_metadata_but_not_real_ownership_changes(project, db):
    document = deepcopy(project["document"])
    scope, candidate = _candidate(document, db)
    legacy = deepcopy(document)
    legacy["music_timeline"].pop("provisional_timing", None)
    legacy.pop("editor_direction", None)
    scope["document_hash"] = music.digest(legacy)  # A preview saved before this optional field existed.
    assert next_scene.validate_request(document, scope["options"])["document_hash"] == scope["document_hash"]
    next_scene.proposal_document(document, scope, candidate, db)
    document["music_timeline"]["provisional_timing"] = {"contract": "local-rhythm-starter-v1", "fingerprint": "0" * 64}
    with pytest.raises(ValueError, match="edit changed"):
        next_scene.proposal_document(document, scope, candidate, db)


def test_manual_crop_preserves_pair_scope_and_written_intent_but_invalidates_visual_proof(project, db, config):
    document = deepcopy(project["document"])
    document["clips"][1]["crop"] = {"x": .1, "y": .1, "width": .8, "height": .8}
    following = document["music_timeline"]["slots"][2]
    following.update(direction=_direction("my framing intention"), direction_source="user", needs_direction=False)
    scope, candidate = _candidate(document, db)
    candidate["incoming"].update(reference_time=62., window_start=61., window_end=64.)
    candidate.update(search_evidence={"rank": 1, "matched_frame_timestamp": 62.},
                     resolved_search=next_scene.resolve_search(_direction(), [], _caps(config), 8.),
                     inspection={"profile": "original-uncropped-images"})
    crop = {"x": .2, "y": .1, "width": .5, "height": .6}
    adjusted = next_scene.adjusted_candidate(document, scope, candidate, db, {"crop": crop})
    assert adjusted["incoming"]["crop"] == crop and adjusted["outgoing"] == scope["anchor"]
    assert adjusted["search_evidence"] is None and adjusted["resolved_search"] is None and adjusted["inspection"] is None
    assert adjusted["incoming"]["reference_time"] is None and adjusted["incoming"]["window_start"] is None
    assert adjusted["preview_ready"] is False and adjusted["adjustments"]["crop"] == crop
    # Saved human adjustments can be revalidated without pretending the model generated them.
    inherited = next_scene.adjusted_candidate(document, scope, adjusted, db)
    assert inherited["incoming"] == adjusted["incoming"]
    proposed = next_scene.proposal_document(document, scope, inherited, db)
    assert proposed["clips"][:-1] == document["clips"]
    for index in (0, 1, 3):
        assert proposed["music_timeline"]["slots"][index] == document["music_timeline"]["slots"][index]
    assert proposed["music_timeline"]["slots"][2]["direction"] == following["direction"]
    assert proposed["music_timeline"]["slots"][2]["needs_direction"] is True
    reset = next_scene.adjusted_candidate(document, scope, adjusted, db, {"crop": None, "source_start": None, "cut_time": None})
    assert reset["incoming"]["crop"] is None and reset["adjustments"]["crop"] is None
    assert reset["incoming"]["source_start"] == adjusted["incoming"]["source_start"]


def test_automatic_candidates_cannot_introduce_crops_or_region_transforms(project, db):
    scope, candidate = _candidate(project["document"], db)
    candidate["incoming"]["crop"] = {"x": .2, "y": .2, "width": .5, "height": .5}
    with pytest.raises(ValueError, match="explicit manual adjustment"):
        next_scene.adjusted_candidate(project["document"], scope, candidate, db)
    candidate["incoming"]["crop"] = None
    candidate["incoming"]["region"] = {"x": .2, "y": .2, "width": .5, "height": .5}
    with pytest.raises(ValueError, match="region transforms"):
        next_scene.adjusted_candidate(project["document"], scope, candidate, db, {"crop": None})
    with pytest.raises(ValueError):
        next_scene.PairChoice(candidate_id="source", search_id="search", source_start=60, cut=20.03,
                              reason="A visual contrast", unverified_requirements=[], crop={})


def test_changed_manual_source_window_clears_old_inspection(project, db):
    scope, candidate = _candidate(project["document"], db)
    candidate["inspection"] = {"profile": "sampled-original-window"}
    assert next_scene.adjusted_candidate(project["document"], scope, candidate, db)["inspection"] == candidate["inspection"]
    assert next_scene.adjusted_candidate(project["document"], scope, candidate, db, {"source_start": 61.})["inspection"] is None


def _mock_work(monkeypatch, config, db, *, queries=1, count=8, preview_failure=False, choose=None):
    calls = []
    def hosted(_config, prompt, schema, **kwargs):
        payload = json.loads(prompt.split("\n", 1)[1]); calls.append((kwargs.get("operation"), payload, kwargs))
        if kwargs.get("audio_path") is not None:
            answer = _audio_interpretation(0, 24)
        elif kwargs.get("operation") == "next-scene-intents":
            answer = {"directions": [_direction(f"idea-{i}") for i in range(queries)]}
        else:
            answer = {"choices": []}
            for identity, offer in list(payload["offers"].items())[:3]:
                key = next(iter(offer["searches"]))
                selected = {"cut": offer["cut_options"][0]["cut"], "source_start": offer["cut_options"][0]["source_start_min"]}
                if choose:
                    selected.update(choose(offer))
                answer["choices"].append({"candidate_id": identity, "search_id": key, **selected,
                                          "reason": "The enclosed anchor opens into a sunlit space", "unverified_requirements": []})
        music.write_json(kwargs["receipt_path"], {"status": "completed"})
        return answer
    monkeypatch.setattr(music, "_hosted_json", hosted)
    monkeypatch.setattr(next_scene, "search_capabilities", lambda *_: _caps(config))
    monkeypatch.setattr(next_scene, "offered_references", lambda *_args, **_kwargs: [])
    def retrieve(resolved, *_):
        return [{**next_scene._unit(db, f"source-{i}"), "matched_frame_timestamp": 62. + i} for i in range(count)]
    monkeypatch.setattr(next_scene, "execute_search", retrieve)
    def render(identity, document, anchor_slot_id, *_):
        if preview_failure:
            raise RuntimeError("Preview encoding failed")
        return {"preview_ready": True, "manifest": {"profile": "test", "anchor": anchor_slot_id}}
    monkeypatch.setattr(next_scene_media, "render_preview", render)
    return calls


def _job(project, **options):
    options = {"anchor_slot_id": "slot-1", **options}
    return {"id": "test-next-job", "base_revision": project["revision"], "document": deepcopy(project["document"]),
            "snapshot": {"next_scene": options, "next_scene_scope": next_scene.validate_request(project["document"], options)}}


def test_run_returns_three_playable_immutable_proposals_with_one_plan_and_selector(config, store, db, project, monkeypatch):
    calls = _mock_work(monkeypatch, config, db)
    original = store.get_project(project["id"])
    job = _job(project)
    result = next_scene.run(job, config, db, store, lambda _: None)
    assert [call[0] for call in calls] == ["next-scene-intents", "next-scene-select"]
    assert result["hosted_request_count"] == 2
    assert len(result["candidates"]) == 3
    assert all(candidate["preview_ready"] and candidate["preview_url"] and candidate["manifest"] for candidate in result["candidates"])
    assert store.get_project(project["id"]) == original and job["document"] == original["document"]
    assert len({candidate["incoming"]["unit_id"] for candidate in result["candidates"]}) == 3
    assert calls[-1][1]["reference_note"].startswith("Indexed references")


def test_selector_end_of_unit_trim_uses_exact_offered_cut_without_requantizing(config, store, db, project, monkeypatch):
    def choose(offer):
        option = offer["cut_options"][-1]
        return {"cut": option["cut"], "source_start": option["source_start_max"]}
    calls = _mock_work(monkeypatch, config, db, choose=choose)
    result = next_scene.run(_job(project, flexible_cut=True), config, db, store, lambda _: None)
    for candidate in result["candidates"]:
        offer = calls[-1][1]["offers"][candidate["incoming"]["unit_id"]]
        assert candidate["cut"] == offer["cut_options"][-1]["cut"]
        assert candidate["incoming"]["source_end"] == candidate["incoming_authority"]["t_end"]
        proposed = next_scene.proposal_document(project["document"], result["scope"], candidate, db)
        assert proposed["clips"][-1]["source_end"] == candidate["incoming_authority"]["t_end"]


@pytest.mark.parametrize("choose,match", [
    (lambda offer: {"cut": offer["cut_options"][0]["cut"] + next_scene.FRAME / 3}, "exact offered cut"),
    (lambda offer: {"source_start": offer["cut_options"][0]["source_start_max"] + next_scene.FRAME / 2}, "enough real footage"),
])
def test_selector_cannot_invent_cut_options_or_repair_invalid_source_in(config, store, db, project, monkeypatch, choose, match):
    calls = _mock_work(monkeypatch, config, db, choose=choose)
    with pytest.raises(ValueError, match=match):
        next_scene.run(_job(project, flexible_cut=True), config, db, store, lambda _: None)
    assert len(calls) == 2  # No retry, hidden trim repair or extra model call.


def test_selector_can_abstain_without_mutation_retries_or_rendering(config, store, db, project, monkeypatch):
    calls = _mock_work(monkeypatch, config, db)
    hosted = music._hosted_json
    def abstain(*args, **kwargs):
        answer = hosted(*args, **kwargs)
        return {"choices": []} if kwargs.get("operation") == "next-scene-select" else answer
    monkeypatch.setattr(music, "_hosted_json", abstain)
    render = MagicMock()
    monkeypatch.setattr(next_scene_media, "render_preview", render)
    original = store.get_project(project["id"])
    result = next_scene.run(_job(project), config, db, store, lambda _: None)
    assert result["candidates"] == [] and result["message"].startswith("No suitable next scene")
    assert len(calls) == 2 and result["hosted_request_count"] == 2
    render.assert_not_called()
    assert store.get_project(project["id"]) == original


def test_absent_music_is_interpreted_privately_without_committing_timeline_or_analysis(config, store, db, project, monkeypatch):
    calls = _mock_work(monkeypatch, config, db)
    project = deepcopy(project); project["document"]["analysis"] = None
    before = deepcopy(project["document"])
    result = next_scene.run(_job(project), config, db, store, lambda _: None)
    assert len(calls) == 3 and calls[0][2].get("audio_path") is not None
    assert result["hosted_request_count"] == 3 and result["music_provenance"]["track"] == before["track"]["id"]
    assert project["document"] == before
    proposed = next_scene.proposal_document(before, result["scope"], result["candidates"][0], db)
    assert proposed["analysis"] is None and proposed["rhythm"] is None


def test_written_following_query_skips_intent_llm(config, store, db, project, monkeypatch):
    calls = _mock_work(monkeypatch, config, db)
    project = deepcopy(project)
    project["document"]["music_timeline"]["slots"][2].update(direction=_direction("my precise image"), direction_source="user")
    result = next_scene.run(_job(project), config, db, store, lambda _: None)
    assert [call[0] for call in calls] == ["next-scene-select"]
    assert result["candidates"][0]["direction"]["query"] == "my precise image"


def test_no_candidates_or_preview_failure_never_fakes_a_playable_result(config, store, db, project, monkeypatch):
    calls = _mock_work(monkeypatch, config, db, count=0)
    result = next_scene.run(_job(project), config, db, store, lambda _: None)
    assert result["candidates"] == [] and len(calls) == 1
    calls = _mock_work(monkeypatch, config, db, preview_failure=True)
    result = next_scene.run(_job(project), config, db, store, lambda _: None)
    assert len(result["candidates"]) == 3 and all(not candidate["preview_ready"] for candidate in result["candidates"])
    assert all(candidate["preview_error"] == "Preview encoding failed" for candidate in result["candidates"])
    assert result["message"].startswith("0 previews ready")


@pytest.mark.parametrize("at", ["Planning complementary", "Choosing complementary", "Preparing transition"])
def test_cancellation_at_stage_boundaries_stops_later_work(config, store, db, project, monkeypatch, at):
    calls = _mock_work(monkeypatch, config, db)
    def progress(message):
        if message.startswith(at):
            raise JobCancelled("Cancelled")
    with pytest.raises(JobCancelled):
        next_scene.run(_job(project), config, db, store, progress)
    assert len(calls) == {"Planning complementary": 0, "Choosing complementary": 1, "Preparing transition": 2}[at]


@pytest.mark.parametrize("frame_match", [True, False])
def test_frame_inspection_has_one_selector_and_only_sampled_offers_without_leaking_paths(config, store, db, project, monkeypatch, frame_match):
    calls = _mock_work(monkeypatch, config, db)
    if not frame_match:
        # Text-only retrieval has explicit null evidence, as in the real index.
        monkeypatch.setattr(next_scene, "execute_search", lambda *_: [next_scene._unit(db, f"source-{i}") for i in range(8)])
    def sample(anchor, offers, *_args, **_kwargs):
        assert len(offers) == 6
        for offer in offers:
            source = next_scene._unit(db, offer["unit_id"])
            assert source["t_start"] <= offer["source_start"] < offer["source_end"] <= source["t_end"]
            assert offer["source_end"] - offer["source_start"] == pytest.approx(8.)
            if not frame_match:
                assert offer["source_start"] == source["t_start"]
        windows = [{"id": row["id"], "role": "anchor" if i == 0 else "offer", "provenance": {"source_start": row["source_start"], "source_end": row["source_end"]},
                    "frames": [{"path": "private/frame.jpg", "file": "frame.jpg", "timestamp": row["source_start"], "frame_end": row["source_start"] + .04}]}
                   for i, row in enumerate([anchor, *offers])]
        return {"profile": "sampled", "windows": windows, "images": [{"path": "private/frame.jpg", "label": "actual PTS"}], "limitation": "stills are not motion"}
    monkeypatch.setattr(next_scene_media, "sample_frames", sample)
    result = next_scene.run(_job(project, inspect_frames=True), config, db, store, lambda _: None)
    assert len(calls) == 2 and calls[-1][2]["images"]
    assert len(calls[-1][1]["offers"]) == 6 and result["selection_offer_count"] == 6
    assert "private/frame.jpg" not in json.dumps(result)
    assert all(len(candidate["inspection"]["windows"]) == 2 for candidate in result["candidates"])


@pytest.mark.parametrize("hint,expected", [(None, 60.), (True, 60.), ("61", 60.), ({}, 60.),
                                           (float("nan"), 60.), (float("inf"), 60.), (-float("inf"), 60.),
                                           (59., 60.), (65., 60.), (61.25, 61.25), (64., 64.)])
def test_inspection_window_validates_optional_hint_and_preserves_initial_cut_duration(hint, expected):
    offer = {"unit_id": "source", "film_id": "film", "t_start": 60., "t_end": 72.,
             "feasible_cut": {"min": 18.03, "max": 22.03},
             "cut_options": [{"cut": 20.03, "duration": 8., "source_start_min": 60., "source_start_max": 64.}],
             "searches": {"search": {"evidence": {"suggested_source_start": hint}}}}
    sampled = next_scene._sample_window(offer)
    assert sampled["source_start"] == expected and sampled["source_end"] == expected + 8.
    assert sampled["source_end"] <= offer["t_end"]


def test_selected_inspection_failure_has_no_caption_fallback(config, store, db, project, monkeypatch):
    calls = _mock_work(monkeypatch, config, db)
    monkeypatch.setattr(next_scene_media, "sample_frames", MagicMock(side_effect=RuntimeError("Decode unavailable")))
    with pytest.raises(RuntimeError, match="Decode unavailable"):
        next_scene.run(_job(project, inspect_frames=True), config, db, store, lambda _: None)
    assert len(calls) == 1


def test_unsupported_inspection_provider_is_rejected_before_any_hosted_work(config, store, db, project, monkeypatch):
    calls = _mock_work(monkeypatch, config, db)
    config.lab.music_provider = "gemini"
    with pytest.raises(ValueError, match="Frame inspection requires"):
        next_scene.run(_job(project, inspect_frames=True), config, db, store, lambda _: None)
    assert calls == []


def test_pool_interleaves_ideas_and_is_bounded_to_24_offers(config, db, project, monkeypatch):
    scope = _scope(project["document"], db)
    queries = []
    def retrieve(resolved, *_):
        text = resolved["clauses"][0]["text"]; queries.append(text)
        ids = range(0, 25) if text == "many" else [27, 28]
        return [next_scene._unit(db, f"source-{i}") for i in ids]
    monkeypatch.setattr(next_scene, "execute_search", retrieve)
    offers, _, count = next_scene._offers(project["document"], scope, [_direction("many"), _direction("rare")], [], _caps(config), config, db, lambda _: None)
    assert len(offers) == 24 and count == 2
    assert list(offers)[:4] == ["source-0", "source-27", "source-1", "source-28"]
    assert queries == ["many", "rare"]
