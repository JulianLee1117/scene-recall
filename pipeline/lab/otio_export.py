"""OpenTimelineIO export of a saved edit, for finishing in DaVinci Resolve on the original media.

Nothing is re-encoded: every clip references its source film file, the song
and any dialogue clips, with frame-exact durations matching the Lab renderer's
cumulative quantization. Times use the timeline rate (24 fps), so films at
23.976, 24 or 25 fps keep exact edit lengths; the editor conforms each source.
Crops and mix levels are carried as clip metadata because OTIO has no
standard transform or gain schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pipeline.lab.media import quantized_frame_counts, resolve_film

OTIO_CONTRACT = "scene-recall-otio-v1"


def _time(value: float, rate: float) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "RationalTime.1", "rate": float(rate), "value": float(value)}


def _range(start: float, duration: float, rate: float) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "TimeRange.1", "start_time": _time(start, rate), "duration": _time(duration, rate)}


def _reference(path: str) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "ExternalReference.1", "target_url": Path(path).resolve().as_uri(),
            "available_range": None, "metadata": {}, "name": Path(path).name}


def _clip(name: str, path: str, start_seconds: float, frames: int, rate: float, metadata: dict[str, Any]) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "Clip.1", "name": name[:120], "source_range": _range(start_seconds * rate, frames, rate),
            "media_reference": _reference(path), "effects": [], "markers": [], "metadata": {"scene_recall": metadata}}


def _gap(frames: int, rate: float) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "Gap.1", "name": "", "source_range": _range(0, frames, rate), "effects": [], "markers": [],
            "metadata": {}}


def _track(name: str, kind: str, children: list[dict[str, Any]]) -> dict[str, Any]:
    return {"OTIO_SCHEMA": "Track.1", "name": name, "kind": kind, "children": children, "source_range": None,
            "effects": [], "markers": [], "metadata": {}}


def export_timeline(document: dict[str, Any], db: Any, store: Any, *, name: str) -> dict[str, Any]:
    """The edit as an OTIO timeline (JSON-serializable)."""
    timeline = document.get("music_timeline")
    if not timeline or not document.get("track"):
        raise ValueError("Export needs a music timeline")
    rate = float(document["fps"])
    slots = timeline["slots"]
    counts = quantized_frame_counts([slot["end"] - slot["start"] for slot in slots], rate)
    clips = {clip["id"]: clip for clip in document["clips"]}
    films: dict[str, dict[str, Any]] = {}

    def film(film_id: str) -> dict[str, Any]:
        if film_id not in films:
            films[film_id] = resolve_film(db, film_id)
        return films[film_id]

    video = []
    for slot, frames in zip(slots, counts):
        clip = clips.get(slot.get("clip_id") or "")
        if clip is None:
            video.append(_gap(frames, rate))
            continue
        source = film(clip["film_id"])
        title, label = str(source.get("title") or clip["film_id"]), str(clip.get("title") or "").strip()
        video.append(_clip(title if not label or label == title else f"{title}: {label}",
                           str(source["path"]), clip["source_start"], frames, rate,
                           {"clip_id": clip["id"], "unit_id": clip.get("unit_id"), "crop": clip.get("crop"),
                            "reason": slot.get("reason")}))
    total = sum(counts)
    passage = document["passage"]
    track = store.get_track(document["track"]["id"])
    music = [_clip(document["track"].get("name") or "Music", track["path"], passage["start"], total, rate,
                   {"gain_db": document.get("music_gain_db", 0), "fade_in_seconds": document.get("audio_fade_in_seconds", 0),
                    "fade_out_seconds": document.get("audio_fade_out_seconds", 0)})]
    dialogue, cursor = [], 0
    for item in sorted(document.get("dialogue_clips") or [], key=lambda row: row["start"]):
        first = round((item["start"] - passage["start"]) * rate)
        frames = max(1, round((item["source_end"] - item["source_start"]) * rate))
        if first < cursor:
            continue                        # overlapping dialogue stays on the Lab mix only
        if first > cursor:
            dialogue.append(_gap(first - cursor, rate))
        source = film(item["film_id"])
        dialogue.append(_clip(item.get("text") or item.get("title") or "Dialogue", str(source["path"]), item["source_start"],
                              frames, rate, {"gain_db": item.get("gain_db", 0), "music_duck_db": item.get("music_duck_db"),
                                             "audio_mode": item.get("source_audio_mode")}))
        cursor = first + frames
    tracks = [_track("V1", "Video", video), _track("A1 Music", "Audio", music)]
    if dialogue:
        tracks.append(_track("A2 Dialogue", "Audio", dialogue))
    return {"OTIO_SCHEMA": "Timeline.1", "name": name[:200], "global_start_time": _time(0, rate),
            "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "children": tracks, "source_range": None,
                       "effects": [], "markers": [], "metadata": {}},
            "metadata": {"scene_recall": {"contract": OTIO_CONTRACT, "fps": rate, "frames": total,
                                          "passage": dict(passage), "track_id": document["track"]["id"]}}}
