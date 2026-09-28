"""Every modern matching channel must leave enough real footage for audition."""

from PIL import Image
import numpy as np
import pytest

from pipeline.lab.models import ProjectDocument
from pipeline.matching import service, subject_service
from pipeline.matching.media import Sample
from pipeline.matching.transitions import CONTRACT


@pytest.mark.parametrize("reference_end,incoming_time,allowed", [
    (0.08, 4., False),  # A shape match at the first native frame is not an audition.
    (0.52, 4., True),
    (1.04, 5.8, False),  # Refinement may move B too close to its shot end.
    (1.04, 5.5, True),
])
def test_all_modern_channels_enforce_actual_half_second_context(config, monkeypatch, reference_end, incoming_time, allowed):
    run = fixture_find(config, monkeypatch, reference_end, incoming_time, modern=True)
    assert bool(run["candidates"]) == allowed
    if not allowed:
        assert "half a second" in run["message"]


def test_legacy_proposal_assembly_remains_unchanged(config, monkeypatch):
    result = fixture_find(config, monkeypatch, 0.08, 4., modern=False)
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["outgoing"]["source_end"] == 0.08


def fixture_find(config, monkeypatch, reference_end, incoming_time, *, modern):
    document = ProjectDocument(clips=[{
        "id": "a", "film_id": "film-a", "unit_id": "a", "source_start": 0.,
        "source_end": 4., "reference_time": 1.,
    }]).model_dump(mode="json")
    source = {"unit_id": "a", "film_id": "film-a", "t_start": 0., "t_end": 4.}
    target = {"unit_id": "b", "film_id": "film-b", "t_start": 4., "t_end": 6., "caption": "Candidate"}
    subset = {"id": "cohort-" + "a" * 16, "units": [source, target]}
    picture = Image.new("RGB", (32, 18))
    candidate = {"unit": target, "score": 1., "reference": Sample(reference_end - .04, reference_end, picture),
                 "sample": Sample(incoming_time, incoming_time + .04, picture), "evidence_kind": "shape"}
    monkeypatch.setattr(service.cohort, "load", lambda *_: subset)
    monkeypatch.setattr(service.cohort, "verify", lambda *_: None)
    monkeypatch.setattr(service.cohort, "resolve_film", lambda *_: {"path": "source.mp4", "title": "Source"})
    monkeypatch.setattr(service, "reference", lambda *_: (document["clips"][0], source, 1.))
    monkeypatch.setattr(service, "profiles", lambda *_: {"shape": {"id": "prepared"}})
    monkeypatch.setattr(service, "profile", lambda *_: {"id": "prepared"})
    monkeypatch.setattr(service.media, "outgoing_start", lambda _, frame, *_args: max(0, frame.end - 1))
    monkeypatch.setattr("pipeline.ingest.probe._content_hash", lambda *_: "film-a")
    monkeypatch.setattr(subject_service, "candidates", lambda *_: [candidate])
    monkeypatch.setattr(service, "_image_candidates", lambda *_: [candidate])
    options = {"cohort_id": subset["id"], "reference_clip_id": "a", "mode": "image", "allow_reframing": False,
               "profile_id": {"scorer": CONTRACT, "shape": "prepared"} if modern else "prepared"}
    if modern:
        options.update(focus="image", timing="fixed")
    return service.find(config, None, document, options, lambda _: None)


def test_camera_filters_short_handles_before_the_best_choice(config, monkeypatch):
    """A high-scoring illegal boundary must not hide a legal nearby candidate."""
    from pathlib import Path
    from types import SimpleNamespace
    from pipeline.matching import transitions

    image = Image.new("RGB", (32, 18))
    source = {"unit_id": "a", "film_id": "a", "t_start": 0., "t_end": 3.}
    target = {"unit_id": "b", "film_id": "b", "t_start": 4., "t_end": 6.}
    refs = [Sample(t, t + .04, image) for t in [.1, .2, .3, .6, .8, 1.]]
    incoming = [Sample(t, t + .04, image) for t in [4., 4.2, 5.6, 5.7, 5.8, 5.9]]

    def sequence(_model, frames, *_args, **_kwargs):
        return [{"start": row.time, "end": row.end, "camera": [1.] * 4, "residual": [0., 0.]}
                for row in frames]

    def descriptor(rows, *_args):
        # Deliberately favor the too-short first A and the too-late B.
        value = 20 if rows and (rows[-1]["end"] < .5 or rows[0]["start"] > 5.5) else 1
        return np.array([[value, 0., 0., 0.]] * 3)

    monkeypatch.setattr(service.motion, "Flow", lambda *_: SimpleNamespace(profile={}))
    monkeypatch.setattr(service.motion, "sequence", sequence)
    monkeypatch.setattr(service.motion, "descriptor", descriptor)
    monkeypatch.setattr(transitions, "motion_similarity", lambda a, b: float(a[0, 0] + b[0, 0]))
    monkeypatch.setattr(service.cohort, "resolve_film", lambda _db, identity: {"path": identity})
    monkeypatch.setattr(service.media, "at", lambda *_: Sample(.1, .14, image))
    monkeypatch.setattr(service.media, "samples", lambda path, *_args, **_kwargs: refs if path == Path("a") else incoming)
    resolutions = []

    def outgoing(_path, frame, *_args):
        resolutions.append(frame.time)
        return max(0., frame.end - 1)

    monkeypatch.setattr(service.media, "outgoing_start", outgoing)
    options = {"focus": "camera", "movement": "camera", "allow_reframing": False, "candidate_budget": 1}
    results = service._motion_candidates(
        config, None, {"units": [source, target], "motion_unit_ids": ["b"]},
        {"model_directory": "model", "profile": {}, "expected_rows": 1,
         "windows": [{"unit_id": "b", "samples": sequence(None, incoming)}]},
        {"window_start": .1, "window_end": 1.1, "locked": False}, source, .1,
        options, None, lambda _: None, lambda: False,
    )
    assert len(results) == 1
    assert results[0]["reference"].time == 1.
    assert results[0]["sample"].time == 4.2
    # The duplicate anchored frame is resolved once across the shared context.
    assert resolutions.count(.1) == 1
