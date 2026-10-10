"""Offline tests for open-data metadata matching, extraction and compilation."""

from __future__ import annotations

from pathlib import Path

from pipeline.evidence import compile as compiler
from pipeline.evidence import metadata
from pipeline.evidence.library import FilmRef


def _entity(qid, label, year, *, film=True, imdb="tt1", cast=(), sitelinks=None, aliases=()):
    claims = {
        "P31": [{"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": "Q11424" if film else "Q5"}}}}],
        "P577": [{"mainsnak": {"snaktype": "value", "datavalue": {"value": {"time": f"+{year}-01-01T00:00:00Z"}}}}],
        "P345": [{"mainsnak": {"snaktype": "value", "datavalue": {"value": imdb}}}],
        "P161": [
            {"mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": actor}}},
             "qualifiers": {"P453": [{"datavalue": {"value": {"id": character}}}]}}
            for actor, character in cast
        ],
    }
    return {
        "id": qid,
        "labels": {"en": {"value": label}},
        "aliases": {"en": [{"value": alias} for alias in aliases]},
        "descriptions": {"en": {"value": f"{year} film" if film else "person"}},
        "claims": claims,
        "sitelinks": sitelinks or {},
    }


def _film(title):
    return FilmRef("0" * 64, title, Path("x.mkv"), 0.0, 0.0)


def test_view_sentences_keep_their_own_closing_mark():
    assert compiler._sentence("He walks away") == "He walks away."
    assert compiler._sentence("He walks away.. ") == "He walks away."
    assert compiler._sentence("You Talkin' to Me?") == "You Talkin' to Me?"
    assert compiler._sentence('She shouts "Run!"') == 'She shouts "Run!"'


def test_choose_film_entity_prefers_matching_title_and_year():
    candidates = {
        "Q1": _entity("Q1", "Singin' in the Rain", 1952),
        "Q2": _entity("Q2", "Singin' in the Rain", 1983),           # remake-like year
        "Q3": _entity("Q3", "Singin' in the Rain", 1952, film=False),
    }
    qid, _entity_doc, match = metadata.choose_film_entity(_film("Singin in the Rain (1952)"), candidates)
    assert qid == "Q1"
    assert match["year_gap"] == 0


def test_choose_film_entity_rejects_distant_titles():
    candidates = {"Q9": _entity("Q9", "Completely Different", 1999)}
    assert metadata.choose_film_entity(_film("The Matrix (1999)"), candidates) is None


class FakeApi:
    def __init__(self, entities):
        self._entities = entities

    def wikipedia_search(self, query, limit=6):
        return ["The Matrix", "The Matrix (franchise)"]

    def wikibase_items(self, titles):
        return {"The Matrix": "Q83495", "The Matrix (franchise)": "Q1"}

    def entities(self, ids, props=None):
        return {qid: self._entities[qid] for qid in ids if qid in self._entities}

    def wikidata_search(self, name, limit=7):
        return []

    def extract(self, api, title):
        if api == metadata.WIKIQUOTE_API:
            return title, "== Neo ==\nNeo: I know kung fu.\n== Cast ==\nKeanu Reeves"
        return title, "Lead.\n\n== Plot ==\nNeo meets Morpheus.\n=== Escape ===\nThey escape.\n\n== Cast ==\nx"

    def pageviews(self, title, months, today=None):
        return 1000


def test_build_metadata_collects_identity_cast_plot_quotes_and_popularity():
    entities = {
        "Q83495": _entity("Q83495", "The Matrix", 1999, imdb="tt0133093", cast=[("Q10", "Q20")],
                          sitelinks={"enwiki": {"title": "The Matrix"}, "enwikiquote": {"title": "The Matrix (film)"}}),
        "Q1": _entity("Q1", "The Matrix", 1999, film=False),
        "Q10": {"id": "Q10", "labels": {"en": {"value": "Keanu Reeves"}}},
        "Q20": {"id": "Q20", "labels": {"en": {"value": "Neo"}}},
    }
    data = metadata.build_metadata(FakeApi(entities), _film("The Matrix (1999)"), ratings={"tt0133093": (8.7, 2_000_000)})
    assert data["match"]["status"] == "matched"
    assert data["wikidata"]["imdb_id"] == "tt0133093"
    assert data["wikidata"]["cast"] == [{"order": 0, "actor": "Keanu Reeves", "characters": ["Neo"]}]
    assert data["wikipedia"]["plot"] == "Neo meets Morpheus.\nThey escape."
    assert data["wikiquote"]["quotes"] == ["I know kung fu."]
    assert data["popularity"] == {"pageviews_12m": 1000, "imdb_rating": 8.7, "imdb_votes": 2_000_000}


def test_plot_section_handles_missing_plot():
    assert metadata.plot_section("Lead only.\n\n== Reception ==\nGood.", 100) == ""


def test_film_popularity_blends_votes_and_pageviews():
    documents = {
        "a": {"popularity": {"imdb_votes": 3_000_000, "pageviews_12m": 100}},
        "b": {"popularity": {"imdb_votes": 10_000, "pageviews_12m": 5_000_000}},
        "c": {"popularity": {}},
    }
    popularity = compiler.film_popularity(documents)
    assert popularity["a"] > popularity["b"] > popularity["c"]
    assert all(0.0 <= value <= 1.0 for value in popularity.values())
