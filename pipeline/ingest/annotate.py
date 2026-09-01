"""annotate.py — single-pass structured multimodal annotation for a shot.

For each shot, uploads up to 3 keyframe images and sends a single request to
the configured annotation provider (OpenAI or Gemini). Both providers return
the same structured payload:

  - ``caption``        one-paragraph description for semantic search
  - ``mood``           2-4 mood keywords
  - ``framing``        dominant shot scale (close_up / medium / wide / ...)
  - ``setting``        interior / exterior
  - ``time_of_day``    day / night / dawn_dusk
  - ``people_count``   clearly visible people (20 means 20 or more)
  - ``energy``         static / calm / moderate / kinetic
  - ``camera_motion``  best guess from the ordered stills
  - ``palette``        1-3 dominant color descriptors

Facet values outside their vocabulary are coerced to ``"unknown"`` rather
than failing the (paid) call; only an unusable caption or mood list aborts.

The function filters the supplied dialogue list to lines that overlap the
shot's time range and appends their text to the caption to form the legacy
``searchable_text`` lexical projection. Dedicated semantic indexes embed the
caption, dialogue, OCR, and facets as independent views.

Usage::

    from pipeline.ingest.annotate import annotate_shot

    result = annotate_shot(shot, keyframe_paths, dialogue_lines, config)
    # result == {"caption": str, "mood": list[str], "searchable_text": str,
    #            "framing": str, "setting": str, "time_of_day": str,
    #            "people_count": int | None, "energy": str,
    #            "camera_motion": str, "palette": list[str]}
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal, get_args
from uuid import uuid4

from google import genai
from google.genai import types
import openai
from openai import OpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pipeline.config import Config
from pipeline.ingest.dialogue import DialogueLine
from pipeline.ingest.shots import Shot

# ---------------------------------------------------------------------------
# Prompt and structured schema
# ---------------------------------------------------------------------------

_PROMPT = (
    "You are annotating one film shot, shown as ordered keyframes, for a "
    "cinematography search index.\n"
    "- caption: one concise paragraph for semantic search covering what is "
    "happening, the setting, composition, lighting, and visual mood.\n"
    "- mood: 2-4 specific mood keywords.\n"
    "- framing: the dominant shot scale.\n"
    "- setting: interior or exterior.\n"
    "- time_of_day: apparent time of day in the scene.\n"
    "- people_count: clearly visible people; answer 20 for 20 or more.\n"
    "- energy: how kinetic the shot feels, from static (nothing moves) to "
    "kinetic (fast action).\n"
    "- camera_motion: best guess from framing changes across the ordered "
    "keyframes; use unknown when the stills are inconclusive.\n"
    "- palette: 1-3 dominant color descriptors (e.g. 'neon red', 'teal').\n"
    "- subjects: 1-5 short noun phrases naming the key visible subjects.\n"
    "- on_screen_text: legible text in frame (titles, signs, credits), "
    "verbatim; an empty string when there is none."
)

_PROMPT_SHA256 = hashlib.sha256(_PROMPT.encode("utf-8")).hexdigest()

_OPENAI_CONTENT_FILTER_VARIANT = "content_filter_no_ocr_v1"
_OPENAI_NO_OCR_PROMPT = (
    _PROMPT
    + "\nFor this fallback request, do not quote, transcribe, spell out, or "
    "reproduce any on-screen text. Set on_screen_text to an empty string. "
    "If text is visually present, describe only its generic role (for "
    "example, title card or sign) in caption without stating its content."
)
_OPENAI_NO_OCR_PROMPT_SHA256 = hashlib.sha256(
    _OPENAI_NO_OCR_PROMPT.encode("utf-8")
).hexdigest()

_ANNOTATION_CACHE_SCHEMA_VERSION = 2

# MIME type mapping for common image extensions
_MIME_MAP: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}

class AnnotationError(RuntimeError):
    """Raised when the configured annotation provider cannot produce a result.

    ``retryable`` marks failures worth one immediate retry (transient network
    faults, unusable output), unlike auth/quota/refusal errors.  ``escalate``
    additionally marks output that was truncated or schema-invalid, where the
    retry should raise the output-token budget; a plain network retry keeps
    the normal budget. ``content_filtered`` identifies the one incomplete
    status eligible for the bounded no-transcription fallback.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        escalate: bool = False,
        content_filtered: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.escalate = escalate
        self.content_filtered = content_filtered


