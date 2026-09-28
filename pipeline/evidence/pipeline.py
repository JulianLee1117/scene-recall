"""Bring one film's evidence up to date: every pass in order, each skipped when current.

    metadata -> subtitles -> understanding -> measure -> hero -> synthesis -> compile -> text views

Used after ingest for new films and by ``python -m pipeline.evidence refresh``.
Each step is independently cached by its producer profile and inputs, so a
refresh after a library backfill only recomputes what changed. Hosted steps are
bounded: subtitles respect the daily download quota and the understanding pass
has a per-film spend ceiling (standard transport; library backfills use the
half-price batch transport instead).
"""

from __future__ import annotations

from typing import Any, Callable

from pipeline.evidence.library import FilmRef

FILM_UNDERSTANDING_MAX_USD = 3.0


def refresh_films(config: Any, db: Any, films: list[FilmRef], *, hosted: bool = True, measure_pass: bool = True,
                  holding_ingest_lock: bool = False, progress: Callable[[str], None] = print) -> dict[str, Any]:
    """Run every evidence pass for *films*; returns per-step summaries. Failures are reported, not raised."""
    from pipeline.evidence import compile as compiler, hero, highlights, measure, metadata, subtitles, synthesis, understanding
    from pipeline.evidence.library import list_films

    summary: dict[str, Any] = {}

    def step(name: str, action: Callable[[], Any]) -> None:
        try:
            summary[name] = action()
        except Exception as exc:  # noqa: BLE001 - later passes still run on what exists
            summary[name] = {"error": str(exc)[:300]}
            progress(f"[evidence] {name} failed: {str(exc)[:300]}")

    if hosted:
        step("metadata", lambda: metadata.run(config, films))
        step("subtitles", lambda: subtitles.run(config, films, max_downloads=len(films)))
        step("understanding", lambda: understanding.run(
            config, db, films, max_usd=FILM_UNDERSTANDING_MAX_USD * len(films), concurrency=4, progress=progress))
        step("highlights", lambda: highlights.run(config, db, films, progress=progress))
    if measure_pass:
        step("measure", lambda: measure.run(config, db, films, lock_films=not holding_ingest_lock, progress=progress))
    step("hero", lambda: hero.run(config, db, films, progress=progress))
    step("synthesis", lambda: synthesis.run(config, db, films, progress=progress))
    step("compile", lambda: (compiler.compile_film_meta(config, db, list_films(db), progress=progress),
                             compiler.compile_films(config, db, films, progress=progress)))

    def text_views() -> Any:
        from pipeline.index.backfill_text import backfill_text_features, backfill_text_features_during_ingest
        backfill = backfill_text_features_during_ingest if holding_ingest_lock else backfill_text_features
        results = [backfill(config, film_id=film.film_id) for film in films]
        return {"embedded": sum(result.embedded for result in results),
                "active": all(result.activated for result in results)}

    step("text_views", text_views)
    return summary
