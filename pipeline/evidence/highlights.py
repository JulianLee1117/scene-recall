"""Film-level ranking of iconic moments (hosted text model, one call per film).

The understanding pass flags iconic moments chunk by chunk, so a famous film
ends up with dozens of shots at the top fame level and no way to tell its
signature image from a memorable beat. This step shows a text model the film's
flagged moments (and famous quotes), asks it to merge shots of the same moment
and rank moments by cultural recognizability, and records the film's visual
motifs. Synthesis turns the rank into a finer fame signal.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from pipeline.evidence import store
from pipeline.evidence.library import FilmRef

MODEL = "gemini-3.8-flash"
PROMPT_VERSION = "film-highlights-v1"
_PROMPT = """Film: {film}

Moments a shot-level pass flagged as iconic (id, time, description):
{moments}

Famous quotes from this film (Wikiquote):
{quotes}

Return JSON:
- moments: the film's most widely recognized moments in film culture, most famous first (at most 15). Merge ids that
  show the same moment. Each: ids (from the list above), label (<=10 words), score 1-10 (10 = recognized by most
  moviegoers, 5 = known to fans, 1 = barely known).
- motifs: 3-5 recurring visual motifs of this film (<=6 words each).
Use only ids from the list. Judge fame, not quality."""


def producer() -> store.Producer:
    return store.Producer(kind="highlights", name="film-rank", version=1,
                          settings={"model": MODEL, "prompt_version": PROMPT_VERSION,
                                    "prompt_sha256": store.digest(_PROMPT), "max_moments": 15})


def _schema() -> dict[str, Any]:
    return {"type": "object", "properties": {
        "moments": {"type": "array", "items": {"type": "object", "properties": {
            "ids": {"type": "array", "items": {"type": "integer"}}, "label": {"type": "string"},
            "score": {"type": "integer", "minimum": 1, "maximum": 10}}, "required": ["ids", "label", "score"]}},
        "motifs": {"type": "array", "items": {"type": "string"}}}, "required": ["moments", "motifs"]}


def rank_film(client: Any, film: FilmRef, iconic: list[dict[str, Any]], unit_times: dict[str, float],
              quotes: list[str]) -> dict[str, Any]:
    from google.genai import types

    listed = [(number, row) for number, row in enumerate(iconic, start=1)]
    moments = "\n".join(f"{number}. [{_clock(unit_times.get(row['unit_id'], 0.0))}] {row['description']}"
                        for number, row in listed)
    prompt = _PROMPT.format(film=film.title, moments=moments or "(none)",
                            quotes="\n".join(f"- {quote}" for quote in quotes[:25]) or "(none)")
    response = client.models.generate_content(
        model=MODEL, contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json", response_json_schema=_schema(),
                                           thinking_config=types.ThinkingConfig(thinking_level="low")))
    output = json.loads(response.text or "{}")
    by_number = {number: row["unit_id"] for number, row in listed}
    ranked = []
    for rank, moment in enumerate(output.get("moments") or [], start=1):
        units = [by_number[number] for number in moment.get("ids") or [] if number in by_number]
        if units:
            ranked.append({"rank": rank, "units": list(dict.fromkeys(units)), "label": str(moment.get("label") or "")[:120],
                           "score": min(10, max(1, int(moment.get("score") or 1)))})
    usage = response.usage_metadata.model_dump(mode="json") if response.usage_metadata else {}
    return {"moments": ranked[:15], "motifs": [str(m)[:60] for m in output.get("motifs") or []][:5], "usage": usage}


def _clock(seconds: float) -> str:
    return f"{int(seconds // 3600)}:{int(seconds % 3600 // 60):02d}:{int(seconds % 60):02d}"


def run(config: Any, db: Any, films: list[FilmRef], *, force: bool = False,
        progress: Callable[[str], None] = print) -> dict[str, int]:
    from google import genai
    from google.genai import types

    from pipeline.evidence import metadata, understanding
    from pipeline.evidence.http import ipv4_transport
    from pipeline.evidence.library import film_units

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(
        timeout=300_000, client_args={"transport": ipv4_transport()}))
    prod, story_producer = producer(), understanding.producer()
    counts = {"done": 0, "cached": 0, "skipped": 0, "failed": 0}
    for film in films:
        story = store.read_artifact(config.paths.assets_dir, film.film_id, story_producer)
        if story is None or not story["data"].get("iconic"):
            counts["skipped"] += 1
            continue
        inputs = {"understanding": f"{story['profile_id']}@{story['created_at']}"}
        if not force and store.read_artifact(config.paths.assets_dir, film.film_id, prod, inputs=inputs):
            counts["cached"] += 1
            continue
        meta = store.read_artifact(config.paths.assets_dir, film.film_id, metadata.PRODUCER)
        quotes = (((meta or {}).get("data") or {}).get("wikiquote") or {}).get("quotes") or []
        times = {unit["unit_id"]: float(unit["t_start"])
                 for unit in film_units(db, film.film_id, columns=["unit_id", "t_start"])}
        try:
            data = rank_film(client, film, story["data"]["iconic"], times, quotes)
        except Exception as exc:  # noqa: BLE001 - one film must not stop a library run
            counts["failed"] += 1
            progress(f"[highlights] {film.title}: failed ({str(exc)[:200]})")
            continue
        store.write_artifact(config.paths.assets_dir, film.film_id, prod, data, inputs=inputs)
        counts["done"] += 1
        top = ", ".join(moment["label"] for moment in data["moments"][:3])
        progress(f"[highlights] {film.title}: {len(data['moments'])} moments; top: {top}")
    return counts
