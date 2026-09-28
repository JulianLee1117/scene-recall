"""Match Cuts tests target source identity, time, evidence and edit boundaries."""

from unittest.mock import patch

import numpy as np
import pytest
from pydantic import ValidationError

from pipeline.experiments.region_geometry import Box, Picture
from pipeline.lab.models import JobRequest, ProjectDocument
from pipeline.lab.media import run_process, JobCancelled
from pipeline.lab.store import LabStore
from pipeline.matching import cohort, media, motion, visual
from pipeline.matching.service import reference, profile


def test_camera_pan_is_not_residual_subject_movement():
    flow = np.zeros((128, 192, 2), dtype=np.float32)
    flow[:, :, 0] = 6
    item = motion.summarize(flow, 0.2)
    assert item["reliable"] and item["confidence"] > 0.98
    assert item["camera"][0] == pytest.approx(6 / 192 / 0.2, abs=1e-5)
    assert np.abs(item["residual"]).max() < 1e-5
    assert motion.descriptor([item] * 6, "camera") is not None
    assert motion.descriptor([item] * 6, "subject", Box(0.25, 0.25, 0.5, 0.5)) is None


def test_subject_motion_survives_camera_removal():
    flow = np.zeros((192, 192, 2), dtype=np.float32)
    flow[:, :, 0] = 3
    flow[64:128, 64:128, 1] = 8
    item = motion.summarize(flow, 0.2)
    assert item["reliable"]
    descriptor = motion.descriptor(
        [item] * 6, "subject", Box(1 / 3, 1 / 3, 1 / 3, 1 / 3)
    )
    assert descriptor is not None and descriptor[:, 1].mean() > 0.15
    assert abs(descriptor[:, 0].mean()) < 1e-5


def test_ambiguous_flow_is_unknown_not_static():
    flow = np.random.default_rng(0).normal(0, 30, (128, 192, 2)).astype(np.float32)
    item = motion.summarize(flow, 0.2)
    assert not item["reliable"]
    assert motion.descriptor([item] * 6, "camera") is None


def test_motion_scores_direction_speed_and_temporal_order():
    movement = np.array([[1.0, 0], [2.0, 0], [3.0, 0]])
    assert motion.similarity(movement, movement) == pytest.approx(1)
    assert motion.similarity(movement, -movement) < 0
    assert motion.similarity(movement, movement * 4) < 0.3
    assert motion.similarity(movement, movement[::-1]) < 0.8


def test_visual_regions_are_bounded_and_class_agnostic():
    boxes = visual.regions(Box(0.3, 0.3, 0.2, 0.25))
    assert len(boxes) <= 32
    assert all(
        0 <= box.x < 1
        and box.x + box.width <= 1.000001
        and box.y + box.height <= 1.000001
        for box in boxes
    )
    features = np.random.default_rng(2).normal(size=(28, 28, 16)).astype(np.float32)
    kwargs = dict(
        reference_picture=Picture(1920, 1080),
        candidate_picture=Picture(1920, 1080),
        output=Picture(1920, 1080),
    )
    match = visual.compare(
        features, Box(0, 0, 1, 1), None, features, Box(0, 0, 1, 1), **kwargs
    )
    assert match["correspondence"] == pytest.approx(1, abs=1e-6)
    assert match["crop"] is None
    cropped = visual.compare(
        features,
        Box(0, 0, 1, 1),
        Box(0.25, 0.25, 0.5, 0.5),
        features,
        Box(0, 0, 1, 1),
        allow_crop=True,
        **kwargs,
    )
    assert cropped and cropped["crop"]["candidate_crop"]