class _ShotAnnotation(BaseModel):
    """Schema-constrained provider response for one shot."""

    model_config = ConfigDict(extra="forbid")

    caption: str = Field(description="One concise, searchable shot description.")
    mood: list[str] = Field(description="Two to four specific mood keywords.")
    framing: Literal[
        "extreme_close_up", "close_up", "medium", "wide", "extreme_wide", "unknown"
    ] = Field(description="Dominant shot scale.")
    setting: Literal["interior", "exterior", "unknown"] = Field(
        description="Whether the shot plays in an interior or exterior space."
    )
    time_of_day: Literal["day", "night", "dawn_dusk", "unknown"] = Field(
        description="Apparent time of day in the scene."
    )
    people_count: int = Field(
        description="Clearly visible people; 20 means 20 or more (a crowd)."
    )
    energy: Literal["static", "calm", "moderate", "kinetic", "unknown"] = Field(
        description="How kinetic the shot feels."
    )
    camera_motion: Literal[
        "static", "pan", "tilt", "tracking", "handheld", "zoom", "unknown"
    ] = Field(description="Best camera-movement guess from the ordered stills.")
    palette: list[str] = Field(
        description="One to three dominant color descriptors."
    )
    subjects: list[str] = Field(
        description="One to five short noun phrases naming key visible subjects."
    )
    on_screen_text: str = Field(
        description="Legible in-frame text, verbatim; empty when there is none."
    )


#: Facet vocabularies, derived from the provider schema so the two can never
#: drift; validation coerces anything outside them to "unknown".
FACET_VOCAB: dict[str, frozenset[str]] = {
    facet: frozenset(get_args(_ShotAnnotation.model_fields[facet].annotation))
    for facet in ("framing", "setting", "time_of_day", "energy", "camera_motion")
}


@lru_cache(maxsize=1)
def _get_openai_client() -> OpenAI:
    """Return one reusable OpenAI client for the ingest process."""
    return OpenAI()


