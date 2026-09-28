"""Reference timing, boundary continuity and immutable adjustment regressions."""
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError

from pipeline.lab.models import MatchOptions, MatchAdjust
from pipeline.lab.matching import adjusted_candidate
from pipeline.matching.media import Sample
from pipeline.matching.transitions import motion_components, fuse_channels


def test_boundary_continuation_beats_restarting_the_same_action():
    outgoing = np.array([[.01, 0], [.02, 0], [.03, 0]])
    continued = np.array([[.03, 0], [.04, 0], [.05, 0]])
    assert motion_components(outgoing, continued)["score"] > motion_components(outgoing, outgoing)["score"]
    assert not motion_components(outgoing, -continued)["reliable"]
    assert not motion_components(outgoing, continued * 10)["reliable"]
    assert not motion_components(outgoing, np.zeros((3, 2)))["reliable"]


def test_reversal_at_boundary_is_not_hidden_by_mean_direction():
    outgoing = np.array([[.3, 0], [.05, 0], [-.01, 0]])
    incoming = np.array([[.2, 0], [.2, 0], [.2, 0]])
    assert not motion_components(outgoing, incoming)["reliable"]


def test_flow_must_explain_the_picture_not_just_have_a_direction():
    from pipeline.matching.motion import photometric_support
    rng = np.random.default_rng(6)
    texture = rng.integers(30, 230, (128, 192, 3), dtype=np.uint8)
    shifted = np.roll(texture, 4, axis=1)
    displacement = np.zeros((128, 192, 2), dtype=np.float32)
    displacement[..., 0] = 4
    first = Image.fromarray(texture)
    assert photometric_support(first, Image.fromarray(shifted), displacement)["reliable"]
    noise = Image.fromarray(rng.integers(30, 230, texture.shape, dtype=np.uint8))
    assert not photometric_support(first, noise, displacement)["reliable"]
    dark = Image.new("RGB", (192, 128))
    assert not photometric_support(dark, dark, displacement)["reliable"]


def test_unreliable_entry_cannot_hide_inside_a_reliable_window():
    from pipeline.matching.motion import descriptor
    rows = [{"reliable": i >= 2, "camera": np.array([.1, 0, 0, 0])} for i in range(8)]
    assert descriptor(rows, "camera") is not None  # Frozen legacy comparison.
    modern = [{**row, "photometric": {"reliable": row["reliable"]}} for row in rows]
    assert descriptor(modern, "camera") is None
    assert descriptor([{**row, "reliable": True} for row in modern], "camera") is not None


def test_equal_area_tall_and_wide_masks_are_not_the_same_shape():
    from pipeline.matching.subject_service import shape_similarity
    base = {"visible": True, "centroid": [.5, .5], "area": .16, "silhouette": [1.] * 64,
            "box": {"width": .2, "height": .8}, "picture_aspect": 16 / 9}
    wide = {**base, "box": {"width": .8, "height": .2}}
    assert shape_similarity(base, base)["score"] == pytest.approx(1)
    assert not shape_similarity(base, wide)["reliable"]


def test_rank_fusion_never_compares_different_raw_model_scores():
    def row(name, score):
        return {"unit": {"unit_id": name}, "score": score}
    fused = fuse_channels({"image": [row("a", 1e8), row("b", 1)],
                           "camera": [row("b", .8)]})
    assert [x["unit"]["unit_id"] for x in fused] == ["b", "a"]
    assert fused[0]["retrieved_channels"] == ["image", "camera"]
    assert fused[0]["matched_channels"] == ["camera"]


def test_new_options_are_explicit_legacy_omission_and_finite():
    base = {"cohort_id": "cohort-" + "a" * 16, "reference_clip_id": "a"}
    assert MatchOptions(**base).focus is None
    assert MatchOptions(**base, focus="auto", timing="nearby").timing == "nearby"
    with pytest.raises(ValidationError):
        MatchOptions(**base, subject_point={"x": float("nan"), "y": .5})
    with pytest.raises(ValidationError):
        MatchAdjust(base_revision=1, outgoing_time=float("inf"))