def test_real_decode_returns_adjacent_pts_even_when_subsampled(tmp_path):
    path = tmp_path / "long-gop.mp4"
    run_process(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=192x128:rate=25:duration=3",
            "-c:v",
            "libx264",
            "-g",
            "75",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
    )
    decoded = media.samples(path, 0.51, 1.8, fps=4)
    assert len(decoded) >= 4
    assert all(
        sample.end - sample.time == pytest.approx(0.04, abs=1e-6) for sample in decoded
    )
    assert decoded[0].time == pytest.approx(0.52)
    chosen = media.at(path, 0.533, 0, 3)
    assert chosen.time == pytest.approx(0.52)
    assert chosen.end == pytest.approx(0.56)
    with pytest.raises(JobCancelled):
        media.samples(path, 0, 1, cancelled=lambda: True)
    with pytest.raises(ValueError):
        media.samples(path, 0, 5)


def test_preview_boundary_check_reads_played_frames_and_rejects_mismatch(tmp_path):
    from pipeline.lab.matching import verify_boundaries, render_candidate

    source, wrong = tmp_path / "source.mp4", tmp_path / "wrong.mp4"
    for path, pattern in (
        (source, "testsrc2=size=320x180:rate=24:duration=2"),
        (wrong, "color=black:size=320x180:rate=24:duration=2"),
    ):
        run_process(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                pattern,
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ]
        )
    manifest = {
        "width": 320,
        "height": 180,
        "clips": [
            {
                "film_id": "film",
                "source_start": 0.0,
                "source_end": 1.0,
                "frame_count": 24,
                "crop": None,
            },
            {
                "film_id": "film",
                "source_start": 1.0,
                "source_end": 2.0,
                "frame_count": 24,
                "crop": None,
            },
        ],
    }
    with patch("pipeline.lab.media.resolve_film", return_value={"path": str(source)}):
        assert verify_boundaries(source, manifest, None, [23 / 24, 1])["passed"]
        assert not verify_boundaries(wrong, manifest, None, [23 / 24, 1])["passed"]
        with pytest.raises(ValueError, match="source footage changed"):
            render_candidate(
                "preview",
                {},
                {"outgoing": manifest["clips"][0], "incoming": manifest["clips"][1]},
                None,
                None,
                None,
                lambda message: None,
                lambda: False,
            )


def test_match_request_cannot_attach_options_to_other_jobs():
    options = {"cohort_id": "cohort-" + "a" * 16, "reference_clip_id": "reference"}
    assert (
        JobRequest(kind="match", base_revision=1, match=options).match.mode == "image"
    )
    with pytest.raises(ValidationError):
        JobRequest(kind="match", base_revision=1)
    with pytest.raises(ValidationError):
        JobRequest(kind="render", base_revision=1, match=options)
    with pytest.raises(ValidationError):
        JobRequest(
            kind="match",
            base_revision=1,
            match={**options, "cohort_id": "../../outside"},
        )


def test_reference_bounds_and_subject_region_are_enforced():
    clip = {
        "id": "a",
        "film_id": "film",
        "unit_id": "unit",
        "source_start": 2,
        "source_end": 6,
        "reference_time": 4,
        "region": None,
    }
    source = {"unit_id": "unit", "film_id": "film", "t_start": 2, "t_end": 6}
    options = {"reference_clip_id": "a", "mode": "movement", "movement": "subject"}
    with patch("pipeline.matching.service.cohort.unit", return_value=source):
        with pytest.raises(ValueError, match="moving subject"):
            reference(None, {"clips": [clip]}, options)
        options["movement"] = "camera"
        assert reference(None, {"clips": [clip]}, options)[2] == 4
        with pytest.raises(ValueError, match="before the end"):
            reference(None, {"clips": [{**clip, "reference_time": 6}]}, options)
        with pytest.raises(ValueError, match="within its indexed shot"):
            reference(None, {"clips": [{**clip, "source_end": 7}]}, options)