@lru_cache(maxsize=1)
def _response_schema_sha256() -> str:
    """Fingerprint the structured schema so shape changes invalidate caches."""
    schema_json = json.dumps(_ShotAnnotation.model_json_schema(), sort_keys=True)
    return hashlib.sha256(schema_json.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def annotate_shot(
    shot: Shot,
    keyframes: list[Path],
    dialogue: list[DialogueLine],
    config: Config,
    *,
    cache_dir: Path | None = None,
) -> dict:
    """Annotate a single shot with a caption, mood keywords, and typed facets.

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
        Pipeline configuration. ``config.models.annotator_provider`` selects
        ``"openai"`` or ``"gemini"`` and ``config.models.annotator`` selects
        that provider's model.
    cache_dir:
        Optional directory for a durable per-shot annotation cache. A cached
        hosted response is reused only when its provider, model, prompt,
        response schema, provider settings, and ordered keyframe contents
        still match. Dialogue is deliberately not cached; ``searchable_text``
        is rebuilt on every call from the current overlapping lines.

    Returns
    -------
    dict
        Caption, mood, searchable_text, and the typed cinematography facets.
    """
    # --- 1. Filter dialogue lines overlapping this shot ---
    shot_dialogue = [
        line
        for line in dialogue
        if line.start < shot.t_end and line.end > shot.t_start
    ]
    dialogue_texts = [line.text for line in shot_dialogue]

    # --- 2. Reuse or request one provider annotation ---
    provider = config.models.annotator_provider.lower()
    if provider not in {"openai", "gemini"}:
        raise AnnotationError(
            f"Unknown annotator provider {config.models.annotator_provider!r}; "
            "expected 'openai' or 'gemini'."
        )

    annotation: dict | None = None
    cache_identity: dict | None = None
    cache_path: Path | None = None
    fallback_cache_identity: dict | None = None
    fallback_cache_path: Path | None = None
    if cache_dir is not None:
        cache_identity = _annotation_cache_identity(keyframes, config, provider)
        profile_id = _annotation_profile_id(cache_identity)
        cache_path = _annotation_cache_path(
            cache_dir,
            shot.shot_id,
            profile_id=profile_id,
        )
        annotation = _read_annotation_cache(cache_path, cache_identity)
        if annotation is None:
            # Migrate a matching pre-profile cache without deleting it.  This
            # preserves paid work while future model/prompt profiles coexist.
            legacy_path = _annotation_cache_path(cache_dir, shot.shot_id)
            annotation = _read_annotation_cache(legacy_path, cache_identity)
            if annotation is not None:
                _write_annotation_cache(
                    cache_path,
                    shot_id=shot.shot_id,
                    identity=cache_identity,
                    annotation=annotation,
                )

        if annotation is None and provider == "openai":
            fallback_cache_identity = _annotation_cache_identity(
                keyframes,
                config,
                provider,
                prompt_sha256=_OPENAI_NO_OCR_PROMPT_SHA256,
                request_variant=_OPENAI_CONTENT_FILTER_VARIANT,
            )
            fallback_profile_id = _annotation_profile_id(fallback_cache_identity)
            fallback_cache_path = _annotation_cache_path(
                cache_dir,
                shot.shot_id,
                profile_id=fallback_profile_id,
            )
            annotation = _read_annotation_cache(
                fallback_cache_path,
                fallback_cache_identity,
            )

    if annotation is None:
        request_variant: str | None = None
        if provider == "openai":
            raw, request_variant = _annotate_openai(keyframes, config)
        else:
            raw = _annotate_gemini(keyframes, config)

        annotation = _validate_annotation(raw, provider)
        if cache_dir is not None:
            if request_variant == _OPENAI_CONTENT_FILTER_VARIANT:
                if fallback_cache_identity is None or fallback_cache_path is None:
                    fallback_cache_identity = _annotation_cache_identity(
                        keyframes,
                        config,
                        provider,
                        prompt_sha256=_OPENAI_NO_OCR_PROMPT_SHA256,
                        request_variant=_OPENAI_CONTENT_FILTER_VARIANT,
                    )
                    fallback_cache_path = _annotation_cache_path(
                        cache_dir,
                        shot.shot_id,
                        profile_id=_annotation_profile_id(fallback_cache_identity),
                    )
                cache_identity = fallback_cache_identity
                cache_path = fallback_cache_path
            assert cache_identity is not None and cache_path is not None
            _write_annotation_cache(
                cache_path,
                shot_id=shot.shot_id,
                identity=cache_identity,
                annotation=annotation,
            )

    # --- 3. Build searchable_text ---
    searchable_text = " ".join(
        part for part in [annotation["caption"], *dialogue_texts] if part
    ).strip()

    return {**annotation, "searchable_text": searchable_text}


def _validate_annotation(raw: dict, provider: str) -> dict:
    """Normalize one provider or cache annotation into the stable contract.

    Caption and mood problems abort (the row would be useless); facet values
    outside their vocabulary degrade to ``"unknown"`` so one drifting enum
    never wastes a paid call.
    """
    caption = str(raw.get("caption") or "").strip()
    mood = [
        keyword.strip()
        for keyword in (raw.get("mood") or [])
        if isinstance(keyword, str) and keyword.strip()
    ]
    if not caption:
        raise AnnotationError(f"{provider} returned an empty annotation caption.")
    if not 2 <= len(mood) <= 4:
        raise AnnotationError(
            f"{provider} returned {len(mood)} mood keywords; expected 2-4."
        )

    annotation: dict = {"caption": caption, "mood": mood}
    for facet, vocabulary in FACET_VOCAB.items():
        value = str(raw.get(facet) or "").strip().lower()
        annotation[facet] = value if value in vocabulary else "unknown"

    try:
        people_count = int(raw["people_count"])
    except (KeyError, TypeError, ValueError):
        people_count = None
    annotation["people_count"] = (
        min(max(people_count, 0), 99) if people_count is not None else None
    )

    palette = [
        color.strip()
        for color in (raw.get("palette") or [])
        if isinstance(color, str) and color.strip()
    ]
    annotation["palette"] = palette[:3]

    subjects = [
        subject.strip()
        for subject in (raw.get("subjects") or [])
        if isinstance(subject, str) and subject.strip()
    ]
    annotation["subjects"] = subjects[:5]
    annotation["on_screen_text"] = str(raw.get("on_screen_text") or "").strip()[:500]
    return annotation


def _annotation_cache_path(
    cache_dir: Path,
    shot_id: str,
    *,
    profile_id: str | None = None,
) -> Path:
    """Return a cache path while rejecting unsafe shot/profile identifiers."""
    if (
        not shot_id
        or Path(shot_id).name != shot_id
        or "/" in shot_id
        or "\\" in shot_id
    ):
        raise AnnotationError(f"Unsafe shot ID for annotation cache: {shot_id!r}")
    if profile_id is None:
        return cache_dir / f"{shot_id}.json"
    if not re.fullmatch(r"[a-f0-9]{20}", profile_id):
        raise AnnotationError(
            f"Unsafe annotation profile ID for cache: {profile_id!r}"
        )
    return cache_dir / profile_id / f"{shot_id}.json"


def _annotation_profile_id(identity: dict) -> str:
    """Hash producer/prompt/schema settings, excluding shot-specific frames."""
    profile = {
        key: value
        for key, value in identity.items()
        if key != "keyframes"
    }
    payload = json.dumps(
        profile,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _annotation_cache_identity(
    keyframes: list[Path],
    config: Config,
    provider: str,
    *,
    prompt_sha256: str = _PROMPT_SHA256,
    request_variant: str | None = None,
) -> dict:
    """Describe every input that can change the hosted visual annotation."""
    settings: dict[str, str] = {}
    if provider == "openai":
        settings = {
            "image_detail": config.models.annotator_image_detail,
            "reasoning_effort": config.models.annotator_reasoning_effort,
        }

    frames = []
    for path in keyframes[:3]:
        frames.append(
            {
                "name": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )

    identity = {
        "provider": provider,
        "model": config.models.annotator,
        "prompt_sha256": prompt_sha256,
        "response_schema_sha256": _response_schema_sha256(),
        "settings": settings,
        "keyframes": frames,
    }
    if request_variant is not None:
        identity["request_variant"] = request_variant
    return identity


def _read_annotation_cache(
    path: Path,
    expected_identity: dict,
) -> dict | None:
    """Return a valid matching cached annotation, otherwise a cache miss."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError) as exc:
        raise AnnotationError(
            f"Cannot read annotation cache {path}; refusing a hosted retry."
        ) from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AnnotationError(
            f"Annotation cache {path} is corrupt; refusing a hosted retry."
        ) from exc

    if not isinstance(payload, dict):
        raise AnnotationError(
            f"Annotation cache {path} has invalid data; "
            "refusing a hosted retry."
        )
    if payload.get("schema_version") != _ANNOTATION_CACHE_SCHEMA_VERSION:
        return None
    if payload.get("identity") != expected_identity:
        return None

    annotation = payload.get("annotation")
    if not isinstance(annotation, dict):
        raise AnnotationError(
            f"Annotation cache {path} has invalid output; "
            "refusing a hosted retry."
        )
    try:
        return _validate_annotation(annotation, "cache")
    except AnnotationError as exc:
        raise AnnotationError(
            f"Annotation cache {path} has invalid output; "
            "refusing a hosted retry."
        ) from exc


def _write_annotation_cache(
    path: Path,
    *,
    shot_id: str,
    identity: dict,
    annotation: dict,
) -> None:
    """Atomically persist a completed hosted response for crash-safe resume."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": _ANNOTATION_CACHE_SCHEMA_VERSION,
        "shot_id": shot_id,
        "identity": identity,
        "annotation": annotation,
    }
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _annotate_gemini(
    keyframes: list[Path],
    config: Config,
) -> dict:
    """Return one raw structured annotation dict from the Gemini model."""
    parts: list[types.Part] = []
    for kf_path in keyframes[:3]:
        suffix = kf_path.suffix.lower()
        mime = _MIME_MAP.get(suffix, "image/jpeg")
        img_bytes = kf_path.read_bytes()
        parts.append(types.Part.from_bytes(data=img_bytes, mime_type=mime))

    parts.append(types.Part.from_text(text=_PROMPT))

    try:
        api_key = os.environ.get("GEMINI_API_KEY")
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=config.models.annotator,
            contents=parts,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_ShotAnnotation,
            ),
        )
        raw_text = response.text
    except Exception as exc:
        raise AnnotationError(f"Gemini annotation failed: {exc}") from exc

    if not isinstance(raw_text, str) or not raw_text.strip():
        raise AnnotationError("Gemini returned an empty annotation response.")

    try:
        parsed = _ShotAnnotation.model_validate_json(raw_text)
    except ValidationError as exc:
        raise AnnotationError(
            "Gemini returned invalid structured annotation output."
        ) from exc
    return parsed.model_dump()


