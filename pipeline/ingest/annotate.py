"""annotate.py — Phase 1 naive single-pass Gemini caption for a shot.

For each shot, uploads up to 3 keyframe images inline as base64 and sends a
single Gemini request that returns:
  - A one-paragraph description of the shot for semantic search.
  - A ``Mood:`` line listing 2-4 comma-separated mood keywords.

The function filters the supplied dialogue list to lines that overlap the
shot's time range and appends their text to the caption to form
``searchable_text`` (used later for text embedding).

Usage::

    from pipeline.ingest.annotate import annotate_shot

    result = annotate_shot(shot, keyframe_paths, dialogue_lines, config)
    # result == {"caption": str, "mood": list[str], "searchable_text": str}
"""

from __future__ import annotations

import os
from pathlib import Path

from google import genai
from google.genai import types

from pipeline.config import Config
from pipeline.ingest.dialogue import DialogueLine
from pipeline.ingest.shots import Shot

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_PROMPT = (
    "Describe this film shot in one paragraph for semantic search. "
    "Include the visual mood, composition, setting, and what is happening. "
    "Then list 2-4 mood keywords on a separate line prefixed with 'Mood:'"
)

# MIME type mapping for common image extensions
_MIME_MAP: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def annotate_shot(
    shot: Shot,
    keyframes: list[Path],
    dialogue: list[DialogueLine],
    config: Config,
) -> dict:
    """Annotate a single shot with a Gemini-generated caption and mood keywords.

    Parameters
    ----------
    shot:
        The shot to annotate; ``t_start`` and ``t_end`` are used to filter
        dialogue lines.
    keyframes:
        Paths to keyframe image files (up to 3 are sent; extras are ignored).
    dialogue:
        All dialogue lines for the film.  Lines whose time range overlaps the
        shot are extracted and appended to ``searchable_text``.
    config:
        Pipeline configuration.  ``config.models.annotator`` selects the
        Gemini model (e.g. ``"gemini-2.0-flash"``).

    Returns
    -------
    dict
        ``{"caption": str, "mood": list[str], "searchable_text": str}``
    """
    # --- 1. Filter dialogue lines overlapping this shot ---
    shot_dialogue = [
        line
        for line in dialogue
        if line.start < shot.t_end and line.end > shot.t_start
    ]
    dialogue_texts = [line.text for line in shot_dialogue]

    # --- 2. Build content parts: up to 3 keyframe images + text prompt ---
    parts: list[types.Part] = []
    for kf_path in keyframes[:3]:
        suffix = kf_path.suffix.lower()
        mime = _MIME_MAP.get(suffix, "image/jpeg")
        img_bytes = kf_path.read_bytes()
        parts.append(types.Part.from_bytes(data=img_bytes, mime_type=mime))

    parts.append(types.Part.from_text(text=_PROMPT))

    # --- 3. Call Gemini ---
    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=config.models.annotator,
            contents=parts,
        )
        raw_text: str = response.text
    except Exception:
        return {"caption": "", "mood": [], "searchable_text": ""}

    # --- 4. Parse the response ---
    caption, mood = _parse_response(raw_text)

    # --- 5. Build searchable_text ---
    if dialogue_texts:
        searchable_text = f"{caption} {' '.join(dialogue_texts)}"
    else:
        searchable_text = caption

    return {
        "caption": caption,
        "mood": mood,
        "searchable_text": searchable_text,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_response(text: str) -> tuple[str, list[str]]:
    """Parse a Gemini response into a ``(caption, mood_keywords)`` tuple.

    The response format expected is::

        <paragraph describing the shot>
        Mood: keyword1, keyword2, keyword3

    The ``Mood:`` line may appear anywhere; all other lines form the caption.

    Parameters
    ----------
    text:
        Raw text returned by Gemini.  Empty or ``None``-ish values return
        an empty caption and empty mood list.

    Returns
    -------
    tuple[str, list[str]]
        ``(caption, mood_keywords)`` where ``caption`` is the joined non-Mood
        lines and ``mood_keywords`` is the parsed list (empty if no Mood: line).
    """
    if not text:
        return ("", [])

    lines = text.strip().splitlines()
    mood_keywords: list[str] = []
    caption_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Mood:"):
            mood_part = stripped[len("Mood:"):].strip()
            mood_keywords = [kw.strip() for kw in mood_part.split(",") if kw.strip()]
        elif stripped:
            caption_lines.append(stripped)

    caption = " ".join(caption_lines).strip()
    return caption, mood_keywords
