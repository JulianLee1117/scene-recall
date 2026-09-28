"""Actual SDK transport boundaries for audio input, streaming and no replay."""
import base64
import json

import httpx
import pytest

from pipeline.lab import music
from pipeline.lab.media import JobCancelled
from pipeline.tests.test_lab_music import _audio, _interpretation


def _client(monkeypatch, handler):
    import openai
    actual = openai.OpenAI
    monkeypatch.setenv("OPENAI_API_KEY", "fake-test-key")
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: actual(
        **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handler))))


def _stream(content):
    def chunk(delta, finish_reason=None):
        return {"id": "test-request", "created": 1, "object": "chat.completion.chunk", "model": "gpt-audio-1.5",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}
    events = [chunk({"content": content[:20]}), chunk({"content": content[20:]}), chunk({}, "stop")]
    return "".join("data: " + json.dumps(event) + "\n\n" for event in events) + "data: [DONE]\n\n"


def test_openai_stream_sends_real_audio_with_no_storage_and_reports_output(config, tmp_path, monkeypatch):
    source = tmp_path / "source.wav"
    _audio(source, 1)
    requests, progress = [], []
    def handle(request):
        requests.append(json.loads(request.content))
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=_stream(json.dumps(_interpretation(0, 1))))
    _client(monkeypatch, handle)
    receipt = tmp_path / "receipt.json"
    result = music._hosted_json(config, "Listen to one second.", music.Interpretation.model_json_schema(),
                               audio_path=source, receipt_path=receipt, progress=progress.append)
    assert result == _interpretation(0, 1)
    assert len(requests) == 1
    request = requests[0]
    assert request["model"] == "gpt-audio-1.5" and request["store"] is False
    assert request["modalities"] == ["text"] and request["stream"] is True
    # Do not claim unsupported Structured Outputs, or reduce music to transcripts.
    assert "response_format" not in request
    audio = request["messages"][1]["content"][1]["input_audio"]
    assert audio["format"] == "wav" and base64.b64decode(audio["data"]) == source.read_bytes()
    assert progress == ["Using openai gpt-audio-1.5 to listen to this passage", "Writing the interpretation"]
    assert json.loads(receipt.read_text())["status"] == "completed"


@pytest.mark.parametrize("status,expected", [(503, "busy"), (429, "too many requests"), (401, "authorize"), (404, "model")])
def test_openai_errors_are_readable_and_never_retried_or_fall_back(config, tmp_path, monkeypatch, status, expected):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(status, json={"error": {"message": "SECRET RAW PROVIDER DETAIL", "type": "api_error", "code": None}})
    _client(monkeypatch, handle)
    with pytest.raises(music.MusicUnavailable, match=expected) as error:
        music._hosted_json(config, "listen", {}, receipt_path=tmp_path / "receipt.json")
    assert len(requests) == 1 and "SECRET" not in str(error.value)
    receipt = json.loads((tmp_path / "receipt.json").read_text())
    assert receipt["status"] == "failed-or-uncertain" and receipt["provider"] == "openai"


def test_cancellation_during_hosted_stream_closes_attempt_without_success(config, tmp_path, monkeypatch):
    _client(monkeypatch, lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"},
                                                text=_stream(json.dumps(_interpretation()))))
    def cancelled(_):
        raise JobCancelled("Job cancelled")
    receipt = tmp_path / "receipt.json"
    with pytest.raises(JobCancelled):
        music._hosted_json(config, "listen", {}, receipt_path=receipt, progress=cancelled)
    assert json.loads(receipt.read_text())["status"] == "failed-or-uncertain"


def test_provider_identity_is_part_of_interpretation_cache(config, tmp_path, monkeypatch):
    from pipeline.tests.test_lab_music import _audio_interpretation
    calls = []
    def hosted(*args, **kwargs):
        calls.append(1)
        return _audio_interpretation()
    monkeypatch.setattr(music, "_hosted_json", hosted)
    args = (config, "track", {"start": 0, "end": 6}, tmp_path / "unused", "", "job")
    first = music.interpret_audio(*args)
    config.lab.music_provider = "gemini"
    second = music.interpret_audio(*args)
    assert len(calls) == 2 and first["provenance"] != second["provenance"]


def test_bad_json_never_creates_interpretation_cache(config, tmp_path, monkeypatch):
    _client(monkeypatch, lambda _: httpx.Response(200, headers={"content-type": "text/event-stream"}, text=_stream("not JSON")))
    source = tmp_path / "audio.wav"
    _audio(source, 1)
    with pytest.raises(music.MusicUnavailable, match="incomplete interpretation"):
        music.interpret_audio(config, "track", {"start": 0, "end": 1}, source, "", "bad-json")
    assert not list((config.paths.assets_dir / "lab" / "interpretations").glob("*.json"))


def test_text_draft_uses_explicit_text_model_and_strict_schema(config, tmp_path, monkeypatch):
    requests = []
    result = {"choices": [{"slot": 0, "candidate_id": "offered", "source_start": 10, "reason": "A quiet start"}]}
    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=_stream(json.dumps(result)))
    _client(monkeypatch, handle)
    receipt = tmp_path / "draft.json"
    assert music._hosted_json(config, "Choose an offered scene.", music.DraftChoices.model_json_schema(),
                              receipt_path=receipt) == result
    assert requests[0]["model"] == config.lab.planner_model == "gpt-5.6-terra"
    assert requests[0]["response_format"]["json_schema"]["strict"] is True
    assert requests[0]["reasoning_effort"] == "low"
    assert "temperature" not in requests[0]
    assert "modalities" not in requests[0]
    assert all(part["type"] == "text" for part in requests[0]["messages"][1]["content"])
    saved = json.loads(receipt.read_text())
    assert saved["operation"] == "draft" and saved["model"] == "gpt-5.6-terra"
    assert saved["contract"] == music.PLANNER_CONTRACT


def test_error_receipt_retains_safe_diagnostics_without_provider_body(config, tmp_path, monkeypatch):
    _client(monkeypatch, lambda _: httpx.Response(400, headers={"x-request-id": "req_test"}, json={
        "error": {"message": "SECRET audio or prompt", "type": "invalid_request_error",
                  "code": "invalid_value", "param": "messages"}}))
    receipt = tmp_path / "failed.json"
    with pytest.raises(music.MusicUnavailable, match="planning model"):
        music._hosted_json(config, "SECRET user prompt", {}, receipt_path=receipt)
    saved = json.loads(receipt.read_text())
    assert saved["http_status"] == 400 and saved["request_id"] == "req_test"
    assert saved["provider_param"] == "messages" and saved["provider_code"] == "invalid_value"
    assert "SECRET" not in receipt.read_text()