def test_adjustment_resolves_pts_and_does_not_reuse_old_measurements():
    clip = {"id": "a", "unit_id": "u", "film_id": "film", "source_start": 1,
            "source_end": 2, "reference_time": 1.96, "locked": False}
    candidate = {"id": "match-" + "a" * 16, "outgoing": clip,
                 "incoming": {**clip, "id": "b"}, "components": {"motion": 1},
                 "reference_frame_pts": 1.96, "candidate_frame_pts": 1,
                 "boundary_checks": {"passed": True}, "preview_ready": True}
    with patch("pipeline.matching.cohort.unit", return_value={"film_id": "film", "t_start": 0, "t_end": 8}), \
         patch("pipeline.lab.media.resolve_film", return_value={"path": "film.mp4"}), \
         patch("pipeline.matching.media.outgoing_start", return_value=2.16), \
         patch("pipeline.matching.media.at", return_value=Sample(3.12, 3.16, Image.new("RGB", (20, 20)))):
        adjusted = adjusted_candidate(candidate, None, {"outgoing_time": 3.13})
        assert adjusted["outgoing"]["source_end"] == 3.16
        assert adjusted["reference_frame_pts"] == 3.12
        assert adjusted["components"] == {} and not adjusted["preview_ready"]
        assert "boundary_checks" not in adjusted
        assert candidate["outgoing"]["source_end"] == 2
        with pytest.raises(ValueError, match="inside"):
            adjusted_candidate(candidate, None, {"incoming_time": 9})


def test_progressive_previews_and_adjusted_apply_keep_parent_revision_guards(config):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pipeline.lab.api import router
    from pipeline.lab.store import LabStore
    from pipeline.lab.models import ProjectDocument

    store = LabStore(config.paths.state_dir)
    store.initialize()
    project = store.create_project("Match validation", "visual-rhymes")
    clip = {"id": "a", "film_id": "film", "unit_id": "u", "source_start": 1., "source_end": 3., "reference_time": 2.}
    doc = ProjectDocument(clips=[clip]).model_dump(mode="json")
    project = store.update_project(project["id"], 1, doc)
    job = store.enqueue("match", project["id"], 2, match={})
    store.claim()
    identity = "match-" + "a" * 16
    candidate = {"id": identity, "outgoing": doc["clips"][0],
                 "incoming": {**doc["clips"][0], "id": identity},
                 "reference_frame_pts": 2., "candidate_frame_pts": 1.,
                 "preview_ready": True, "original_is_proposed": True}
    partial = {"candidates": [candidate], "reference_clip_id": "a"}
    store.publish_match_progress(job["id"], partial)
    preview = config.paths.assets_dir / "lab" / "renders" / f"{job['id']}-{identity}-proposed" / "output.mp4"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"preview-fixture")
    app = FastAPI()
    app.include_router(router)
    app.state.config, app.state.lab, app.state.db = config, store, None
    with TestClient(app) as client, patch("pipeline.lab.api.validate_sources"):
        url = f"/lab/jobs/{job['id']}"
        assert client.get(url + f"/matches/{identity}/preview?original=true").status_code == 200
        assert client.post(url + "/apply-match", json={"base_revision": 2, "candidate_id": identity}).status_code == 409
        assert store.get_project(project["id"]) == project
        store.finish(job["id"], result=partial)
        modified = {**candidate, "adjusted": True,
                    "incoming": {**candidate["incoming"], "source_start": 4., "source_end": 5., "reference_time": 4.}}
        with patch("pipeline.lab.matching.adjusted_candidate", return_value=modified):
            response = client.post(url + f"/matches/{identity}/adjust", json={"base_revision": 2, "incoming_time": 4.})
        assert response.status_code == 200
        adjusted_job = response.json()
        store.claim()
        store.finish(adjusted_job["id"], result={"match_job_id": job["id"], "candidate": modified})
        adjusted_preview = config.paths.assets_dir / "lab" / "renders" / f"{adjusted_job['id']}-{identity}-proposed" / "output.mp4"
        adjusted_preview.parent.mkdir(parents=True)
        adjusted_preview.write_bytes(b"adjusted-preview-fixture")
        assert store.get_project(project["id"]) == project
        result = client.post(f"/lab/jobs/{adjusted_job['id']}/apply-match", json={"base_revision": 2, "candidate_id": identity})
        assert result.status_code == 200
        assert result.json()["document"]["clips"][1]["source_start"] == 4.
        assert client.post(f"/lab/jobs/{adjusted_job['id']}/apply-match", json={"base_revision": 3, "candidate_id": identity}).status_code == 409


def test_cancelled_match_does_not_publish_new_partial_results(config):
    from pipeline.lab.store import LabStore
    store = LabStore(config.paths.state_dir)
    store.initialize()
    project = store.create_project("Cancelled", "visual-rhymes")
    job = store.enqueue("match", project["id"], 1, match={})
    store.claim()
    store.cancel(job["id"])
    store.publish_match_progress(job["id"], {"candidates": [{"id": "late"}]})
    assert not store.get_job(job["id"])["result"]
