"""Critique: a video model watches the assembled edit with its audio and flags what to change.

The optimizer never sees the result it produces. After assembly and review, a
low-resolution preview is rendered and Gemini (the only hosted model here that
takes video with sound) watches it at 4 frames per second. It returns
timestamped issues, which become constraints for one re-assembly:

- ``weak_shot``, ``repetitive`` and ``off_direction`` ban the shots overlapping
  the flagged time;
- ``too_fast`` and ``too_slow`` scale the pace of the acts overlapping it;
- ``off_beat`` and ``continuity`` are recorded only. Cut timing already follows
  the measured grid, and continuity is scored directly.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import Field

from pipeline.lab.models import LabModel

CRITIQUE_CONTRACT = "harness-critique-v1"
MODEL = "gemini-3.8-flash"
FPS = 4
PACE_STEP = 1.35


class Issue(LabModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    kind: Literal["weak_shot", "repetitive", "off_direction", "too_fast", "too_slow", "off_beat", "continuity"]
    note: str = Field(min_length=1, max_length=300)


class Critique(LabModel):
    summary: str = Field(min_length=1, max_length=600)
    issues: list[Issue] = Field(max_length=16)


PROMPT = (
    "You are a demanding music video editor reviewing a rough cut. Watch it with its audio: feature-film footage "
    "cut to a music passage. The requested direction was: {direction}\n"
    "List up to 16 concrete issues worth fixing, each with start and end seconds in the video and a kind: "
    "weak_shot (a dull, unclear or ugly image), repetitive (a near-repeat of an earlier setup, setting or action), "
    "off_direction (does not fit the direction or the song), too_fast (cutting too busy for the music here), "
    "too_slow (a hold that lets the energy sag), off_beat (a cut that fights the rhythm), continuity (a jarring "
    "transition). Report only real problems; an edit that works can have few or none. Also give a one-line summary."
)


def _schema() -> dict[str, Any]:
    issue = {"type": "OBJECT", "properties": {
        "start": {"type": "NUMBER"}, "end": {"type": "NUMBER"}, "note": {"type": "STRING"},
        "kind": {"type": "STRING", "enum": ["weak_shot", "repetitive", "off_direction", "too_fast", "too_slow",
                                            "off_beat", "continuity"]}},
        "required": ["start", "end", "kind", "note"]}
    return {"type": "OBJECT", "properties": {"summary": {"type": "STRING"},
                                             "issues": {"type": "ARRAY", "items": issue}},
            "required": ["summary", "issues"]}


def watch(path: Path, direction: str, progress: Callable[[str], None]) -> Critique:
    """Upload one render, have the model watch it at 4 fps, and delete the upload."""
    from google.genai import types

    from pipeline.evidence.understanding import _client

    client = _client()
    progress("Watching the rough cut with its music")
    uploaded = client.files.upload(file=str(path), config={"mime_type": "video/mp4"})
    try:
        deadline = time.monotonic() + 300
        while str(getattr(uploaded.state, "name", uploaded.state)) == "PROCESSING":
            if time.monotonic() > deadline:
                raise RuntimeError("the rough cut is still processing after 5 minutes")
            time.sleep(2)
            uploaded = client.files.get(name=uploaded.name)
        response = client.models.generate_content(
            model=MODEL,
            contents=[types.Part(file_data=types.FileData(file_uri=uploaded.uri, mime_type="video/mp4"),
                                 video_metadata=types.VideoMetadata(fps=FPS)),
                      types.Part.from_text(text=PROMPT.format(direction=(direction or "(none)")[:2000]))],
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=_schema(),
                                               temperature=0.2))
        output = json.loads(response.text or "{}")
        output["issues"] = [issue for issue in output.get("issues") or []
                            if isinstance(issue, dict) and issue.get("end", 0) > issue.get("start", 0) >= 0][:16]
        return Critique.model_validate(output)
    finally:
        try:
            client.files.delete(name=uploaded.name)
        except Exception:  # noqa: BLE001 - uploads expire after 48 hours anyway
            pass


def constraints(critique: Critique, placements: list[Any], acts: list[Any], origin: float) -> dict[str, Any]:
    """Banned shots and per-act pace scales from the flagged issues (absolute song times)."""
    banned: set[str] = set()
    scales = [1.0] * len(acts)
    for issue in critique.issues:
        left, right = origin + issue.start, origin + issue.end
        if issue.kind in ("weak_shot", "repetitive", "off_direction"):
            for placement in placements:
                if placement.start < right and placement.end > left and not placement.fixed:
                    banned.add(placement.candidate.unit_id)
        elif issue.kind in ("too_fast", "too_slow"):
            for index, act in enumerate(acts):
                if act.start < right and act.end > left:
                    scales[index] = PACE_STEP if issue.kind == "too_fast" else 1 / PACE_STEP
    return {"banned": sorted(banned), "pace_scales": scales}
