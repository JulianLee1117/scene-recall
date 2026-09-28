"""Frozen capture safety with fake HTTP and database metadata only."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest
import yaml

from pipeline.experiments import capture_search_intent as capture
from pipeline.experiments.search_intent import _freeze


@pytest.fixture
def setup(tmp_path, monkeypatch):
    assets = tmp_path / "assets"
    (assets / "db").mkdir(parents=True)
    config, queries, out = (tmp_path / name for name in ("config.yaml", "queries.yaml", "capture.json"))
    config.write_text(yaml.safe_dump({"paths": {"assets_dir": str(assets)}, "models": {"text": "model"},
                                     "retrieval": {"limit": 200}}), encoding="utf-8")
    cases = [{"id": "first", "query": "an almost kiss", "film_ids": ["film-a"],
              "expected_windows": [{"film_id": "film-a", "start": 3, "end": 8,
                                    "status": "frame-verified"}], "review_status": "pending"},
             {"id": "second", "query": "a blue room", "film_ids": [], "group_id": "correlated"}]
    queries.write_text(yaml.safe_dump({"version": 1, "notes": "preserve me", "cases": cases}), encoding="utf-8")
    events, snapshots = [], [{"films": 2, "units": 9}] * 4

    def connect(path):
        assert path == str(assets / "db")
        events.append("versions")
        snapshot = snapshots.pop(0)
        return SimpleNamespace(list_tables=lambda: SimpleNamespace(tables=list(snapshot)),
                               open_table=lambda name: SimpleNamespace(version=snapshot[name]))

    row = {"unit_id": "film-a_0001", "film_id": "film-a", "t_start": 3, "t_end": 8,
           "caption": "Two people looking at one another", "matched_text": "almost kiss"}
    responses = [{"results": [row], "has_more": True, "next_limit": 200}, {"results": [], "has_more": False}]
    calls, options = [], []

    class Client:
        def __init__(self, **kwargs):
            options.append(kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def get(self, url, *, params):
            events.append("get")
            calls.append((url, params))
            item = responses.pop(0)
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, httpx.Response):
                return item
            return httpx.Response(200, json=item, request=httpx.Request("GET", url))

    monkeypatch.setattr(capture.lancedb, "connect", connect)
    monkeypatch.setattr(capture.httpx, "Client", Client)
    return SimpleNamespace(queries=queries, config=config, out=out, cases=cases, snapshots=snapshots,
                           responses=responses, events=events, calls=calls, options=options, assets=assets)


def run(s):
    return capture.capture(s.queries, s.out, s.config)


def test_complete_preserves_shapes_evidence_and_provenance_sequentially(setup):
    s = setup
    result = run(s)
    assert result == json.loads(s.out.read_text(encoding="utf-8"))
    assert result["capture_status"] == "complete"
    assert result["captured_case_count"] == result["expected_case_count"] == 2
    assert "partial_cases" not in result
    assert result["fixture_metadata"] == {"version": 1, "notes": "preserve me"}
    for original, frozen in zip(s.cases, result["cases"]):
        assert all(frozen[k] == v for k, v in original.items())
        assert frozen["snapshot_before"] == frozen["snapshot_after"] == {"films": 2, "units": 9}
    assert result["cases"][0]["response_envelope"] == {"has_more": True, "next_limit": 200}
    assert result["provenance"]["fixture_sha256"] == hashlib.sha256(s.queries.read_bytes()).hexdigest()
    assert len(result["provenance"]["on_disk_search_code_sha256"]) == 3
    assert s.events == ["versions", "get", "versions"] * 2
    assert s.calls[0] == ("http://127.0.0.1:8000/search", [("q", "an almost kiss"), ("limit", "200"), ("film_id", "film-a")])
    assert s.options[0]["follow_redirects"] is False and s.options[0]["trust_env"] is False
    assert _freeze(result)["cases"] == result["cases"]


@pytest.mark.parametrize("failure", ["scope", "versions", "http", "transport", "malformed", "too_many", "interrupt"])
def test_failure_retains_explicit_partial_not_replayable_or_resumable(setup, failure):
    s = setup
    # Let one valid case complete; the second must not turn a partial run into a benchmark.
    s.cases[1]["film_ids"] = ["film-a"]
    s.queries.write_text(yaml.safe_dump({"cases": s.cases}), encoding="utf-8")
    if failure == "scope":
        s.responses[1] = {"results": [{"film_id": "other"}]}
    elif failure == "versions":
        s.snapshots[3] = {"films": 3, "units": 9}
    elif failure == "http":
        s.responses[1] = httpx.Response(503, request=httpx.Request("GET", "http://127.0.0.1:8000/search"))
    elif failure == "transport":
        s.responses[1] = httpx.ConnectError("offline")
    elif failure == "malformed":
        s.responses[1] = {"results": None}
    elif failure == "too_many":
        s.responses[1] = {"results": [{"film_id": "film-a"}] * 201}
    else:
        s.responses[1] = KeyboardInterrupt()
    with pytest.raises((ValueError, httpx.HTTPError, KeyboardInterrupt)):
        run(s)
    partial = json.loads(s.out.read_text(encoding="utf-8"))
    assert partial["capture_status"] == "partial"
    assert partial["captured_case_count"] == 1 and partial["expected_case_count"] == 2
    assert partial["cases"] == [] and len(partial["partial_cases"]) == 1
    assert partial["failure"]["case_id"] == "second"
    with pytest.raises(ValueError, match="version-1 capture"):
        _freeze(partial)
    before = s.out.read_bytes()
    with pytest.raises(FileExistsError):
        run(s)
    assert s.out.read_bytes() == before and len(s.calls) == 2


@pytest.mark.parametrize("mutate", ["empty", "too_many", "duplicate", "bad_scope", "query"])
def test_invalid_fixture_rejected_before_requests_or_output(setup, mutate):
    s = setup
    cases = deepcopy(s.cases)
    if mutate == "empty": cases = []
    elif mutate == "too_many": cases = [{**cases[0], "id": f"case{i}"} for i in range(31)]
    elif mutate == "duplicate": cases[1]["id"] = cases[0]["id"]
    elif mutate == "bad_scope": cases[0]["film_ids"] = ["film-a", "film-a"]
    else: cases[0]["query"] = ""
    s.queries.write_text(yaml.safe_dump({"cases": cases}), encoding="utf-8")
    with pytest.raises(ValueError): run(s)
    assert not s.out.exists() and not s.calls and not s.events


def test_missing_database_does_not_initialize_it(setup):
    s = setup
    (s.assets / "db").rmdir()
    with pytest.raises(ValueError, match="existing database"):
        run(s)
    assert not (s.assets / "db").exists() and not s.out.exists() and not s.events


@pytest.mark.parametrize("length", [500, 501])
def test_api_query_length_boundary_before_output_or_request(setup, length):
    s = setup
    s.cases[0]["query"] = "q" * length
    s.queries.write_text(yaml.safe_dump({"cases": s.cases}), encoding="utf-8")
    if length == 500:
        assert run(s)["cases"][0]["query"] == "q" * length
        assert s.calls[0][1][0] == ("q", "q" * length)
    else:
        with pytest.raises(ValueError, match="1–500 character query"):
            run(s)
        assert not s.out.exists() and not s.calls and not s.events


def test_nonloopback_rejected_before_output_or_http(setup):
    s = setup
    with pytest.raises(ValueError, match="loopback"):
        capture.capture(s.queries, s.out, s.config, "https://example.com")
    assert not s.out.exists() and not s.calls


def test_cli_accepts_queries_key_and_custom_loopback(setup):
    s = setup
    s.queries.write_text(yaml.safe_dump({"queries": s.cases}), encoding="utf-8")
    capture.main(["--queries", str(s.queries), "--out", str(s.out), "--config", str(s.config),
                  "--api-base", "http://localhost:8010/"])
    assert len(s.calls) == 2 and s.calls[0][0] == "http://localhost:8010/search"
