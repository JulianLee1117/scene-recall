"""Immutable, source-window PCM shared by browser audition and export.

Voice focus uses a declared surround center, never stereo phase cancellation.
These regenerable local derivatives are not transcription or isolated speech.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid

from filelock import FileLock, Timeout

from pipeline.ingest.playback import _fingerprint, _ordinary_path, _selected_streams, PlaybackPreparationError
from pipeline.ingest.probe import _content_hash
from pipeline.intake import move_file_no_replace
from pipeline.lab.models import DialogueAudioSource


PROFILE = "source-window-pcm48-center-band-v1"
SURROUND_WITH_CENTER = frozenset({"5.1", "5.1(side)", "7.1", "7.1(wide)", "7.1(wide-side)"})
_HEX = re.compile(r"[0-9a-f]{64}")


class DialogueAudioBusy(ValueError):
    pass


def _source_request(clip):
    return DialogueAudioSource.model_validate({key: clip[key] for key in DialogueAudioSource.model_fields if key in clip}).model_dump(mode="json")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def _safe(path):
    try:
        return _ordinary_path(path)
    except PlaybackPreparationError as exc:
        raise ValueError(str(exc)) from exc


def _stat(path):
    try:
        return _fingerprint(path)
    except (OSError, PlaybackPreparationError) as exc:
        raise ValueError("Dialogue source or audio asset is unavailable or not an ordinary file") from exc


def _require_source(path, expected):
    if _stat(path) != expected:
        raise ValueError("Dialogue source changed during audio preparation")


def source_info(db, film_id):
    """Authoritative source stream and fingerprint; no generation or mutation."""
    from pipeline.lab.media import probe_media, resolve_film

    film = resolve_film(db, film_id)
    path = _safe(Path(film["path"]))
    fingerprint = _stat(path)
    if _content_hash(path) != film_id:
        raise ValueError("Dialogue source changed; restore the indexed original film before rendering")
    probe = probe_media(path)
    _require_source(path, fingerprint)
    _, audio = _selected_streams(probe)
    if audio is None:
        raise ValueError(f"Dialogue source {film.get('title', film_id)} has no audio stream")
    duration = float(probe.get("format", {}).get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Dialogue source has no readable duration")
    return {"path": path, "fingerprint": fingerprint, "film_id": film_id,
            "duration": min(float(film["duration"]), duration), "audio_stream_index": int(audio["index"]),
            "channel_layout": audio.get("channel_layout"), "channels": int(audio.get("channels") or 0)}


def identity_for(request, fingerprint):
    return {"profile": PROFILE, "source": fingerprint, **_source_request(request)}


def describe_clip(clip, source):
    request = _source_request(clip)
    if request["source_end"] > source["duration"] + .001:
        raise ValueError(f"Dialogue clip {clip.get('id', '')} extends beyond its source film")
    focused = request["source_audio_mode"] == "voice_focus"
    center = focused and source["channels"] in {6, 8} and source["channel_layout"] in SURROUND_WITH_CENTER
    return {"audio_stream_index": source["audio_stream_index"],
            "source_fingerprint": {"film_id": source["film_id"], **source["fingerprint"]},
            "audio_processing_profile": PROFILE,
            "audio_channel_method": "center" if center else "filtered_mix" if focused else "original_mix",
            "source_audio_key": _digest(identity_for(request, source["fingerprint"]))}


def processing_filters(request, channel_method):
    """Preserve source-clock gaps, then process; never reset away an audio offset."""
    duration = request["source_end"] - request["source_start"]
    filters = ["aresample=48000:async=1:first_pts=0"]
    if channel_method == "center":
        filters.append("pan=stereo|FL=FC|FR=FC")
    filters.append("aformat=sample_fmts=fltp:channel_layouts=stereo")
    if request["source_audio_mode"] == "voice_focus":
        filters.extend(["highpass=f=80:p=2", "lowpass=f=9000:p=2"])
    filters.extend([f"atrim=duration={duration:.12g}", f"apad=whole_dur={duration:.12g}",
                    f"atrim=end_sample={round(duration * 48000)}"])
    return ",".join(filters)


def cache_root(config):
    return _safe(config.paths.assets_dir / "lab" / "dialogue-audio" / PROFILE)


def audio_asset_path(config, asset_id):
    """Only content-addressed ordinary assets; this URL never changes bytes."""
    if not isinstance(asset_id, str) or not _HEX.fullmatch(asset_id):
        raise ValueError("Invalid dialogue audio asset identity")
    path = _safe(cache_root(config) / "audio" / f"{asset_id}.wav")
    if not path.parent.is_dir():
        raise KeyError("Dialogue audio is unavailable; prepare this source range again")
    # The existing idle collector uses this same small guard and rechecks age.
    # Touch only after verification; immutable content/ETag remains the SHA256.
    try:
        with FileLock(_safe(path.with_suffix(".lock")), timeout=5, preserve_lock_file=True):
            if not path.is_file():
                raise KeyError("Dialogue audio is unavailable; prepare this source range again")
            before = _stat(path)
            with path.open("rb") as handle:
                actual = hashlib.file_digest(handle, "sha256").hexdigest()
            if actual != asset_id or _stat(path) != before:
                raise ValueError("Dialogue audio asset changed; prepare the source range again")
            os.utime(path, None)
    except Timeout as exc:
        raise DialogueAudioBusy("Dialogue audio is busy; retry shortly") from exc
    return path


def _cached(config, receipt, identity):
    try:
        _safe(receipt)
        if not receipt.is_file() or receipt.stat().st_size > 32768:
            return None
        data = json.loads(receipt.read_text(encoding="utf-8"))
        if data.get("identity") != identity:
            return None
        audio_asset_path(config, data["asset_id"])
        return data
    except DialogueAudioBusy:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        return None


def prepare_dialogue_audio(config, db, clip, *, cancelled=lambda: False):
    """One bounded, locked preparation; all levels/fades remain editable outside it."""
    from pipeline.lab.media import JobCancelled, probe_media, resolve_film, run_process

    request = _source_request(clip)
    film = resolve_film(db, request["film_id"])
    source_path = _safe(Path(film["path"]))
    fingerprint = _stat(source_path)
    if request["source_end"] > float(film["duration"]) + .001:
        raise ValueError("Dialogue range extends beyond its source film")
    identity = identity_for(request, fingerprint)
    key = _digest(identity)
    if clip.get("source_audio_key") not in {None, key}:
        raise ValueError("Dialogue source changed after the render was prepared")
    root = cache_root(config)
    for directory in (root / "requests", root / "audio", root / "scratch"):
        _safe(directory).mkdir(parents=True, exist_ok=True)
    receipt = root / "requests" / f"{key}.json"
    lock = _safe(root / "requests" / f"{key}.lock")
    try:
        with FileLock(lock, timeout=5, preserve_lock_file=True):
            if cancelled():
                raise JobCancelled("Dialogue audio preparation cancelled")
            _require_source(source_path, fingerprint)
            cached = _cached(config, receipt, identity)
            if cached is not None:
                _require_source(source_path, fingerprint)
                return cached
            source = source_info(db, request["film_id"])
            if source["fingerprint"] != fingerprint:
                raise ValueError("Dialogue source changed during audio preparation")
            description = describe_clip(clip, source)
            temporary = _safe(root / "scratch" / f"{uuid.uuid4().hex}.wav")
            receipt_temporary = _safe(root / "scratch" / f"{uuid.uuid4().hex}.json")
            try:
                run_process(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-n",
                             "-ss", str(request["source_start"]), "-t", str(request["source_end"] - request["source_start"]),
                             "-i", str(source_path), "-map", f"0:{source['audio_stream_index']}", "-vn", "-sn", "-dn",
                             "-af", processing_filters(request, description["audio_channel_method"]),
                             "-c:a", "pcm_f32le", "-fflags", "+bitexact", str(temporary)], cancelled=cancelled, timeout=180)
                _require_source(source_path, fingerprint)
                meta = probe_media(temporary)
                audio = next((stream for stream in meta["streams"] if stream.get("codec_type") == "audio"), None)
                duration = round((request["source_end"] - request["source_start"]) * 48000) / 48000
                if (audio is None or audio.get("codec_name") != "pcm_f32le" or audio.get("channels") != 2
                        or audio.get("sample_rate") != "48000" or abs(float(meta["format"]["duration"]) - duration) > 1 / 48000):
                    raise ValueError("Prepared dialogue audio did not match the requested source range")
                with temporary.open("rb") as handle:
                    asset_id = hashlib.file_digest(handle, "sha256").hexdigest()
                destination = _safe(root / "audio" / f"{asset_id}.wav")
                try:
                    move_file_no_replace(temporary, destination)
                except FileExistsError:
                    audio_asset_path(config, asset_id)
                _require_source(source_path, fingerprint)
                if cancelled():
                    raise JobCancelled("Dialogue audio preparation cancelled")
                data = {"identity": identity, "asset_id": asset_id, "duration": duration,
                        "sample_rate": 48000, "channels": 2, "channel_method": description["audio_channel_method"],
                        "audio_url": f"/lab/dialogue-audio/assets/{asset_id}.wav"}
                receipt_temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
                _safe(receipt)
                os.replace(receipt_temporary, receipt)
                return data
            finally:
                # Only these newly generated scratch names belong to this call.
                for path in (temporary, receipt_temporary):
                    _safe(path).unlink(missing_ok=True)
    except Timeout as exc:
        raise DialogueAudioBusy("Dialogue audio is still being prepared; retry shortly") from exc
