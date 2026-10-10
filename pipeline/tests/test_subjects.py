"""Subject backends: labels, duplicates, mask matching, the per-film choice and cache keys."""
import numpy as np

from pipeline.evidence import subjects


def _box(x0, y0, x1, y1):
    return np.array([x0, y0, x1, y1], np.float32)


def test_labels_map_to_codes_and_names():
    assert subjects.label_of("character creature") == "character"
    assert subjects.label_of("vehicle") == "vehicle"
    assert subjects.label_of("tree") is None
    assert subjects.Detection("character", .5, _box(0, 0, .5, .5)).code == 1
    assert subjects.Detection("character", .5, _box(0, 0, .5, .5)).name == "person"
    assert subjects.Detection("creature", .5, _box(0, 0, .5, .5)).code == 82
    assert subjects.EXTRA_CLASS_NAMES[82] == "creature"


def test_nms_merges_one_figure_found_under_two_words():
    found = [subjects.Detection("person", .6, _box(.1, .1, .5, .9)), subjects.Detection("character", .5, _box(.12, .1, .5, .88)),
             subjects.Detection("animal", .4, _box(.6, .5, .9, .9))]
    kept = subjects.nms(found)
    assert [(d.label, d.score) for d in kept] == [("person", .6), ("animal", .4)]


def test_match_masks_picks_the_overlapping_query_or_none():
    found = [subjects.Detection("person", .6, _box(.1, .1, .5, .9)), subjects.Detection("creature", .5, _box(.7, .7, .9, .9))]
    queries = np.array([[.68, .68, .92, .92], [.11, .1, .5, .9], [0, 0, .05, .05]], np.float32)
    assert subjects.match_masks(found, queries) == [1, 0]
    assert subjects.match_masks(found, np.zeros((0, 4), np.float32)) == [-1, -1]
    assert subjects.match_masks([found[1]], queries[2:]) == [-1]


def test_backend_follows_the_film_families(monkeypatch):
    facets = {"anime": {"genres": ["Animation", "Anime", "Fantasy"]}, "live": {"genres": ["Drama"]}}
    monkeypatch.setattr("pipeline.search.film_facets.film_facets", lambda _db: facets)
    assert subjects.backend_for_film(None, "anime") == "grounded"
    assert subjects.backend_for_film(None, "live") == "coco"
    assert subjects.backend_for_film(None, "unknown") == "coco"
    assert subjects.backend_for_film(None, "anime", families=()) == "coco"
    assert subjects.backend_for_film(None, "live", families=("Drama",)) == "grounded"


def test_cache_inputs_leave_coco_artifacts_current_and_key_grounded_ones():
    assert subjects.cache_inputs("abc", "coco") == {"shots": "abc"}
    assert subjects.cache_inputs("abc", "grounded") == {"shots": "abc", "subjects": "grounded"}
    assert subjects.record("coco") == {"backend": "coco"}
    assert subjects.record("grounded")["models"]["grounder"] == "grounding-dino-tiny (resize 640/1066)"


def test_scoring_families_and_class_names_cover_grounded_codes():
    from pipeline.matching.moments import score as scoring
    assert scoring.family(81) == 1 and scoring.family(82) == 1 and scoring.family(83) == 2
    assert scoring._FAMILY[82] == 1
    from pipeline.evidence import moments
    names = moments.class_names()
    assert names[1] == "person" and names[81] == "animal" and names[83] == "vehicle"


def test_config_default_grounded_families():
    from pipeline.config import IngestConfig
    assert IngestConfig().grounded_subjects == ("Animation",)
