"""Near-canvas bridge media must not insert transient bars into a filled pair."""
import json

import numpy as np
import pytest

from pipeline.lab.media import run_process
from pipeline.tests.test_transition_bridges import bridge_pair as bridge_pair, complete, prepare
from pipeline.transitions import bridges, jobs


FILL = {"framing": {"fit": "fill", "anchor_x": .5, "anchor_y": .5, "zoom": 1}}
H3 = {"width": 1344, "height": 768}


@pytest.mark.parametrize("outgoing,incoming,stream,reason", [
    ({}, FILL, H3, "source-pair-does-not-both-fill"),
    (FILL, {"framing": {"fit": "fit"}}, H3, "source-pair-does-not-both-fill"),
    (FILL, FILL, {"width": 1280, "height": 800}, "aspect-difference-exceeds-bound"),
    (FILL, FILL, {**H3, "sample_aspect_ratio": "4:3"}, "non-square-or-invalid-sample-aspect"),
    (FILL, FILL, {**H3, "sample_aspect_ratio": "0:1"}, "non-square-or-invalid-sample-aspect"),
    (FILL, FILL, {**H3, "sample_aspect_ratio": "invalid"}, "non-square-or-invalid-sample-aspect"),
    (FILL, FILL, {**H3, "tags": {"rotate": "90"}}, "rotated-or-ambiguous-display-geometry"),
    (FILL, FILL, {**H3, "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]},
     "rotated-or-ambiguous-display-geometry"),
    (FILL, FILL, {**H3, "side_data_list": [{"side_data_type": "Display Matrix"}]},
     "rotated-or-ambiguous-display-geometry"),
])
def test_bridge_framing_keeps_fit_outside_narrow_policy(outgoing, incoming, stream, reason):
    framing, receipt = bridges._bridge_framing(854, 480, outgoing, incoming, stream)
    assert framing == {"fit": "fit", "anchor_x": .5, "anchor_y": .5, "zoom": 1}
    assert receipt["applied"] is False and receipt["reason"] == reason


@pytest.mark.parametrize("sar", [None, "1:1"])
def test_h3_grid_gets_only_centered_bounded_fill(sar):
    framing, receipt = bridges._bridge_framing(854, 480, FILL, FILL, {**H3, "sample_aspect_ratio": sar})
    assert framing == {"fit": "fill", "anchor_x": .5, "anchor_y": .5, "zoom": 1}
    assert receipt["applied"] is True
    assert receipt["coded_aspect_ratio_mismatch"] == pytest.approx(1 / 60)
    assert receipt["maximum_aspect_ratio_mismatch"] == .02
    assert receipt["missing_sample_aspect_assumed_square"] is (sar is None)


@pytest.mark.parametrize("width,height,applied", [(1020, 1000, True), (1021, 1000, False),
                                                (1000, 1020, True), (1000, 1021, False)])
def test_aspect_threshold_is_symmetric_and_strictly_bounded(width, height, applied):
    framing, receipt = bridges._bridge_framing(480, 480, FILL, FILL, {"width": width, "height": height})
    assert receipt["applied"] is applied
    assert framing["fit"] == ("fill" if applied else "fit")


def _frame(path, timestamp, width=854, height=480):
    raw = run_process(["ffmpeg", "-v", "error", "-ss", str(timestamp), "-i", str(path),
                       "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
    return np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)


def test_real_h3_dimensions_fill_only_bridge_bars_and_preserve_owned_original(config, bridge_pair, monkeypatch):
    store, parent_id, _path, films = bridge_pair
    prior_parent = store.get_job(parent_id, private=True)
    prior_manifest = jobs.artifact(config, prior_parent, "manifest")
    prior_hash = jobs.sha256(prior_manifest)
    request = prior_parent["snapshot"]["transition_render"]["request"]
    for side in ("outgoing", "incoming"):
        request[side]["framing"] = dict(FILL["framing"])
    monkeypatch.setattr(jobs, "dimensions", lambda _output: (854, 480))
    parent = store.enqueue_transition_render(jobs.freeze(request, None))
    result = jobs.run(store.claim(role="editor"), config, None, store, lambda _: None, lambda: False)
    store.finish(parent["id"], result=result)
    original = config.paths.films_dir / "native-h3-grid.mp4"
    run_process(["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i",
                 "color=lime:s=1344x768:r=24", "-t", "0.5", "-c:v", "libx264", "-threads", "1", str(original)])
    pair = (store, parent["id"], original, films)
    proposal = prepare(config, pair, playback_duration=.4)
    original_hash = jobs.sha256(original)
    input_path = jobs.output_root(config, proposal["job_id"]) / "input.json"
    input_hash = jobs.sha256(input_path)
    job = complete(config, pair, proposal)
    manifest = json.loads(bridges.artifact(config, job, "manifest").read_text())
    receipt = manifest["normalization"][1]
    assert receipt["framing"]["fit"] == "fill"
    assert receipt["bridge_framing"]["policy"] == "near-canvas-native-grid-correction-v1"
    assert receipt["bridge_framing"]["applied"] is True
    assert receipt["bridge_framing"]["selected_framing"] == receipt["framing"]
    video = bridges.artifact(config, job, "video")
    # Both sides of both joins remain filled. A solid, bright fixture makes
    # artificial black columns unambiguous even after H.264 chroma compression.
    for timestamp, channel in ((23 / 30, 0), (24 / 30, 1), (35 / 30, 1), (36 / 30, 2)):
        frame = _frame(video, timestamp)
        for edge in (frame[:, :7], frame[:, -7:]):
            assert float(edge[:, :, channel].mean()) > 200
    assert manifest["segment_frame_counts"] == [24, 12, 24]
    assert jobs.sha256(bridges.artifact(config, job, "original")) == original_hash
    assert jobs.sha256(input_path) == input_hash
    assert jobs.sha256(prior_manifest) == prior_hash
    assert bridges.BRIDGE_VERSION == "imported-transition-bridge-v2"
