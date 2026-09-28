"""Source-exact group requirements survive broad, unverified still recall."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from pipeline.lab.media import JobCancelled
from pipeline.matching import cohort, library_search as engine, media, search


def person(x=.4, y=.5):
    return {"visible": True, "centroid": [x, y], "area": .035, "picture_aspect": 16 / 9,
            "box": {"x": x - .08, "y": y - .2, "width": .16, "height": .4},
            "silhouette": [1.] * 64}


def pair():
    return [person(.38), person(.62)]


def image(code):
    return Image.new("RGB", (24, 16), (code, 0, 0))


@pytest.fixture
def scene(monkeypatch, tmp_path):
    state = SimpleNamespace(config=SimpleNamespace(paths=SimpleNamespace(assets_dir=tmp_path)),
                            source={"unit_id": "source", "film_id": "a", "t_start": 0., "t_end": 8.},
                            options={"cohort_id": "cohort-test", "focus": "auto", "timing": "fixed",
                                     "film_ids": [], "include_source_film": False, "min_incoming_seconds": 1.,
                                     "reference": {"unit_id": "source", "time": 5., "region": None, "subject_point": None}},
                            snapshot={"films": [{"film_id": "a"}, {"film_id": "b"}], "frame_count": 120, "unit_count": 60},
                            descriptions=[], sample_calls=[], seed_calls=[], progress=[], published=[], seeds=[],
                            native_people=pair(), seed_people=pair(), anchor_people=pair(), nearby_people=pair(),
                            cancelled=False, during_description=None)
    state.identity = {"people": "people-profile", "library": deepcopy(state.snapshot), "search_contract": "test"}
    state.anchor = media.Sample(5.04, 5.08, image(1))
    state.references = [state.anchor]
    state.starts = {5.04: 4.04, 5.5: 4.5}
    (tmp_path / "a.mp4").write_bytes(b"source")
    (tmp_path / "b.mp4").write_bytes(b"candidate")

    def add_seed(index=0):
        path = tmp_path / f"still-{index}.png"
        image(10 + index).save(path)
        state.seeds.append({"unit_id": f"candidate-{index}", "film_id": "b", "t_start": 9., "t_end": 13.,
                            "film_path": str(tmp_path / "b.mp4"), "film_title": "Candidate film", "caption": "",
                            "time": 10., "path": str(path), "frame_id": f"indexed-{index}", "global_rank": index + 1,
                            "reference_frame": {"frame_id": "indexed-source", "time": 4., "path": str(path)}})
    state.add_seed = add_seed
    add_seed()

    class Detector:
        profile = {"id": "people-profile"}

        def describe(self, rgb):
            code = rgb.getpixel((0, 0))[0]
            state.descriptions.append(code)
            if state.during_description:
                state.during_description(code)
            selected = state.anchor_people if code == 1 else state.nearby_people if code == 2 else state.native_people if code >= 200 else state.seed_people
            return {"profile_id": self.profile["id"], "selected": deepcopy(selected)}

    class Evidence:
        def __init__(self, config, detector):
            self.detector, self.profile = detector, detector.profile["id"]

        def describe(self, rgb):
            return self.detector.describe(rgb)

    def samples(path, start, end, **kwargs):
        state.sample_calls.append((path, start, end))
        return [media.Sample(9.8 + index / 20, 9.85 + index / 20, image(200)) for index in range(24)]

    def at(path, timestamp, lower, upper, cancelled):
        state.seed_calls.append(timestamp)
        return media.Sample(10.012, 10.052, image(201))

    monkeypatch.setattr(engine.people, "Detector", lambda config: Detector())
    monkeypatch.setattr(engine, "PeopleEvidence", Evidence)
    monkeypatch.setattr(engine.cohort, "resolve_film", lambda db, fid: {"path": str(tmp_path / f"{fid}.mp4")})
    from pipeline.ingest import probe
    monkeypatch.setattr(probe, "_content_hash", lambda path: Path(path).stem)
    monkeypatch.setattr(search, "_references", lambda *args: (state.anchor, [state.anchor], state.references, state.starts))
    monkeypatch.setattr(engine.library_retrieval, "library_identity", lambda *args: deepcopy(state.snapshot))
    monkeypatch.setattr(engine.library_retrieval, "retrieve", lambda *args: deepcopy(state.seeds))
    monkeypatch.setattr(engine.media, "samples", samples)
    monkeypatch.setattr(engine.media, "at", at)

    def run():
        return engine.find(state.config, None, state.options, state.source, {}, state.identity,
                           state.progress.append, lambda: state.cancelled, state.published.append)
    state.run = run
    return state


def test_exact_people_layout_publishes_native_timestamps_and_owned_callback_copy(scene):
    result = scene.run()
    assert result["reference_summary"] == {"kind": "people", "count": 2}
    candidate = result["candidates"][0]
    assert candidate["primary_cue"] == "layout"
    assert candidate["reference_frame_pts"] == 5.04
    assert candidate["candidate_frame_pts"] != candidate["retrieval_evidence"]["frame_time"]
    assert candidate["retrieval_evidence"]["frame_id"] == "indexed-0"
    assert candidate["cues"][0]["measurements"]["reference_frame_pts"] == 5.04
    assert candidate["cues"][0]["measurements"]["candidate_frame_pts"] == candidate["candidate_frame_pts"]
    scene.published[0]["cues"][0]["description"] = "caller changed its copy"
    assert result["candidates"][0]["cues"][0]["description"] != "caller changed its copy"


def test_missing_partner_in_retained_still_never_falls_back_to_camera(scene, monkeypatch):
    scene.seed_people = [person()]
    monkeypatch.setattr(engine, "_generic_pairs", lambda *args: pytest.fail("People requirements cannot fall back to camera"))
    result = scene.run()
    assert result["candidates"] == scene.published == []
    assert not scene.sample_calls
    assert any("No verified arrangement of 2" in notice for notice in result["notices"])


def test_more_than_three_foreground_people_abstains_without_camera_fallback(scene, monkeypatch):
    scene.anchor_people = [person(x) for x in (.2, .4, .6, .8)]
    monkeypatch.setattr(engine, "_generic_pairs", lambda *args: pytest.fail("An unsupported group cannot become a camera match"))
    result = scene.run()
    assert result["candidates"] == []
    assert result["reference_summary"] is None
    assert not scene.sample_calls
    assert any("more than 3 prominent people" in notice for notice in result["notices"])


def test_matching_indexed_still_is_not_proof_when_native_frame_loses_partner(scene, monkeypatch):
    scene.native_people = [person()]
    monkeypatch.setattr(engine, "_generic_pairs", lambda *args: pytest.fail("Missing native partner must abstain"))
    result = scene.run()
    assert result["coverage"]["screened_frame_count"] == 1
    assert result["coverage"]["refined_window_count"] == 1
    assert result["candidates"] == scene.published == []


def test_nearby_reference_cannot_silently_drop_one_source_person(scene):
    scene.options["timing"] = "nearby"
    scene.references.append(media.Sample(5.5, 5.54, image(2)))
    scene.nearby_people = [person()]
    result = scene.run()
    assert result["candidates"][0]["reference_frame_pts"] == scene.anchor.time
    assert result["candidates"][0]["cues"][0]["measurements"]["reference_people"] == 2


def test_exact_native_seed_is_checked_even_when_sampling_grid_misses_it(scene, monkeypatch):
    original = engine._layout_cues

    def only_seed_matches(reference, candidate, outgoing, incoming):
        return original(reference, candidate, outgoing, incoming) if candidate.time == 10.012 else None

    monkeypatch.setattr(engine, "_layout_cues", only_seed_matches)
    result = scene.run()
    assert scene.seed_calls == [10.]
    assert result["candidates"][0]["candidate_frame_pts"] == 10.012


def test_work_is_bounded_to_48_stills_10_windows_and_4_native_frames_per_window(scene):
    for index in range(1, 60):
        scene.add_seed(index)
    scene.native_people = [person()]  # exhaust the bounded refiner without hits
    result = scene.run()
    assert result["coverage"]["screened_frame_count"] == 48
    assert result["coverage"]["shortlist_count"] == result["coverage"]["refined_window_count"] == 10
    assert len(scene.sample_calls) == 10 and len(scene.seed_calls) == 10
    assert sum(code >= 200 for code in scene.descriptions) == 40
    assert len(scene.descriptions) == 1 + 48 + 40


def test_three_verified_results_stop_after_initial_five_windows(scene):
    for index in range(1, 20):
        scene.add_seed(index)
    result = scene.run()
    assert len(result["candidates"]) == len(scene.sample_calls) == 5


def test_explicit_subject_point_uses_bounded_legacy_verification(scene, monkeypatch):
    for index in range(1, 60):
        scene.add_seed(index)
    scene.options["reference"]["subject_point"] = {"x": .3, "y": .4}
    calls = []
    monkeypatch.setattr(search, "_enabled", lambda *args: {"camera"})
    monkeypatch.setattr(search, "_models", lambda *args: (None, object()))

    def measure(rows, tracker, flow, enabled, cancelled, **kwargs):
        calls.append(kwargs)
        return [], []

    monkeypatch.setattr(search, "_measure", measure)
    monkeypatch.setattr(search, "_best_pair", lambda *args: None)
    result = scene.run()
    assert result["reference_summary"] is None
    assert result["coverage"]["screened_frame_count"] == 0
    assert result["coverage"]["refined_window_count"] == len(scene.sample_calls) == 10
    assert calls[0]["point"] == (.3, .4) and calls[0]["seed_time"] == 5.04
    assert len(calls) == 11 and not scene.descriptions


def test_cancellation_during_native_detection_cannot_publish_a_candidate(scene):
    # Cancel during the LAST detector call, after the loop's final pre-call
    # cancellation check. Publication itself must check cancellation again.
    def cancel_last_frame(code):
        if code >= 200 and sum(value >= 200 for value in scene.descriptions) == 4:
            scene.cancelled = True
    scene.during_description = cancel_last_frame
    with pytest.raises(JobCancelled):
        scene.run()
    assert scene.published == []


def test_changed_candidate_source_is_rejected_before_native_decoding(scene, monkeypatch):
    from pipeline.ingest import probe
    monkeypatch.setattr(probe, "_content_hash", lambda path: "a" if Path(path).stem == "a" else "different")
    with pytest.raises(ValueError, match="[Cc]andidate|[Ss]ource.*changed"):
        scene.run()
    assert not scene.sample_calls and not scene.published


def test_changed_reference_source_is_rejected_before_discovery(scene, monkeypatch):
    from pipeline.ingest import probe
    monkeypatch.setattr(probe, "_content_hash", lambda path: "different")
    with pytest.raises(ValueError, match="Reference source identity changed"):
        scene.run()
    assert not scene.descriptions and not scene.sample_calls and not scene.published


def test_stale_library_before_progressive_publication_rejects_all_results(scene):
    def change_index(code):
        if code >= 200:
            scene.snapshot["frame_count"] += 1
    scene.during_description = change_index
    with pytest.raises(ValueError, match="[Ll]ibrary|index"):
        scene.run()
    assert scene.published == []


def test_prepared_detector_profile_must_match_queued_identity(scene):
    scene.identity["people"] = "older-people-profile"
    with pytest.raises(ValueError, match="People model changed"):
        scene.run()
    assert scene.published == []


@pytest.mark.parametrize("corruption", ["json", "checksum", "profile"])
def test_corrupt_people_cache_is_recomputed_and_valid_cache_is_reused(tmp_path, corruption):
    calls = []

    class Detector:
        profile = {"id": "people-profile"}

        def describe(self, rgb):
            calls.append(rgb.size)
            return {"profile_id": "people-profile", "selected": pair()}

    config = SimpleNamespace(paths=SimpleNamespace(assets_dir=tmp_path))
    evidence = engine.PeopleEvidence(config, Detector())
    expected = evidence.describe(image(1))
    assert evidence.describe(image(1)) == expected and len(calls) == 1
    path = next(evidence.directory.glob("*.json"))
    cached = json.loads(path.read_text())
    if corruption == "json":
        path.write_text("{broken")
    elif corruption == "checksum":
        cached["result"]["selected"] = []
        path.write_text(json.dumps(cached))
    else:
        cached["result"]["profile_id"] = "other-profile"
        cached["result_sha256"] = cohort.digest(cached["result"])
        path.write_text(json.dumps(cached))
    assert evidence.describe(image(1)) == expected and len(calls) == 2
    assert evidence.describe(image(1)) == expected and len(calls) == 2


def test_detector_profile_change_cannot_write_reusable_evidence(tmp_path):
    detector = SimpleNamespace(profile={"id": "pinned"}, describe=lambda image: {"profile_id": "changed", "selected": pair()})
    evidence = engine.PeopleEvidence(SimpleNamespace(paths=SimpleNamespace(assets_dir=tmp_path)), detector)
    with pytest.raises(ValueError, match="People detector changed"):
        evidence.describe(image(1))
    assert not evidence.directory.exists()
