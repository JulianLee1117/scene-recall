"""The standalone viewer retains blind/source boundaries and human-only grades."""
from copy import deepcopy
import json
import re
import shutil
import subprocess

import httpx
import pytest

from pipeline.experiments import search_intent_review as viewer
from pipeline.experiments.search_intent_review import (
    CONTRACT, _SCRIPT, loopback_base, main, render_review, validate_review,
)


@pytest.fixture
def document():
    film = "a" * 64
    unit = film + "_0001"
    row = {"film_id": film, "unit_id": unit, "film_title": "Example", "t_start": 10., "t_end": 20.,
           "caption": "Two people", "keyframe_url": f"/media/keyframe/{unit}/1",
           "matched_frame_url": f"/media/keyframe/{unit}/1", "matched_frame_timestamp": 15.,
           "preview_url": f"/media/preview/{unit}", "matched_text": None, "matched_text_view": None,
           "rank": 1, "human_grade": None}
    cases = [{"id": case, "query": query, "film_ids": [],
              "lists": {label: [deepcopy(row)] for label in "ABCD"},
              "human_review_status": "pending", "preferred_list": None, "notes": None}
             for case, query in [("first", "close-up"), ("second", "wide shot")]]
    return {"contract": CONTRACT, "input_sha256": "b" * 64, "cases": cases, "limits": "Human review pending"}


def node_state(document, action):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required to exercise the embedded browser state code")
    script = _SCRIPT + "\nconst data=" + json.dumps(document) + ";\n" + action
    process = subprocess.run([node], input=script, text=True, capture_output=True, timeout=15, check=True)
    return json.loads(process.stdout)


def test_grades_are_shared_within_query_not_between_queries_and_export_is_blind(document):
    result = node_state(document, """
const values=new Map(),storage={getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v)};
const r=createReview(data,storage);r.grade('first',data.cases[0].lists.A[0],3);
r.state.preferences.first='C';r.state.notes.first='Clearer source match';r.state.reviewed.first=true;r.save();
const replay=createReview(data,storage);console.log(JSON.stringify({out:replay.exportData(),key:[...values.keys()][0],original:data}));
""")
    assert result["key"] == "scene-recall:search-intent-review:" + document["input_sha256"]
    assert result["original"] == document
    first, second = result["out"]["cases"]
    assert all(rows[0]["human_grade"] == 3 for rows in first["lists"].values())
    assert all(rows[0]["human_grade"] is None for rows in second["lists"].values())
    assert first["preferred_list"] == "C" and first["notes"] == "Clearer source match"
    assert first["human_review_status"] == "reviewed" and second["human_review_status"] == "pending"
    assert set(result["out"]) == set(document)


def test_partial_and_cleared_grades_have_truthful_status(document):
    result = node_state(document, """
const r=createReview(data,{getItem:()=>null,setItem:()=>{}});r.grade('first',data.cases[0].lists.A[0],0);
const partial=r.exportData();r.grade('first',data.cases[0].lists.D[0],null);
console.log(JSON.stringify({partial,cleared:r.exportData()}));
""")
    assert result["partial"]["cases"][0]["human_review_status"] == "in-progress"
    assert result["partial"]["cases"][0]["lists"]["D"][0]["human_grade"] == 0
    assert result["cleared"]["cases"][0]["human_review_status"] == "pending"


def test_unavailable_or_corrupt_browser_storage_keeps_export_working(document):
    result = node_state(document, """
const r=createReview(data,{getItem:()=>'{broken',setItem:()=>{throw Error('denied')}});
const readWarning=r.warning;r.grade('first',data.cases[0].lists.A[0],2);
console.log(JSON.stringify({readWarning,warning:r.warning,out:r.exportData(),unavailable:createReview(data,null).warning}));
""")
    assert "could not be read" in result["readWarning"]
    assert "Export JSON" in result["warning"] and "Export JSON" in result["unavailable"]
    assert result["out"]["cases"][0]["lists"]["B"][0]["human_grade"] == 2


def test_existing_human_grade_is_reconciled_across_unrated_copies(document):
    document["cases"][0]["lists"]["C"][0]["human_grade"] = 1
    result = node_state(document, "console.log(JSON.stringify(createReview(data,null).exportData()));")
    assert all(rows[0]["human_grade"] == 1 for rows in result["cases"][0]["lists"].values())


