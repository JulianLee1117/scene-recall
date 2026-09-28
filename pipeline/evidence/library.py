"""Read-only views of the published library used by evidence producers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any

from lancedb.expr import col, lit

from pipeline.index.reads import iter_filtered_rows


_TITLE = re.compile(r"^(?P<name>.+?)\s*\((?P<year>\d{4})\)\s*(?:\[(?P<edition>[^\]]+)\])?\s*$")


@dataclass(frozen=True)
class FilmRef:
    film_id: str
    title: str
    path: Path
    duration: float
    fps: float

    @property
    def name(self) -> str:
        return parse_title(self.title)[0]

    @property
    def year(self) -> int | None:
        return parse_title(self.title)[1]

    @property
    def edition(self) -> str | None:
        return parse_title(self.title)[2]


def parse_title(title: str) -> tuple[str, int | None, str | None]:
    """Split ``"Film - Subtitle (1999) [Edition]"`` into name, year, edition.

    Canonical filenames replace ``:`` with `` - ``; restore the colon so
    lookups match the published title.
    """
    match = _TITLE.match(str(title or "").strip())
    if not match:
        return str(title or "").strip(), None, None
    name = match.group("name").strip().replace(" - ", ": ")
    edition = match.group("edition")
    return name, int(match.group("year")), edition.strip() if edition else None


def list_films(db: Any) -> list[FilmRef]:
    """All published films, ordered by title."""
    rows = db.open_table("films").search().select(["film_id", "title", "path", "duration", "fps"]).limit(None).to_list()
    films = [
        FilmRef(
            film_id=str(row["film_id"]),
            title=str(row.get("title") or ""),
            path=Path(str(row.get("path") or "")),
            duration=float(row.get("duration") or 0.0),
            fps=float(row.get("fps") or 0.0),
        )
        for row in rows
        if row.get("film_id")
    ]
    return sorted(films, key=lambda film: film.title.casefold())


def resolve_films(db: Any, selectors: list[str] | None) -> list[FilmRef]:
    """Resolve film IDs, ID prefixes or case-insensitive title substrings.

    ``None`` or an empty list selects every film. Ambiguous selectors fail
    instead of guessing.
    """
    films = list_films(db)
    if not selectors:
        return films
    chosen: dict[str, FilmRef] = {}
    for selector in selectors:
        needle = selector.strip().casefold()
        matches = [film for film in films if film.film_id == needle or (len(needle) >= 8 and film.film_id.startswith(needle))]
        if not matches:
            matches = [film for film in films if needle in film.title.casefold()]
        exact = [film for film in matches if film.title.casefold() == needle or film.name.casefold() == needle]
        if len(matches) > 1 and len(exact) == 1:
            matches = exact
        if not matches:
            raise ValueError(f"no published film matches {selector!r}")
        if len(matches) > 1:
            titles = ", ".join(film.title for film in matches[:6])
            raise ValueError(f"{selector!r} matches several films: {titles}")
        chosen[matches[0].film_id] = matches[0]
    return list(chosen.values())


def film_units(db: Any, film_id: str, columns: list[str] | None = None) -> list[dict[str, Any]]:
    """Return one film's shot rows in timeline order."""
    wanted = columns or ["unit_id", "film_id", "t_start", "t_end", "parent_shot_id"]
    for required in ("unit_id", "t_start", "t_end"):
        if required not in wanted:
            wanted = [*wanted, required]
    rows = list(iter_filtered_rows(db.open_table("units"), columns=wanted, where=col("film_id") == lit(film_id), batch_size=1024))
    return sorted(rows, key=lambda row: (float(row["t_start"]), str(row["unit_id"])))
