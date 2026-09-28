"""Dialogue v2: official English subtitles for films that only had Whisper text.

For each film whose dialogue came from Whisper, search OpenSubtitles by IMDb ID
and file hash, download the best candidate (daily quota), keep the raw bytes in
the evidence archive, synchronize it to the Whisper speech timing, validate it,
and publish a synced SRT that ingestion's dialogue stage prefers over Whisper.

The Whisper transcript is preserved as ``whisper-reference.json`` beside the
artifact: it is the sync reference and the original-language dialogue view.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from pipeline.evidence import speech, store, subsync
from pipeline.evidence.library import FilmRef
from pipeline.evidence.opensubtitles import Client, OpenSubtitlesError, QuotaExhausted, movie_hash, rank_candidates


# Calibrated against Silero VAD speech on the first 12 foreign films: aligned
# files reach lift 1.7-3.5 and prominence 5-10; chance alignment sits near lift
# 1.0. Raw precision is reported but not gated: subtitles stay on screen longer
# than speech, so sparse-dialogue films show low precision even when aligned.
ACCEPT = {"min_lift": 1.3, "min_prominence": 5.0, "min_text_agreement": 0.2}
PRODUCER = store.Producer(
    kind="subtitles",
    name="opensubtitles-sync",
    version=1,
    settings={
        "language": "en",
        "resolution": subsync.RESOLUTION,
        "scales": [round(scale, 6) for scale in subsync.SCALES],
        "window_seconds": subsync.WINDOW_SECONDS,
        "window_max_shift": subsync.WINDOW_MAX_SHIFT,
        "accept": ACCEPT,
        "speech_reference": speech.PRODUCER.profile_id,
    },
)
MAX_ATTEMPTS_PER_FILM = 2


def archive_dir(config: Any, film_id: str) -> Path:
    """Raw downloads live beside release evidence, outside rebuildable assets."""
    return Path(config.paths.incoming_dir).parent / "evidence" / "subtitles" / film_id


def evidence_dir(config: Any, film_id: str) -> Path:
    return Path(config.paths.assets_dir) / film_id / "evidence" / PRODUCER.kind


def synced_path(config: Any, film_id: str) -> Path:
    return evidence_dir(config, film_id) / f"{PRODUCER.profile_id}.en.srt"


def reference_path(config: Any, film_id: str) -> Path:
    return evidence_dir(config, film_id) / "whisper-reference.json"


def _dialogue_manifest(config: Any, film_id: str) -> dict[str, Any] | None:
    data = store.read_json(Path(config.paths.assets_dir) / film_id / "dialogue.manifest.json")
    return data if isinstance(data, dict) else None


def load_reference(config: Any, film_id: str, *, capture: bool) -> list[subsync.Cue] | None:
    """Return the preserved Whisper cues, capturing them from current dialogue once."""
    path = reference_path(config, film_id)
    data = store.read_json(path)
    if data is None and capture:
        manifest = _dialogue_manifest(config, film_id)
        current = Path(config.paths.assets_dir) / film_id / "dialogue.json"
        if manifest and manifest.get("kind") == "whisper" and current.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            store.write_json(path, {"manifest": manifest, "lines": store.read_json(current)})
            data = store.read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
        return None
    return [(float(row["start"]), float(row["end"]), str(row["text"])) for row in data["lines"]
            if isinstance(row, dict) and float(row["end"]) > float(row["start"])]


def needs_subtitles(config: Any, film: FilmRef) -> bool:
    manifest = _dialogue_manifest(config, film.film_id)
    return bool(manifest and manifest.get("kind") == "whisper") or reference_path(config, film.film_id).is_file()


def format_srt(cues: list[subsync.Cue]) -> str:
    def stamp(seconds: float) -> str:
        millis = int(round(max(0.0, seconds) * 1000))
        hours, millis = divmod(millis, 3_600_000)
        minutes, millis = divmod(millis, 60_000)
        secs, millis = divmod(millis, 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
    blocks = [f"{index}\n{stamp(start)} --> {stamp(end)}\n{text}\n" for index, (start, end, text) in enumerate(cues, start=1)]
    return "\n".join(blocks)


def _parse(raw: bytes) -> list[subsync.Cue]:
    from pipeline.ingest.subtitles import parse_external_dialogue_srt
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    return [(cue.start, cue.end, cue.text) for cue in parse_external_dialogue_srt(text) if cue.end > cue.start]


def decide(result: subsync.SyncResult, english_reference: bool) -> tuple[bool, list[str]]:
    reasons = []
    if result.lift < ACCEPT["min_lift"]:
        reasons.append(f"speech overlap lift {result.lift:.2f} < {ACCEPT['min_lift']}")
    if result.prominence < ACCEPT["min_prominence"]:
        reasons.append(f"offset prominence {result.prominence:.1f} < {ACCEPT['min_prominence']}")
    if english_reference and (result.text_agreement or 0.0) < ACCEPT["min_text_agreement"]:
        reasons.append(f"text agreement {result.text_agreement or 0:.2f} < {ACCEPT['min_text_agreement']}")
    return not reasons, reasons


def _raw_file(config: Any, film_id: str, file_id: int) -> Path:
    return archive_dir(config, film_id) / f"opensubtitles-{file_id}.srt"


def process_film(config: Any, film: FilmRef, client: Client, imdb_id: str | None,
                 progress: Callable[[str], None]) -> dict[str, Any]:
    """Return artifact data for one film (may download up to MAX_ATTEMPTS_PER_FILM files)."""
    reference = load_reference(config, film.film_id, capture=True)
    if not reference:
        return {"status": "no_reference"}
    if not imdb_id:
        return {"status": "no_imdb_id"}
    english_reference = subsync.is_mostly_ascii(reference)
    speech_reference = [(start, end, "") for start, end in speech.speech_intervals(config, film)]
    try:
        film_hash = movie_hash(film.path)
    except (OSError, ValueError):
        film_hash = None
    candidates = client.search(imdb_id=imdb_id)
    if film_hash:
        candidates += client.search(imdb_id=imdb_id, moviehash=film_hash)
    ranked = rank_candidates(candidates, film_file=film.path.name, film_fps=film.fps or None, imdb_id=imdb_id)
    data: dict[str, Any] = {"status": "no_candidates", "imdb_id": imdb_id, "moviehash": film_hash,
                            "english_reference": english_reference, "candidates": [
                                {"score": round(score, 2), **candidate.as_dict()} for score, candidate in ranked[:8]],
                            "attempts": []}
    for score, candidate in ranked[:MAX_ATTEMPTS_PER_FILM]:
        raw_path = _raw_file(config, film.film_id, candidate.file_id)
        if raw_path.is_file():
            raw = raw_path.read_bytes()
        else:
            raw = client.download(candidate.file_id)
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_bytes(raw)
            (raw_path.with_suffix(".json")).write_text(json.dumps(candidate.as_dict(), indent=1), encoding="utf-8")
        cues = _parse(raw)
        attempt: dict[str, Any] = {"file_id": candidate.file_id, "release": candidate.release, "score": round(score, 2),
                                   "raw_sha256": store.file_digest(raw_path), "cues": len(cues)}
        if len(cues) < 50:
            attempt.update(accepted=False, reasons=["too few dialogue cues"])
            data["attempts"].append(attempt)
            continue
        result = subsync.align(speech_reference, cues, film.duration)
        synced = subsync.apply(cues, result)
        result.text_agreement = subsync.text_agreement(reference, synced) if english_reference else None
        accepted, reasons = decide(result, english_reference)
        attempt.update(sync=result.as_dict(), accepted=accepted, reasons=reasons)
        data["attempts"].append(attempt)
        progress(f"[subtitles] {film.title}: file {candidate.file_id} scale={result.scale:.4f} offset={result.offset:+.1f}s "
                 f"lift={result.lift:.2f} prom={result.prominence:.1f} text={result.text_agreement} -> "
                 f"{'accepted' if accepted else 'rejected: ' + '; '.join(reasons)}")
        if accepted:
            path = synced_path(config, film.film_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            store._atomic_write_bytes(path, format_srt(synced).encode("utf-8"))
            from pipeline.ingest.subtitles import validate_external_srt
            validation = validate_external_srt(path, film.duration)
            data.update(status="accepted" if validation.automatic_eligible else "invalid",
                        chosen_file_id=candidate.file_id, synced_file=path.name,
                        synced_sha256=store.file_digest(path),
                        validation={"automatic_eligible": validation.automatic_eligible,
                                    "reasons": list(validation.reasons), "profile": validation.profile})
            return data
    if data["attempts"]:
        data["status"] = "rejected"
    return data


def accepted_download(config: Any, film_id: str) -> dict[str, Any] | None:
    """The accepted synced subtitle for a film, verified against its recorded hash."""
    artifact = store.read_artifact(config.paths.assets_dir, film_id, PRODUCER)
    if artifact is None:
        return None
    data = artifact.get("data") or {}
    path = synced_path(config, film_id)
    if data.get("status") != "accepted" or not path.is_file():
        return None
    if store.file_digest(path) != data.get("synced_sha256"):
        return None
    return {"path": path, "file_id": data.get("chosen_file_id"), "synced_sha256": data["synced_sha256"],
            "profile_id": PRODUCER.profile_id}


def run(config: Any, films: list[FilmRef], *, max_downloads: int = 20, force: bool = False,
        progress: Callable[[str], None] = print, client: Client | None = None) -> dict[str, int]:
    """Process films that need subtitles, foreign-language films first."""
    counts: dict[str, int] = {}
    todo = [film for film in films if needs_subtitles(config, film)]
    meta = {film.film_id: (store.read_artifact(config.paths.assets_dir, film.film_id, _metadata_producer()) or {}).get("data", {})
            for film in todo}

    def priority(film: FilmRef) -> tuple[int, float]:
        reference = load_reference(config, film.film_id, capture=True) or []
        english = subsync.is_mostly_ascii(reference) if reference else True
        votes = (meta[film.film_id].get("popularity") or {}).get("imdb_votes") or 0
        return (1 if english else 0, -float(votes))

    todo.sort(key=priority)
    own_client = client is None
    client = client or Client.from_env()
    downloads_before: int | None = None
    logged_in = False
    try:
        for film in todo:
            imdb_id = (meta[film.film_id].get("wikidata") or {}).get("imdb_id")
            inputs = {"reference": store.digest(load_reference(config, film.film_id, capture=True) or []),
                      "imdb_id": store.digest(imdb_id)}
            existing = store.read_artifact(config.paths.assets_dir, film.film_id, PRODUCER, inputs=inputs)
            if existing and not force and existing["data"].get("status") in {"accepted", "rejected", "no_candidates", "invalid"}:
                counts["cached"] = counts.get("cached", 0) + 1
                continue
            if client.remaining is not None and client.remaining < MAX_ATTEMPTS_PER_FILM and not _all_raw_present(config, film):
                progress(f"[subtitles] stopping: {client.remaining} downloads left today")
                break
            if not logged_in:
                client.login()
                logged_in = True
                downloads_before = client.remaining
                progress(f"[subtitles] {len(todo)} films need subtitles" + (
                    f"; {client.remaining} downloads left today" if client.remaining is not None else ""))
            try:
                data = process_film(config, film, client, imdb_id, progress)
            except QuotaExhausted:
                progress("[subtitles] daily download quota exhausted; rerun tomorrow to continue")
                break
            except (OpenSubtitlesError, ValueError, OSError) as exc:
                progress(f"[subtitles] {film.title}: failed ({exc})")
                counts["failed"] = counts.get("failed", 0) + 1
                continue
            store.write_artifact(config.paths.assets_dir, film.film_id, PRODUCER, data, inputs=inputs)
            counts[data["status"]] = counts.get(data["status"], 0) + 1
            if downloads_before is not None and client.remaining is not None and downloads_before - client.remaining >= max_downloads:
                progress(f"[subtitles] reached this run's download budget ({max_downloads})")
                break
    finally:
        if own_client:
            client.close()
    return counts


def _all_raw_present(config: Any, film: FilmRef) -> bool:
    directory = archive_dir(config, film.film_id)
    return directory.is_dir() and any(directory.glob("opensubtitles-*.srt"))


def _metadata_producer() -> store.Producer:
    from pipeline.evidence.metadata import PRODUCER as METADATA
    return METADATA


def current_dialogue(config: Any, film_id: str) -> list[dict[str, Any]]:
    """Best available timed dialogue: an accepted synced download, else dialogue.json.

    Ingest adopts accepted downloads on the next re-ingest; producers that run
    before then should already see the English lines.
    """
    download = accepted_download(config, film_id)
    if download is not None:
        return [{"start": start, "end": end, "text": text} for start, end, text in _parse(Path(download["path"]).read_bytes())]
    data = store.read_json(Path(config.paths.assets_dir) / film_id / "dialogue.json")
    return data if isinstance(data, list) else []