# The first attempt's output budget covers ordinary shots; the retry budget
# exists for shots that are mostly text (credit rolls transcribe hundreds of
# names into ``on_screen_text``, truncating the JSON mid-stream).  Neither cap
# is part of the annotation cache identity, so tuning them never re-bills
# cached shots — unlike the prompt or response schema, which must stay frozen
# until a deliberate full re-annotation.
_OPENAI_MAX_OUTPUT_TOKENS = 800
_OPENAI_RETRY_MAX_OUTPUT_TOKENS = 3000


def _annotate_openai(
    keyframes: list[Path],
    config: Config,
) -> tuple[dict, str | None]:
    """Return one structured annotation dict, retrying one unusable response."""
    try:
        return _annotate_openai_once(keyframes, config), None
    except AnnotationError as exc:
        if exc.content_filtered:
            fallback = _annotate_openai_once(
                keyframes,
                config,
                prompt=_OPENAI_NO_OCR_PROMPT,
            )
            if fallback["on_screen_text"].strip():
                raise AnnotationError(
                    "OpenAI no-transcription fallback returned on-screen text."
                )
            return (
                fallback,
                _OPENAI_CONTENT_FILTER_VARIANT,
            )
        if not exc.retryable:
            raise
        retry_budget = (
            _OPENAI_RETRY_MAX_OUTPUT_TOKENS
            if exc.escalate
            else _OPENAI_MAX_OUTPUT_TOKENS
        )
        return (
            _annotate_openai_once(
                keyframes,
                config,
                max_output_tokens=retry_budget,
            ),
            None,
        )