def test_match_snapshot_and_completion_never_modify_project(config):
    store = LabStore(config.paths.state_dir)
    store.initialize()
    project = store.create_project("Reference", "visual-rhymes")
    options = {
        "cohort_id": "cohort-" + "a" * 16,
        "profile_id": "frozen",
        "reference_clip_id": "a",
    }
    job = store.enqueue("match", project["id"], 1, match=options)
    claimed = store.claim()
    assert claimed["snapshot"]["match"] == options
    store.update_project(
        project["id"], 1, {**project["document"], "brief": "later edit"}
    )
    store.finish(job["id"], result={"candidates": []})
    assert store.get_project(project["id"])["revision"] == 2
    assert store.get_project(project["id"])["document"]["brief"] == "later edit"


def test_profiles_require_complete_content_and_correct_identity(config):
    identity = "cohort-" + "a" * 16
    with pytest.raises(ValueError, match="DINOv3"):
        profile(config, identity, "image")
    path = cohort.cohort_path(config, identity) / "visual" / "manifest.json"
    payload = {"cohort_id": identity, "complete": False}
    cohort.write_new(path, {**payload, "id": cohort.digest(payload)})
    with pytest.raises(ValueError, match="incomplete"):
        profile(config, identity, "image")


@pytest.mark.parametrize("locked,stale", [(False, False), (True, False), (False, True)])
def test_match_apply_uses_saved_candidate_and_revision_guard(
    config, tmp_path, locked, stale
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pipeline.lab.api import router
    from pipeline.index.writer import create_tables, open_db

    db = open_db(config)
    create_tables(db, vector_dim=4)
    source = tmp_path / "film.mp4"
    source.write_bytes(b"source-exists")
    db.open_table("films").add(
        [
            {
                "film_id": "film",
                "title": "Film",
                "path": str(source),
                "duration": 10.0,
                "fps": 24.0,
            }
        ]
    )
    store = LabStore(config.paths.state_dir)
    store.initialize()
    project = store.create_project("Match", "visual-rhymes")
    clip = {
        "id": "a",
        "film_id": "film",
        "source_start": 1.0,
        "source_end": 3.0,
        "locked": locked,
    }
    doc = ProjectDocument(clips=[clip]).model_dump(mode="json")
    project = store.update_project(project["id"], 1, doc)
    job = store.enqueue("match", project["id"], 2, match={})
    store.claim()
    candidate_id = "match-" + "a" * 16
    outgoing = {**doc["clips"][0], "source_end": 2.0}
    incoming = {
        **doc["clips"][0],
        "id": candidate_id,
        "source_start": 4.0,
        "source_end": 5.0,
    }
    store.finish(
        job["id"],
        result={
            "reference_clip_id": "a",
            "candidates": [
                {"id": candidate_id, "outgoing": outgoing, "incoming": incoming}
            ],
        },
    )
    if stale:
        project = store.update_project(project["id"], 2, {**doc, "brief": "newer edit"})
    app = FastAPI()
    app.include_router(router)
    app.state.lab, app.state.db, app.state.config = store, db, config
    with TestClient(app) as client:
        preview_url = f"/lab/jobs/{job['id']}/matches/{candidate_id}/preview"
        preview = client.post(preview_url)
        assert preview.status_code == 200
        pending = store.get_job(preview.json()["id"], private=True)
        assert pending["kind"] == "match-preview"
        assert pending["snapshot"]["match"]["document"] == doc
        assert store.get_project(project["id"]) == project
        assert client.get(preview_url).status_code == 409
        store.cancel(pending["id"])
        result = client.post(
            f"/lab/jobs/{job['id']}/apply-match",
            json={"base_revision": project["revision"], "candidate_id": candidate_id},
        )
        assert result.status_code == (409 if locked or stale else 200)
        current = store.get_project(project["id"])
        if locked or stale:
            assert current == project
        else:
            assert current["revision"] == 3
            assert current["document"]["clips"] == [outgoing, incoming]
        assert (
            client.get(
                f"/lab/jobs/{job['id']}/matches/match-{'b' * 16}/frame"
            ).status_code
            == 404
        )
