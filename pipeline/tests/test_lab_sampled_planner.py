"""Sampled footage enters the existing single-attempt planner as actual images."""
import base64
import hashlib
import json

import httpx
from PIL import Image
import pytest

from pipeline.lab import music
from pipeline.tests.test_lab_openai_music import _client, _stream


def test_sampled_selector_sends_images_with_labels_and_private_hashed_receipt(config, tmp_path, monkeypatch):
    frame = tmp_path / "source-frame.jpg"
    Image.new("RGB", (64, 48), "orange").save(frame)
    original = frame.read_bytes()
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=_stream('{"choices":[]}'))

    _client(monkeypatch, handle)
    receipt = tmp_path / "sampled.json"
    output = music._hosted_json(config, "Compare the supplied windows.", {}, receipt_path=receipt,
                               operation="next-scene-select", images=[{"path": frame, "label": "Incoming at source 12.25s"}])
    assert output == {"choices": []} and len(requests) == 1
    content = requests[0]["messages"][1]["content"]
    assert content[1] == {"type": "text", "text": "Incoming at source 12.25s"}
    assert content[2]["type"] == "image_url"
    assert base64.b64decode(content[2]["image_url"]["url"].split(",", 1)[1]) == original
    assert requests[0]["store"] is False
    assert requests[0]["response_format"]["json_schema"]["strict"] is True
    saved = json.loads(receipt.read_text())
    assert saved["contract"] == music.SAMPLED_PLANNER_CONTRACT
    assert saved["images"][0]["sha256"] == hashlib.sha256(original).hexdigest()
    assert "data:image" not in receipt.read_text() and str(frame) not in receipt.read_text()
    assert frame.read_bytes() == original


def test_sampled_selector_is_explicit_and_does_not_fall_back(config, tmp_path):
    config.lab.music_provider = "gemini"
    with pytest.raises(music.MusicUnavailable, match="OpenAI text planner"):
        music._hosted_json(config, "Inspect", {}, receipt_path=tmp_path / "unused.json", images=[])
    assert not (tmp_path / "unused.json").exists()


@pytest.mark.parametrize("images", [[], [{}] * 22])
def test_sampled_selector_bounds_before_any_hosted_attempt(config, tmp_path, images):
    with pytest.raises(ValueError, match="one and 21"):
        music._hosted_json(config, "Inspect", {}, receipt_path=tmp_path / "unused.json", images=images)
    assert not (tmp_path / "unused.json").exists()


def test_invalid_sample_is_never_sent(config, tmp_path):
    frame = tmp_path / "invalid.jpg"
    frame.write_text("not a derived frame")
    with pytest.raises(ValueError, match="derived JPEG"):
        music._hosted_json(config, "Inspect", {}, receipt_path=tmp_path / "unused.json",
                           images=[{"path": frame, "label": "sample"}])


def test_sampled_selector_provider_failure_is_one_attempt(config, tmp_path, monkeypatch):
    frame = tmp_path / "frame.jpg"
    Image.new("RGB", (8, 8)).save(frame)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(503, json={"error": {"message": "busy"}})

    _client(monkeypatch, handle)
    with pytest.raises(music.MusicUnavailable, match="busy"):
        music._hosted_json(config, "Inspect", {}, receipt_path=tmp_path / "failed.json",
                           images=[{"path": frame, "label": "sample"}])
    assert len(requests) == 1
    assert json.loads((tmp_path / "failed.json").read_text())["status"] == "failed-or-uncertain"
