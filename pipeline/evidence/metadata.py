"""Open film metadata: identity, cast, plot, quotes and popularity.

Sources and licences (all usable with AI features in a personal tool):

* Wikidata (CC0): identity, IMDb ID, cast with character roles, directors,
  genres, languages.
* Wikipedia (CC BY-SA 4.0): the plot section; Wikimedia pageviews: popularity.
* Wikiquote (CC BY-SA 4.0): quotes, later aligned to subtitles.
* IMDb non-commercial ratings dataset (personal use): vote counts.

TMDB is deliberately not used: its API terms forbid use "in connection with
... a machine learning (ML) or artificial intelligence (AI) based Application".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
import difflib
import gzip
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable

from pipeline.evidence import store
from pipeline.evidence.http import HttpError, JsonClient
from pipeline.evidence.library import FilmRef


PRODUCER = store.Producer(
    kind="metadata",
    name="open-data",
    version=1,
    settings={
        "sources": ["wikidata", "wikidata-forms", "wikipedia-plot", "wikiquote", "wikimedia-pageviews", "imdb-ratings"],
        "plot_max_chars": 12000,
        "quotes_max_chars": 40000,
        "pageview_months": 12,
    },
)

WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIQUOTE_API = "https://en.wikiquote.org/w/api.php"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
PAGEVIEWS_API = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/user"
IMDB_RATINGS_URL = "https://datasets.imdbws.com/title.ratings.tsv.gz"

# Classes that make a Wikidata item an acceptable film match.
FILM_CLASSES = {
    "Q11424",      # film
    "Q24862",      # short film
    "Q24869",      # feature film
    "Q506240",     # television film
    "Q202866",     # animated film
    "Q29168811",   # animated feature film
    "Q226730",     # silent film
    "Q93204",      # documentary film
    "Q20650540",   # anthology film
    "Q229390",     # 3D film
}
_PLOT_HEADINGS = ("plot", "plot summary", "synopsis", "premise", "story", "summary")
# Forms that say nothing beyond "a film": every other P31 class (anime film, animated film, silent
# film, documentary film, short film) is kept as the film's form beside its genres.
_PLAIN_FORMS = {"Q11424", "Q24869"}


def normalize_title(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", text)


def title_similarity(left: str, right: str) -> float:
    a, b = normalize_title(left), normalize_title(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------------------
# Wikidata helpers
# ---------------------------------------------------------------------------


def _claim_values(entity: dict[str, Any], prop: str) -> list[Any]:
    values = []
    for claim in entity.get("claims", {}).get(prop, []):
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        values.append(snak.get("datavalue", {}).get("value"))
    return values


def _claim_ids(entity: dict[str, Any], prop: str) -> list[str]:
    return [value["id"] for value in _claim_values(entity, prop) if isinstance(value, dict) and "id" in value]


def _years(entity: dict[str, Any]) -> list[int]:
    years = []
    for value in _claim_values(entity, "P577"):
        if isinstance(value, dict):
            match = re.match(r"^[+-]?(\d{4})", str(value.get("time", "")))
            if match:
                years.append(int(match.group(1)))
    return sorted(set(years))


def _label(entity: dict[str, Any]) -> str:
    labels = entity.get("labels", {})
    for language in ("en", "en-gb", "en-us", "mul"):
        if language in labels:
            return str(labels[language]["value"])
    return next((str(value["value"]) for value in labels.values()), "")


def _cast(entity: dict[str, Any]) -> list[dict[str, Any]]:
    cast = []
    for order, claim in enumerate(entity.get("claims", {}).get("P161", [])):
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") != "value":
            continue
        actor = snak.get("datavalue", {}).get("value", {}).get("id")
        characters = []
        for qualifier in claim.get("qualifiers", {}).get("P453", []):  # character role
            value = qualifier.get("datavalue", {}).get("value")
            if isinstance(value, dict) and "id" in value:
                characters.append({"id": value["id"]})
        for qualifier in claim.get("qualifiers", {}).get("P4633", []):  # name of the character role
            value = qualifier.get("datavalue", {}).get("value")
            if isinstance(value, str):
                characters.append({"name": value})
        if actor:
            cast.append({"order": order, "actor_id": actor, "characters": characters})
    return cast


def _is_film(entity: dict[str, Any]) -> bool:
    if set(_claim_ids(entity, "P31")) & FILM_CLASSES:
        return True
    description = str(entity.get("descriptions", {}).get("en", {}).get("value", ""))
    return bool(re.search(r"\bfilm\b|\bmovie\b", description, re.IGNORECASE))


@dataclass
class OpenData:
    """Fetch helpers bound to one polite client (injectable for tests)."""

    client: JsonClient

    def wikipedia_search(self, query: str, limit: int = 6) -> list[str]:
        data = self.client.get(WIKIPEDIA_API, {"action": "query", "list": "search", "srsearch": query,
                                               "srlimit": limit, "format": "json"}) or {}
        return [row["title"] for row in data.get("query", {}).get("search", [])]

    def wikibase_items(self, titles: list[str]) -> dict[str, str]:
        if not titles:
            return {}
        data = self.client.get(WIKIPEDIA_API, {"action": "query", "prop": "pageprops", "ppprop": "wikibase_item",
                                               "titles": "|".join(titles[:50]), "redirects": 1, "format": "json"}) or {}
        result = {}
        for page in data.get("query", {}).get("pages", {}).values():
            qid = page.get("pageprops", {}).get("wikibase_item")
            if qid:
                result[page.get("title", "")] = qid
        return result

    def entities(self, ids: list[str], props: str = "labels|aliases|descriptions|claims|sitelinks") -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        unique = list(dict.fromkeys(ids))
        for start in range(0, len(unique), 50):
            data = self.client.get(WIKIDATA_API, {"action": "wbgetentities", "ids": "|".join(unique[start:start + 50]),
                                                  "props": props, "languages": "en|mul", "format": "json"}) or {}
            for qid, entity in (data.get("entities") or {}).items():
                if "missing" not in entity:
                    result[qid] = entity
        return result

    def wikidata_search(self, name: str, limit: int = 7) -> list[str]:
        data = self.client.get(WIKIDATA_API, {"action": "wbsearchentities", "search": name, "language": "en",
                                              "type": "item", "limit": limit, "format": "json"}) or {}
        return [row["id"] for row in data.get("search", []) if row.get("id")]

    def extract(self, api: str, title: str) -> tuple[str, str] | None:
        data = self.client.get(api, {"action": "query", "prop": "extracts", "explaintext": 1, "titles": title,
                                     "redirects": 1, "format": "json"}) or {}
        pages = list(data.get("query", {}).get("pages", {}).values())
        if not pages or "missing" in pages[0]:
            return None
        return str(pages[0].get("title", title)), str(pages[0].get("extract", ""))

    def pageviews(self, title: str, months: int, today: date | None = None) -> int | None:
        today = today or datetime.now(UTC).date()
        end = date(today.year, today.month, 1) - timedelta(days=1)
        start_year, start_month = end.year, end.month - months + 1
        while start_month <= 0:
            start_month += 12
            start_year -= 1
        article = title.replace(" ", "_")
        url = f"{PAGEVIEWS_API}/{_path_quote(article)}/monthly/{start_year:04d}{start_month:02d}0100/{end:%Y%m%d}00"
        data = self.client.get(url)
        if not data:
            return None
        return int(sum(int(item.get("views", 0)) for item in data.get("items", [])))


def _path_quote(value: str) -> str:
    from urllib.parse import quote
    return quote(value, safe="")


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


def choose_film_entity(film: FilmRef, candidates: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
    """Pick the Wikidata film item best matching the canonical title and year."""
    best = None
    for qid, entity in candidates.items():
        if not _is_film(entity):
            continue
        years = _years(entity)
        year_gap = min((abs(year - film.year) for year in years), default=None) if film.year else 0
        if film.year and (year_gap is None or year_gap > 1):
            continue
        names = [_label(entity)] + [alias["value"] for alias in entity.get("aliases", {}).get("en", [])]
        enwiki = entity.get("sitelinks", {}).get("enwiki", {}).get("title")
        if enwiki:
            names.append(re.sub(r"\s*\((?:\d{4} )?film\)$", "", enwiki))
        similarity = max(title_similarity(film.name, name) for name in names if name) if any(names) else 0.0
        if similarity < 0.6:
            continue
        score = similarity - 0.1 * (year_gap or 0) + (0.05 if enwiki else 0.0)
        if best is None or score > best[0]:
            best = (score, qid, entity, {"similarity": round(similarity, 3), "year_gap": year_gap})
    if best is None:
        return None
    return best[1], best[2], best[3]


def find_film_entity(api: OpenData, film: FilmRef, override: str | None = None) -> tuple[str, dict[str, Any], dict[str, Any]] | None:
    if override:
        entity = api.entities([override]).get(override)
        return (override, entity, {"method": "override"}) if entity else None
    queries = [f"{film.name} {film.year} film" if film.year else f"{film.name} film", film.name]
    titles: list[str] = []
    for query in queries:
        titles += [title for title in api.wikipedia_search(query) if title not in titles]
    qids = list(dict.fromkeys(api.wikibase_items(titles).values()))
    chosen = choose_film_entity(film, api.entities(qids)) if qids else None
    if chosen:
        return chosen[0], chosen[1], {**chosen[2], "method": "wikipedia-search"}
    qids = api.wikidata_search(film.name)
    chosen = choose_film_entity(film, api.entities(qids)) if qids else None
    if chosen:
        return chosen[0], chosen[1], {**chosen[2], "method": "wikidata-search"}
    return None


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


def plot_section(extract: str, max_chars: int) -> str:
    """Return the plot section (including its sub-sections) from a plain-text extract."""
    body: list[str] = []
    inside = False
    for line in extract.splitlines():
        heading = re.match(r"^(={2,})\s*(.+?)\s*\1\s*$", line)
        if heading:
            if len(heading.group(1)) == 2:
                if inside:
                    break
                inside = heading.group(2).strip().casefold() in _PLOT_HEADINGS
            continue
        if inside:
            body.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(body)).strip()[:max_chars]


def parse_quotes(extract: str, max_chars: int) -> list[str]:
    """Extract candidate quote lines from a Wikiquote plain-text page."""
    text = extract[:max_chars]
    stop = re.search(r"\n==\s*(Cast|External links|See also|Taglines?)\s*==", text, re.IGNORECASE)
    if stop:
        text = text[:stop.start()]
    quotes = []
    for raw in text.splitlines():
        line = raw.strip().strip("•*-").strip()
        if not line or line.startswith("=") or len(line) < 12 or len(line) > 400:
            continue
        line = re.sub(r"^[A-Z][\w .'\-]{0,40}:\s+", "", line)  # drop a leading "Speaker:"
        line = re.sub(r"\[[^\]]{0,80}\]", "", line).strip()      # stage directions
        if len(line.split()) >= 3 and line not in quotes:
            quotes.append(line)
    return quotes[:400]


# ---------------------------------------------------------------------------
# IMDb ratings (library-wide, refreshed at most weekly)
# ---------------------------------------------------------------------------


def imdb_ratings(assets_dir: Path, client: JsonClient, *, max_age_days: int = 7,
                 wanted: set[str] | None = None) -> dict[str, tuple[float, int]]:
    """Return ``{tconst: (rating, votes)}`` from the cached IMDb ratings dataset."""
    path = store.library_path(assets_dir, "imdb", "title.ratings.tsv.gz")
    fresh = path.is_file() and (datetime.now().timestamp() - path.stat().st_mtime) < max_age_days * 86400
    if not fresh:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".part")
        try:
            client.download(IMDB_RATINGS_URL, temporary)
            temporary.replace(path)
        except (HttpError, OSError):
            temporary.unlink(missing_ok=True)
            if not path.is_file():
                return {}
    ratings: dict[str, tuple[float, int]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        next(handle, None)
        for line in handle:
            tconst, rating, votes = line.rstrip("\n").split("\t")[:3]
            if wanted is None or tconst in wanted:
                ratings[tconst] = (float(rating), int(votes))
    return ratings


# ---------------------------------------------------------------------------
# Producer
# ---------------------------------------------------------------------------


def film_inputs(film: FilmRef) -> dict[str, str]:
    return {"title": store.digest(film.title)}


def build_metadata(api: OpenData, film: FilmRef, *, override: str | None = None,
                   ratings: dict[str, tuple[float, int]] | None = None) -> dict[str, Any]:
    """Collect the open metadata document for one film (no writes)."""
    settings = PRODUCER.settings
    found = find_film_entity(api, film, override)
    result: dict[str, Any] = {"query": {"name": film.name, "year": film.year, "edition": film.edition}}
    if found is None:
        result["match"] = {"status": "unmatched"}
        return result
    qid, entity, match = found
    result["match"] = {"status": "matched", "wikidata_id": qid, **match}
    cast = _cast(entity)
    forms = [item for item in _claim_ids(entity, "P31") if item not in _PLAIN_FORMS]
    referenced = (_claim_ids(entity, "P57") + _claim_ids(entity, "P136") + forms + _claim_ids(entity, "P495")
                  + _claim_ids(entity, "P364") + [row["actor_id"] for row in cast]
                  + [c["id"] for row in cast for c in row["characters"] if "id" in c])
    labels = {key: _label(value) for key, value in api.entities(referenced, props="labels").items()} if referenced else {}
    imdb_ids = [value for value in _claim_values(entity, "P345") if isinstance(value, str) and value.startswith("tt")]
    duration = next((float(value["amount"]) for value in _claim_values(entity, "P2047")
                     if isinstance(value, dict) and "amount" in value), None)
    sitelinks = entity.get("sitelinks", {})
    result["wikidata"] = {
        "id": qid,
        "label": _label(entity),
        "description": entity.get("descriptions", {}).get("en", {}).get("value", ""),
        "years": _years(entity),
        "imdb_id": imdb_ids[0] if imdb_ids else None,
        "directors": [labels.get(item, item) for item in _claim_ids(entity, "P57")],
        "genres": [labels.get(item, item) for item in _claim_ids(entity, "P136")],
        "forms": [labels.get(item, item) for item in forms],
        "countries": [labels.get(item, item) for item in _claim_ids(entity, "P495")],
        "languages": [labels.get(item, item) for item in _claim_ids(entity, "P364")],
        "duration_minutes": duration,
        "cast": [
            {"order": row["order"], "actor": labels.get(row["actor_id"], row["actor_id"]),
             "characters": [c.get("name") or labels.get(c.get("id", ""), c.get("id")) for c in row["characters"]]}
            for row in cast
        ],
    }
    enwiki = sitelinks.get("enwiki", {}).get("title")
    if enwiki:
        extract = api.extract(WIKIPEDIA_API, enwiki)
        if extract:
            result["wikipedia"] = {"title": extract[0], "plot": plot_section(extract[1], settings["plot_max_chars"])}
        views = api.pageviews(enwiki, settings["pageview_months"])
        result["popularity"] = {"pageviews_12m": views}
    enquote = sitelinks.get("enwikiquote", {}).get("title")
    if enquote:
        extract = api.extract(WIKIQUOTE_API, enquote)
        if extract:
            result["wikiquote"] = {"title": extract[0], "quotes": parse_quotes(extract[1], settings["quotes_max_chars"])}
    imdb_id = result["wikidata"]["imdb_id"]
    if ratings is not None and imdb_id in ratings:
        rating, votes = ratings[imdb_id]
        result.setdefault("popularity", {}).update(imdb_rating=rating, imdb_votes=votes)
    return result


def load_overrides(state_dir: Path) -> dict[str, str]:
    """User-authored corrections: ``{film_id: wikidata_qid}``."""
    data = store.read_json(Path(state_dir) / "evidence" / "metadata-overrides.json")
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items() if re.fullmatch(r"Q\d+", str(value))}


def run(config: Any, films: list[FilmRef], *, force: bool = False,
        progress: Callable[[str], None] = print, client: JsonClient | None = None) -> dict[str, int]:
    """Produce metadata artifacts for *films*; returns counts by outcome."""
    counts = {"cached": 0, "matched": 0, "unmatched": 0, "failed": 0}
    overrides = load_overrides(config.paths.state_dir)
    own_client = client is None
    client = client or JsonClient()
    try:
        api = OpenData(client)
        ratings = None
        for index, film in enumerate(films, start=1):
            inputs = {**film_inputs(film), "override": store.digest(overrides.get(film.film_id))}
            if not force and store.read_artifact(config.paths.assets_dir, film.film_id, PRODUCER, inputs=inputs):
                counts["cached"] += 1
                continue
            if ratings is None:
                ratings = imdb_ratings(config.paths.assets_dir, client)
            try:
                data = build_metadata(api, film, override=overrides.get(film.film_id), ratings=ratings)
            except HttpError as exc:
                counts["failed"] += 1
                progress(f"[metadata] {film.title}: failed ({exc})")
                continue
            store.write_artifact(config.paths.assets_dir, film.film_id, PRODUCER, data, inputs=inputs)
            status = data["match"]["status"]
            counts[status] += 1
            detail = data.get("wikidata", {})
            progress(f"[metadata] {index}/{len(films)} {film.title}: {status}"
                     + (f" -> {detail.get('label')} ({detail.get('id')}, {detail.get('imdb_id')})" if detail else ""))
    finally:
        if own_client:
            client.close()
    return counts