def test_script_markup_is_data_and_unknown_treatment_fields_are_not_embedded(document):
    hostile = '</script><script>alert("x")</script><img src="https://evil.test/x" onerror="alert(1)"> & '
    document["cases"][0]["query"] = hostile
    document["cases"][0]["lists"]["A"][0]["caption"] = hostile
    document["blind_key"] = {"A": "secret-treatment-name"}
    document["cases"][0]["lists"]["A"][0]["debug"] = {"variant": "secret-treatment-name"}
    html = render_review(document)
    payload = re.search(r'<script id="review-data" type="application/json">(.*?)</script>', html, re.S).group(1)
    assert "<" not in payload and "&" not in payload
    assert json.loads(payload)["review"]["cases"][0]["query"] == hostile
    assert "secret-treatment-name" not in html
    assert "innerHTML" not in html and "eval(" not in html
    assert "autoplay" not in html and ".play()" not in html
    assert "textContent" in html and "video.pause()" in html


@pytest.mark.parametrize("base", ["http://localhost:8000/", "http://127.0.0.1:8010", "https://[::1]:8000"])
def test_loopback_origins_are_allowed(base):
    assert loopback_base(base) == base.rstrip("/")


@pytest.mark.parametrize("base", ["https://example.com", "http://localhost.evil.test", "file:///tmp",
    "http://user:pass@localhost:8000", "http://localhost:8000/media", "http://localhost:8000?key=x",
    "http://127.0.0.1:0", "http://[::ffff:192.0.2.1]:8000", "http://localhost:8000/#x"])
def test_nonlocal_or_credentialed_api_bases_are_rejected(base):
    with pytest.raises(ValueError):
        loopback_base(base)


@pytest.mark.parametrize("route", ["https://evil.test/pixel", "//evil.test/pixel", "/media/keyframe/../2",
    "/media/keyframe/wrong-unit/1", "/video/anything", "/media/keyframe/x/1?redirect=https://evil.test"])
def test_untrusted_thumbnail_routes_are_rejected(document, route):
    document["cases"][0]["lists"]["A"][0]["matched_frame_url"] = route
    with pytest.raises(ValueError, match="media route"):
        render_review(document)


