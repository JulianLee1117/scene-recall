"""Film-level facets for search filters: release year, directors and genre families.

Open metadata (ADR-0093) stores Wikidata's genres verbatim: 144 labels across
186 films, many of them fine-grained ("crime thriller film", "neo-noir",
"flashback film"). Filters need a short, stable vocabulary, so each label maps
to zero or more families by word patterns; new labels from new films join a
family without code changes. Labels that describe form rather than genre
("independent film", "flashback film", "epic film") join none.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Iterable

from pipeline.evidence.tables import FILM_META

# Family -> pattern over a lowercased Wikidata genre label.
GENRE_FAMILIES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (family, re.compile(pattern))
    for family, pattern in (
        ("Action", r"\baction\b|martial arts|superhero|wuxia|samurai|girls with guns|\bchase\b|sword-and-sandal"),
        ("Adventure", r"adventure|treasure hunt|survival|sword and sorcery"),
        ("Animation", r"anim(?:ated|ation)|\banime\b"),
        ("Anime", r"\banime\b"),
        ("Arthouse", r"\bart film|arthouse|experimental|surreal|psychedelic|absurdist"),
        ("Biography", r"biograph|biopic"),
        ("Comedy", r"comedy|comedic|satir|parody|tragicomedy"),
        ("Coming of age", r"coming-of-age|\bteen\b|high school"),
        ("Crime", r"crime|gangster|police|heist|detective|\bhood film|prison|vigilante|buddy cop|trial film"),
        ("Documentary", r"documentary"),
        ("Drama", r"\bdrama\b|melodrama|tragedy|slice of life"),
        ("Family", r"family film|children's"),
        ("Fantasy", r"fantasy|magic realis|supernatural|sword and sorcery"),
        ("Historical", r"historical|period drama|medieval|sword-and-sandal"),
        ("Horror", r"horror|slasher|splatter|vampire|ghost film|monster|zombie|gothic"),
        ("LGBTQ+", r"lgbt"),
        ("Musical", r"musical|\bdance film|music film"),
        ("Mystery", r"mystery|detective"),
        ("Noir", r"\bnoir\b"),
        ("Psychological", r"psychological"),
        ("Romance", r"romance|romantic|comedy of remarriage"),
        ("Sci-fi", r"science fiction|sci-fi|dystopia|cyberpunk|post-apocalyptic|time-travel|space opera|alternate history"),
        ("Sports", r"\bsports?\b|boxing|football"),
        ("Thriller", r"thriller|suspense|\bspy\b"),
        ("War", r"\bwar\b|military|partisan"),
        ("Western", r"western"),
    )
)


# A live-action film with animated passages (Avatar, Who Framed Roger Rabbit) is not an animation.
_HYBRID = re.compile(r"live[- ]action")


def genre_families(genres: Iterable[str], forms: Iterable[str] = ()) -> list[str]:
    """The families a film's raw genre labels and its Wikidata form (anime film, animated film)
    belong to, in table order."""
    labels = [str(label).casefold() for label in (*genres, *forms) if label]
    labels = [label for label in labels if not _HYBRID.search(label)]      # a form, not a genre
    return [family for family, pattern in GENRE_FAMILIES if any(pattern.search(label) for label in labels)]


def _json_list(value: Any) -> list[str]:
    try:
        items = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in items if item] if isinstance(items, list) else []


_LOCK = threading.Lock()
_CACHE: dict[str, tuple[int, dict[str, dict[str, Any]]]] = {}


def film_facets(db: Any) -> dict[str, dict[str, Any]]:
    """``{film_id: {"year", "directors", "genres"}}`` for films with open metadata.

    Cached per metadata table version: the library catalog is polled often and
    metadata changes only when an evidence pass compiles.
    """
    from pipeline.index.writer import table_names

    if FILM_META not in table_names(db):
        return {}
    table = db.open_table(FILM_META)
    key, version = str(getattr(db, "uri", "")), int(table.version)
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None and cached[0] == version:
            return cached[1]
    columns = ["film_id", "year", "directors", "genres"] + (["forms"] if "forms" in table.schema.names else [])
    rows = table.search().select(columns).limit(None).to_list()
    facets = {
        str(row["film_id"]): {
            "year": int(row["year"]) if row.get("year") else None,
            "directors": _json_list(row.get("directors")),
            "genres": genre_families(_json_list(row.get("genres")), _json_list(row.get("forms"))),
        }
        for row in rows
        if row.get("film_id")
    }
    with _LOCK:
        _CACHE[key] = (version, facets)
    return facets
