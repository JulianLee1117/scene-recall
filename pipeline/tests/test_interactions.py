"""Tests for the local interaction (taste) log."""

from __future__ import annotations

import pytest

from pipeline.interactions import InteractionLog


def test_interactions_are_appended_and_validated(tmp_path):
    log = InteractionLog(tmp_path)
    log.initialize()
    log.initialize()                                   # idempotent
    log.record("search", query="neo dodges bullets", preset="famous", context={"results": 48})
    log.record("play", film_id="f", unit_id="f_0001", t=12.5, rank=3)
    rows = log.recent()
    assert [row["kind"] for row in rows] == ["play", "search"]
    assert rows[1]["query"] == "neo dodges bullets" and rows[1]["preset"] == "famous"
    with pytest.raises(ValueError):
        log.record("like")
    with pytest.raises(ValueError):
        log.record("search", context={"blob": "x" * 5000})


def test_only_requests_marked_by_the_app_are_logged(tmp_path):
    from types import SimpleNamespace

    from starlette.datastructures import Headers

    from pipeline.api.main import _log_interaction

    log = InteractionLog(tmp_path)
    log.initialize()
    state = SimpleNamespace(interactions=log)

    def request(headers: dict[str, str]):
        return SimpleNamespace(app=SimpleNamespace(state=state), headers=Headers(headers))

    _log_interaction(request({}), "search", query="from a script")
    _log_interaction(request({"X-Scene-Recall-Client": "agent"}), "search", query="from an agent")
    _log_interaction(request({"X-Scene-Recall-Client": "app"}), "search", query="from the app")
    assert [row["query"] for row in log.recent()] == ["from the app"]