def _annotate_openai_once(
    keyframes: list[Path],
    config: Config,
    *,
    max_output_tokens: int = _OPENAI_MAX_OUTPUT_TOKENS,
    prompt: str = _PROMPT,
) -> dict:
    """Return one raw structured annotation dict from the OpenAI Responses API."""
    if not keyframes:
        raise AnnotationError("OpenAI annotation requires at least one keyframe.")

    content: list[dict] = [
        {"type": "input_text", "text": prompt},
    ]

    for kf_path in keyframes[:3]:
        suffix = kf_path.suffix.lower()
        mime = None if suffix == ".bmp" else _MIME_MAP.get(suffix)
        if mime is None:
            raise AnnotationError(
                f"OpenAI does not support keyframe type {suffix or '<none>'!r}."
            )
        encoded = base64.b64encode(kf_path.read_bytes()).decode("ascii")
        content.append(
            {
                "type": "input_image",
                "image_url": f"data:{mime};base64,{encoded}",
                "detail": config.models.annotator_image_detail,
            }
        )

    try:
        response = _get_openai_client().responses.create(
            model=config.models.annotator,
            input=[{"role": "user", "content": content}],
            reasoning={"effort": config.models.annotator_reasoning_effort},
            max_output_tokens=max_output_tokens,
            store=False,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "shot_annotation",
                    "strict": True,
                    "schema": _ShotAnnotation.model_json_schema(),
                }
            },
        )
    except openai.APIStatusError as exc:
        body = exc.body if isinstance(exc.body, dict) else {}
        error_code = body.get("code") or body.get("type")
        code_suffix = f" ({error_code})" if error_code else ""
        raise AnnotationError(
            f"OpenAI HTTP {exc.status_code}{code_suffix}; "
            f"request_id={exc.request_id}"
        ) from exc
    except openai.APIConnectionError as exc:
        raise AnnotationError(
            "OpenAI connection or timeout failure.",
            retryable=True,
        ) from exc
    except Exception as exc:
        raise AnnotationError(f"OpenAI annotation failed: {exc}") from exc

    refusals = [
        part.refusal
        for item in response.output
        if item.type == "message"
        for part in item.content
        if part.type == "refusal"
    ]
    if refusals:
        raise AnnotationError(f"OpenAI refused the annotation: {refusals[0]}")

    if response.status != "completed":
        reason = None
        if response.incomplete_details is not None:
            reason = response.incomplete_details.reason
        if response.error is not None:
            reason = f"{response.error.code}: {response.error.message}"
        content_filtered = reason == "content_filter" and response.error is None
        raise AnnotationError(
            f"OpenAI response status {response.status}: {reason}",
            # Truncation (max_output_tokens) and other incomplete responses
            # are worth one retry at a raised budget; provider-reported
            # errors are not.
            retryable=response.error is None and not content_filtered,
            escalate=response.error is None and not content_filtered,
            content_filtered=content_filtered,
        )

    raw_text = response.output_text
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise AnnotationError(
            "OpenAI completed without annotation output.",
            retryable=True,
            escalate=True,
        )

    try:
        parsed = _ShotAnnotation.model_validate_json(raw_text)
    except ValidationError as exc:
        details = ", ".join(
            f"{error['type']} at "
            f"{'.'.join(str(part) for part in error.get('loc', ())) or 'response'}"
            for error in exc.errors(include_url=False, include_input=False)
        )
        raise AnnotationError(
            "OpenAI returned invalid structured annotation output"
            f" ({details or 'validation failed'}).",
            retryable=True,
            escalate=True,
        ) from exc

    return parsed.model_dump()
