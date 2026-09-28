"""Cross-encoder rerank: blending, documents, and its time budget (no model download)."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from pipeline.search import rerank


@pytest.fixture(autouse=True)
def awake(monkeypatch):
    monkeypatch.setattr(rerank, "_RESTING", {"until": 0.0})


def test_blend_lets_a_confident_judgement_overtake_the_fused_rank_within_the_shortlist():
    assert rerank.blend(["a", "b", "c"], [0.1, 0.2, 0.99]) == ["c", "a", "b"]
    assert rerank.blend(["a", "b"], [0.5, 0.5]) == ["a", "b"]


def test_document_reads_scene_action_caption_and_the_matched_line():
    text = rerank.document({"caption": "A man in a car.", "_matched_line": {"text": "You talking to me?"}},
                           {"action": "Travis rehearses", "characters": '["Travis"]'},
                           {"title": "Mirror", "summary": "Alone at home."}, "Taxi Driver")
    assert text.splitlines() == ["Film: Taxi Driver.", "Scene: Mirror. Alone at home.",
                                 "Action: Travis rehearses (Travis)", "Visual: A man in a car.",
                                 'Dialogue: "You talking to me?"']


def test_rerank_rests_when_the_gpu_is_full_or_a_rerank_overruns(monkeypatch):
    monkeypatch.setattr(rerank, "_load", lambda: {"device": "cuda"})
    monkeypatch.setattr(rerank, "_free_bytes", lambda _state: 0)
    assert rerank.score("q", ["doc"]) is None
    assert rerank._RESTING["until"] > time.monotonic()

    monkeypatch.setattr(rerank, "_RESTING", {"until": 0.0})
    monkeypatch.setattr(rerank, "_free_bytes", lambda _state: 8 << 30)
    monkeypatch.setattr(rerank, "_score", lambda *_args, **_kwargs: None)          # abandoned: over budget
    assert rerank.score("q", ["doc"]) is None
    calls = []
    monkeypatch.setattr(rerank, "_score", lambda *_args, **_kwargs: calls.append(1) or [0.5])
    assert rerank.score("q", ["doc"]) is None and not calls                          # resting: no inference
    monkeypatch.setattr(rerank, "_RESTING", {"until": 0.0})
    assert rerank.score("q", ["doc"]) == [0.5]


def test_rerank_abandons_the_rest_of_the_shortlist_after_its_deadline():
    torch = pytest.importorskip("torch")

    class Batch(dict):
        def to(self, _device):
            return self

    class Tokenizer:
        def __call__(self, texts, **_kwargs):
            return {"input_ids": [[1, 2] for _ in texts]}

        def pad(self, encoded, **_kwargs):
            return Batch(input_ids=torch.tensor(encoded["input_ids"]))

    class Model:
        calls = 0

        def __call__(self, input_ids):
            Model.calls += 1
            return SimpleNamespace(logits=torch.zeros(input_ids.shape[0], input_ids.shape[1], 4))

    state = {"torch": torch, "tokenizer": Tokenizer(), "model": Model(), "device": "cpu",
             "prefix": [0], "suffix": [0], "yes": 1, "no": 2}
    assert rerank._score(state, "q", ["doc"] * 40, 16, deadline=time.monotonic() - 1) is None
    assert Model.calls == 1                                   # the first batch ran, the rest were abandoned
    scores = rerank._score(state, "q", ["doc"] * 40, 16, deadline=time.monotonic() + 60)
    assert len(scores) == 40 and scores[0] == pytest.approx(0.5)
