"""Request reuse must never leak mutable vectors or cross execution boundaries."""
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from pipeline.search.request import reuse_vector, search_execution


def test_nested_execution_reuses_once_but_next_request_recomputes():
    calls = []

    def compute():
        calls.append(1)
        return np.array([1., 2.], dtype=np.float32)

    @search_execution
    def clause():
        return reuse_vector(("model", "revision", "instruction", "query"), compute)

    @search_execution
    def recipe():
        first = clause()
        first[0] = 99
        return clause()

    np.testing.assert_array_equal(recipe(), [1, 2])
    assert len(calls) == 1
    np.testing.assert_array_equal(recipe(), [1, 2])
    assert len(calls) == 2


def test_concurrent_requests_and_failed_requests_are_isolated():
    @search_execution
    def request(value, fail=False):
        answer = reuse_vector(("query",), lambda: np.array([value]))
        if fail:
            raise RuntimeError("failed query")
        return answer.item()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(request, range(10))) == list(range(10))
    with pytest.raises(RuntimeError):
        request(99, fail=True)
    assert request(2) == 2


def test_profile_and_instruction_changes_do_not_reuse_embeddings():
    @search_execution
    def request():
        return [reuse_vector(key, lambda i=i: np.array([i])).item()
                for i, key in enumerate([("v1", "words"), ("v2", "words"), ("v1", "mood")])]
    assert request() == [0, 1, 2]


def test_metadata_hydrates_only_new_ids_and_never_leaks_mutations():
    from pipeline.search.request import reuse_rows
    calls = []
    def fetch(ids):
        calls.append(ids)
        return [{"unit_id": identity, "caption": "original"} for identity in ids]
    @search_execution
    def request():
        first = reuse_rows(("units", 1), ["a", "b"], fetch)
        first[0]["caption"] = "changed"
        second = reuse_rows(("units", 1), ["a", "c"], fetch)
        assert second[0]["caption"] == "original"
    request()
    assert calls == [["a", "b"], ["c"]]