def test_bounds_conflicting_grades_and_nonblind_inputs_fail_clearly(document):
    broken = deepcopy(document)
    broken["cases"][0]["lists"]["A"][0]["matched_frame_timestamp"] = 20.
    with pytest.raises(ValueError, match="inside"):
        validate_review(broken)
    broken = deepcopy(document)
    broken["cases"][0]["lists"]["A"][0]["t_start"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        validate_review(broken)
    broken = deepcopy(document)
    broken["cases"][0]["lists"]["A"][0]["human_grade"] = 3
    broken["cases"][0]["lists"]["B"][0]["human_grade"] = 0
    with pytest.raises(ValueError, match="conflicting"):
        validate_review(broken)
    with pytest.raises(ValueError, match="cases"):
        validate_review({"contract": CONTRACT, "input_sha256": "b"*64, "cases": {"first": {"A": "baseline"}}})


def test_cli_reads_only_named_blind_input_and_never_replaces_output(document, tmp_path, monkeypatch):
    source, output = tmp_path / "blind-review.json", tmp_path / "review.html"
    source.write_text(json.dumps(document), encoding="utf-8")
    key = tmp_path / "blind-key.json"
    key.write_text("DO NOT READ THE KEY", encoding="utf-8")
    from pathlib import Path
    read = Path.read_text
    def guarded(path, *args, **kwargs):
        assert path != key
        return read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", guarded)
    assert main(["--input", str(source), "--out", str(output)]) == 0
    original = output.read_bytes()
    assert b"DO NOT READ THE KEY" not in original
    with pytest.raises(FileExistsError):
        main(["--input", str(source), "--out", str(output)])
    assert output.read_bytes() == original


def payload(html):
    return json.loads(re.search(r'<script id="review-data" type="application/json">(.*?)</script>', html, re.S).group(1))


def resolver_client(monkeypatch, responses):
    calls, options = [], []
    real_client = httpx.Client
    def handler(request):
        calls.append(request)
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response
    def client(**kwargs):
        options.append(kwargs)
        return real_client(**kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(viewer.httpx, "Client", client)
    return calls, options


def test_offline_default_does_not_fetch_and_marks_playback_unresolved(document, monkeypatch):
    monkeypatch.setattr(viewer.httpx, "Client", lambda **kwargs: pytest.fail("Offline generation performed HTTP"))
    html = render_review(document)
    saved = payload(html)
    assert saved["playback"]["a" * 64]["status"] == "unresolved"
    assert saved["playback"]["a" * 64]["url"] is None
    assert saved["review"] == validate_review(document)
    assert "button.disabled=source?.status!=='resolved'" in html
    assert "fetch(" not in html and "base+'/video/'" not in html


def test_resolves_distinct_films_once_and_pins_prepared_url_without_changing_blind_data(document, monkeypatch):
    film, token = "a" * 64, "c" * 24
    route = f"/video/{film}?representation={token}"
    calls, options = resolver_client(monkeypatch, [httpx.Response(200, json={"url": route})])
    html = render_review(document, "http://127.0.0.1:8000", resolve_playback=True)
    saved = payload(html)
    assert len(calls) == 1 and str(calls[0].url) == f"http://127.0.0.1:8000/video/{film}/playback"
    assert calls[0].method == "GET"
    assert options == [{"follow_redirects": False, "trust_env": False, "timeout": 5.0}]
    assert max(calls[0].extensions["timeout"].values()) <= 5
    assert saved["playback"][film] == {"status": "resolved", "url": route, "kind": "prepared"}
    assert saved["review"] == validate_review(document)
    assert "video.src=base+source.url+'#t='" in html


def test_original_resolver_url_is_allowed_but_browser_support_not_claimed(monkeypatch):
    film = "a" * 64
    resolver_client(monkeypatch, [httpx.Response(200, json={"url": f"/video/{film}"})])
    assert viewer.resolve_playback_urls([film], "http://localhost:8000")[film]["kind"] == "original"
    assert "Original source; browser compatibility is unverified." in _SCRIPT


@pytest.mark.parametrize("route", [
    "/video/" + "b" * 64,
    "https://evil.test/video/" + "a" * 64,
    "http://localhost:8000/video/" + "a" * 64,
    "/video/" + "a" * 64 + "?representation=" + "c" * 23,
    "/video/" + "a" * 64 + "?representation=" + "c" * 24 + "&other=x",
    "/video/" + "a" * 64 + "#t=1,2",
    "/video/" + "a" * 64 + "/../other",
])
def test_resolver_cannot_supply_other_film_external_or_unexpected_urls(monkeypatch, route):
    calls, _ = resolver_client(monkeypatch, [httpx.Response(200, json={"url": route})])
    item = viewer.resolve_playback_urls(["a" * 64], "http://localhost:8000")["a" * 64]
    assert item["status"] == "unresolved" and item["url"] is None
    assert "unexpected source URL" in item["reason"] and len(calls) == 1


@pytest.mark.parametrize("response", [
    httpx.Response(302, headers={"location": "http://elsewhere.test"}),
    httpx.Response(503), httpx.Response(200, content=b"not JSON"),
    httpx.Response(200, content=b" " * 8193), httpx.ConnectError("offline"),
])
def test_resolution_errors_are_explicit_without_retries_or_fallback(monkeypatch, response):
    calls, _ = resolver_client(monkeypatch, [response])
    item = viewer.resolve_playback_urls(["a" * 64], "http://localhost:8000")["a" * 64]
    assert item["status"] == "unresolved" and item["url"] is None and item["reason"]
    assert len(calls) == 1


def test_resolution_film_and_total_time_budgets(monkeypatch):
    first, second = "a" * 64, "b" * 64
    calls, _ = resolver_client(monkeypatch, [httpx.Response(200, json={"url": f"/video/{first}"})])
    with pytest.raises(ValueError, match="at most 200"):
        viewer.resolve_playback_urls([f"{i:064x}" for i in range(201)], "http://localhost:8000")
    assert not calls
    ticks = iter([0., 0., 0., 61.])
    monkeypatch.setattr(viewer.time, "monotonic", lambda: next(ticks))
    result = viewer.resolve_playback_urls([second, first], "http://localhost:8000")
    assert result[first]["status"] == "resolved"
    assert result[second]["status"] == "unresolved" and "budget exhausted" in result[second]["reason"]
    assert len(calls) == 1


def test_cli_resolution_is_opt_in_and_existing_output_prevents_http(document, tmp_path, monkeypatch):
    source, output = tmp_path / "blind-review.json", tmp_path / "resolved-review.html"
    source.write_text(json.dumps(document), encoding="utf-8")
    calls, _ = resolver_client(monkeypatch, [httpx.Response(200, json={"url": "/video/" + "a" * 64})])
    args = ["--input", str(source), "--out", str(output), "--resolve-playback"]
    assert main(args) == 0
    assert payload(output.read_text(encoding="utf-8"))["resolve_playback"] is True
    with pytest.raises(FileExistsError):
        main(args)
    assert len(calls) == 1
