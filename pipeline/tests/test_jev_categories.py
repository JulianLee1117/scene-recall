"""Category probes keep expectations and authority separate from model requests."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml

from pipeline.experiments.jev_categories import CHOICES, LABELS, prepare_requests


def fixture():
    return yaml.safe_load((Path(__file__).parents[1] / "eval" / "jev_category_probe.yaml").read_text(encoding="utf-8"))


def test_predeclared_fixture_has_24_independent_seven_question_requests():
    value = fixture()
    original = deepcopy(value)
    requests = prepare_requests(value)
    assert len(requests) == 24 == value["sampling"]["case_count"]
    assert len({row["id"] for row in requests}) == 24
    assert value == original
    for row, case in zip(requests, value["cases"]):
        assert set(row["body"]["questions"]) == set(LABELS)
        assert row["body"]["state"]["query"] == case["query"]
        assert row["body"]["state"]["explicit_film_ids"] == case["explicit_film_ids"]
        assert row["expected"] == case["expected"]
        assert row["metadata"]["human_review"] == "pending"
        for question in row["body"]["questions"].values():
            assert question["type"] == "choice"
            assert tuple(question["criteria"]) == CHOICES


def test_expected_answers_rationales_case_labels_and_groups_never_enter_provider_body():
    value = fixture()
    value["cases"][0]["rationale"] = "LOCAL_ONLY_SECRET_EXPECTATION"
    value["cases"][0]["group_id"] = "LOCAL_ONLY_GROUP"
    row = prepare_requests(value)[0]
    encoded = json.dumps(row["body"])
    assert "LOCAL_ONLY" not in encoded
    assert "expected" not in row["body"] and "metadata" not in row["body"]
    assert row["metadata"]["rationale"] == "LOCAL_ONLY_SECRET_EXPECTATION"


def test_movie_words_cannot_create_scope_or_return_scope_decisions():
    rows = {row["metadata"]["case_id"]: row for row in prepare_requests(fixture())}
    assert rows["movie_words_unconfirmed"]["body"]["state"]["explicit_film_ids"] == []
    assert len(rows["movie_scope_confirmed"]["body"]["state"]["explicit_film_ids"]) == 1
    assert rows["title_word_as_description"]["body"]["state"]["explicit_film_ids"] == []
    assert rows["movie_scope_confirmed"]["expected"] == rows["movie_words_unconfirmed"]["expected"]
    assert all(set(row["body"]["questions"]) == set(LABELS) for row in rows.values())


def test_negation_is_retained_and_ambiguous_expectations_stay_explicit():
    rows = {row["metadata"]["case_id"]: row for row in prepare_requests(fixture())}
    assert rows["visible_not_spoken"]["expected"]["dialogue"] == ["no"]
    assert rows["spoken_not_visible"]["expected"]["on_screen_text"] == ["no"]
    assert "not somebody saying" in rows["visible_not_spoken"]["body"]["state"]["query"]
    assert rows["bare_quote_ambiguous"]["metadata"]["ambiguity"]
    assert len(rows["bare_quote_ambiguous"]["expected"]["dialogue"]) == 2
    assert all(allowed == ["no"] for allowed in rows["exclusions_only"]["expected"].values())


def test_provider_identity_changes_only_request_identity_not_fixture_identity():
    first = prepare_requests(fixture())
    second = prepare_requests(fixture(), model="typesafe/test-model")
    assert first[0]["metadata"]["fixture_sha256"] == second[0]["metadata"]["fixture_sha256"]
    assert first[0]["metadata"]["request_sha256"] != second[0]["metadata"]["request_sha256"]


def test_prepared_request_bodies_do_not_share_mutable_category_definitions():
    value = fixture()
    rows = prepare_requests(value)
    original = rows[1]["body"]["state"]["category_definitions"]["content"]
    rows[0]["body"]["state"]["category_definitions"]["content"] = "changed"
    assert rows[1]["body"]["state"]["category_definitions"]["content"] == original
    assert value["definitions"]["content"] == original


@pytest.mark.parametrize("damage", ["duplicate", "unsafe", "query", "scope", "missing_label", "boolean_label", "duplicate_choice", "definition", "state"])
def test_malformed_or_unfrozen_cases_fail_before_transport(damage):
    value = fixture()
    case = value["cases"][0]
    if damage == "duplicate":
        value["cases"][1]["id"] = case["id"]
    elif damage == "unsafe":
        case["id"] = "../case"
    elif damage == "query":
        case["query"] = " "
    elif damage == "scope":
        case["explicit_film_ids"] = ["film", "film"]
    elif damage == "missing_label":
        del case["expected"]["dialogue"]
    elif damage == "boolean_label":
        case["expected"]["dialogue"] = [True]
    elif damage == "duplicate_choice":
        case["expected"]["dialogue"] = ["no", "no"]
    elif damage == "definition":
        del value["definitions"]["dialogue"]
    else:
        value["state"] = "tuned-after-provider-output"
    with pytest.raises(ValueError):
        prepare_requests(value)
