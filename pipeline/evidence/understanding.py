"""Hosted video understanding: scenes, per-shot story/action records and fame.

Each film is split into chunks of consecutive indexed shots. A low-resolution
proxy of each chunk is rendered with the film-wide shot number burned into the
top-left corner, and the model returns one record per listed shot number plus
scene groupings and iconic moments. Keying output to known shots (instead of
asking for timestamps) is what makes the output alignable: the 2026-09-27
probe returned all 187 shots of a Matrix chunk with every peak time inside its
own shot.

World knowledge is allowed for names and story (ADR-0093); actions must be
described from what the clip shows. Camera labels from this pass are hints;
measured camera motion comes from the local measurement pass.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import json
import os
import re
import statistics
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from pipeline.evidence import store
from pipeline.evidence.library import FilmRef, film_units


PROMPT_VERSION = "shot-keyed-understanding-v1"

def proxy_fps(chunk: "Chunk") -> float:
    """Proxy frame rate: twice the sampling rate, so every sampled frame exists (uploads stay small)."""
    return 2 * chunk.fps

CAMERA_HINTS = ["static", "pan", "tilt", "push_in", "pull_out", "tracking", "handheld", "crane", "orbit", "zoom", "unclear"]
# USD per 1M tokens (standard tier; batch is half). Checked 2026-09-27; Flash
# prices are introductory until 2026-12-31.
PRICES = {
    "gemini-3.8-flash": {"input": 0.75, "output": 3.75},
    "gemini-3.1-pro-preview": {"input": 2.00, "output": 12.00},
    "gemini-3.5-flash-lite": {"input": 0.30, "output": 2.50},
}
DEFAULTS = {
    "model": "gemini-3.8-flash",
    "max_shots": 160,
    "max_seconds": 600.0,
    "fast_median_shot_s": 3.0,
    "fps_fast": 2.0,
    "fps_normal": 1.0,
    "proxy_height": 240,       # at low media resolution frames are reduced to ~64 tokens; 240p loses nothing
    "proxy_audio_kbps": 32,    # mono; the model resamples audio to 16 kHz
    "thinking": "low",
    "media_resolution": "low",
    "plot_chars": 3500,
}


def producer(model: str = DEFAULTS["model"]) -> store.Producer:
    settings = {key: value for key, value in DEFAULTS.items() if key != "model"}
    settings.update(model=model, prompt_version=PROMPT_VERSION, prompt_sha256=store.digest(_PROMPT_TEMPLATE),
                    schema_sha256=store.digest(response_schema()))
    return store.Producer(kind="understanding", name="gemini-shots", version=1, settings=settings)


# ---------------------------------------------------------------------------
# Chunk planning and proxies
# ---------------------------------------------------------------------------


@dataclass
class Chunk:
    index: int
    shots: list[dict[str, Any]]           # rows with unit_id, t_start, t_end, ordinal

    @property
    def start(self) -> float:
        return float(self.shots[0]["t_start"])

    @property
    def end(self) -> float:
        return float(self.shots[-1]["t_end"])

    @property
    def fps(self) -> float:
        median = statistics.median(float(s["t_end"]) - float(s["t_start"]) for s in self.shots)
        return DEFAULTS["fps_fast"] if median < DEFAULTS["fast_median_shot_s"] else DEFAULTS["fps_normal"]

    def digest(self) -> str:
        return store.digest([[s["unit_id"], round(float(s["t_start"]), 3), round(float(s["t_end"]), 3)] for s in self.shots])


def plan_chunks(units: list[dict[str, Any]], *, max_shots: int = DEFAULTS["max_shots"],
                max_seconds: float = DEFAULTS["max_seconds"]) -> list[Chunk]:
    rows = [{**unit, "ordinal": index} for index, unit in enumerate(units, start=1)]
    chunks: list[Chunk] = []
    current: list[dict[str, Any]] = []
    for row in rows:
        if current and (len(current) >= max_shots or float(row["t_end"]) - float(current[0]["t_start"]) > max_seconds):
            chunks.append(Chunk(len(chunks), current))
            current = []
        current.append(row)
    if current:
        chunks.append(Chunk(len(chunks), current))
    return chunks


def clock(seconds: float) -> str:
    minutes, rest = divmod(max(0.0, seconds), 60)
    return f"{int(minutes):02d}:{rest:04.1f}"


def _srt_time(seconds: float) -> str:
    millis = int(round(max(0.0, seconds) * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _proxy_size(path: Path) -> tuple[int, int]:
    """Proxy width and height at ``proxy_height``, honouring non-square pixels."""
    import av
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        display_width = stream.codec_context.width * float(stream.sample_aspect_ratio or 1)
        height = stream.codec_context.height
    target = DEFAULTS["proxy_height"]
    return max(2, int(round(target * display_width / height / 2)) * 2), target


def render_proxy(film: FilmRef, chunk: Chunk, directory: Path) -> Path:
    """Low-res H.264 proxy of one chunk with shot numbers burned in.

    Decoding, frame-rate reduction and scaling run on the GPU (NVDEC) and only
    the small frames come back for the subtitle burn-in; codecs NVDEC cannot
    decode fall back to the same filters on the CPU. Both paths produce the
    same frames, size and encoding. Non-reference frames are never decoded: at
    2-6 fps the sampled instant moves by at most a source frame, and decoding
    (shared with the measurement pass) gets about 1.8x cheaper.
    """
    labels = directory / f"chunk-{chunk.index:03d}.srt"
    lines = []
    for number, shot in enumerate(chunk.shots, start=1):
        start = float(shot["t_start"]) - chunk.start
        end = float(shot["t_end"]) - chunk.start - 0.01
        lines.append(f"{number}\n{_srt_time(start)} --> {_srt_time(max(start + 0.02, end))}\n{{\\an7}}S{shot['ordinal']}\n")
    labels.write_text("\n".join(lines), encoding="utf-8")
    output = directory / f"chunk-{chunk.index:03d}.mp4"
    escaped = labels.as_posix().replace(":", "\\:")
    style = "Fontsize=20,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,MarginL=6,MarginV=6"
    burn = f"subtitles='{escaped}':force_style='{style}'"
    fps = proxy_fps(chunk)
    width, height = _proxy_size(film.path)
    window = ["-skip_frame:v", "noref", "-ss", f"{chunk.start:.3f}", "-to", f"{chunk.end:.3f}", "-i", str(film.path),
              "-map", "0:v:0", "-map", "0:a:0?"]
    encode = ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "32", "-b:v", "0",
              "-c:a", "aac", "-b:a", f"{DEFAULTS['proxy_audio_kbps']}k", "-ac", "1", str(output)]
    attempts = [
        ["ffmpeg", "-y", "-nostdin", "-v", "error", "-hwaccel", "cuda", "-hwaccel_output_format", "cuda", *window,
         "-vf", f"fps={fps},scale_cuda={width}:{height}:format=nv12,hwdownload,format=nv12,format=yuv420p,{burn}", *encode],
        ["ffmpeg", "-y", "-nostdin", "-v", "error", *window,
         "-vf", f"fps={fps},scale={width}:{height},setsar=1,{burn}", *encode],
        ["ffmpeg", "-y", "-nostdin", "-v", "error", *window,
         "-vf", f"fps={fps},scale={width}:{height},setsar=1,{burn}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "32", *encode[encode.index("-c:a"):]],
    ]
    completed = None
    for command in attempts:
        completed = subprocess.run(command, capture_output=True, check=False)
        if completed.returncode == 0 and output.is_file():
            return output
    raise RuntimeError(f"proxy render failed: {completed.stderr.decode('utf-8', 'replace')[-400:]}")


# ---------------------------------------------------------------------------
# Prompt and schema
# ---------------------------------------------------------------------------


_PROMPT_TEMPLATE = """You are indexing a feature film for a personal footage search and music-video editing tool.

FILM: {film}
{context}
CLIP: film time {clip_start}-{clip_end} (part {part} of {parts}). Every frame shows the current shot number (S<n>) in the top-left corner. Shot table with clip-relative times:
{shot_table}

TIMED DIALOGUE (clip-relative, from subtitles; may be imperfect):
{dialogue}

Return JSON with:
- scenes: consecutive groups of shots that form one dramatic scene (same place, time and action), covering every listed shot exactly once and in order. continues_previous=true only for the first scene when it continues the scene that ended the previous part. title <=8 words; summary <=40 words (what happens and why it matters in the story); setting <=8 words; characters (names); story_context <=25 words (where this sits in the plot); tone <=4 words.
- shots: exactly one entry per listed shot number: characters visible (the film's character names; [] if none or unknown); action <=20 words describing what visibly happens; peak = clip time MM:SS.s of the most important instant inside this shot; emotion <=4 words; line = the most notable line spoken during this shot, verbatim if clear, else ""; speaker ("" if none); audio <=6 words of notable sound (music, silence, gunfire) or ""; fame 0-3 = how widely recognized this exact moment or image is in film culture (3 iconic, 2 memorable key moment, 1 notable, 0 ordinary coverage); craft 0-3 = visual and editorial strength regardless of fame (composition, light, motion, performance; 0 weak, transitional or unusable); cut_inside = true only if the numbered shot visibly contains a cut to a different camera setup; camera = best guess of the camera's own movement.
- iconic: this clip's most famous moments (shot number, description <=15 words, why) or [] if none.
Use your knowledge of this film for character names and story context, but describe only what the clip shows for actions. Keep every field short."""


def response_schema() -> dict[str, Any]:
    string = {"type": "string"}
    return {
        "type": "object",
        "properties": {
            "scenes": {"type": "array", "items": {"type": "object", "properties": {
                "first_shot": {"type": "integer"}, "last_shot": {"type": "integer"},
                "continues_previous": {"type": "boolean"}, "title": string, "summary": string, "setting": string,
                "characters": {"type": "array", "items": string}, "story_context": string, "tone": string},
                "required": ["first_shot", "last_shot", "continues_previous", "title", "summary", "setting",
                             "characters", "story_context", "tone"]}},
            "shots": {"type": "array", "items": {"type": "object", "properties": {
                "shot": {"type": "integer"}, "characters": {"type": "array", "items": string}, "action": string,
                "peak": string, "emotion": string, "line": string, "speaker": string, "audio": string,
                "fame": {"type": "integer", "minimum": 0, "maximum": 3},
                "craft": {"type": "integer", "minimum": 0, "maximum": 3},
                "cut_inside": {"type": "boolean"}, "camera": {"type": "string", "enum": CAMERA_HINTS}},
                "required": ["shot", "characters", "action", "peak", "emotion", "line", "speaker", "audio",
                             "fame", "craft", "cut_inside", "camera"]}},
            "iconic": {"type": "array", "items": {"type": "object", "properties": {
                "shot": {"type": "integer"}, "description": string, "why": string},
                "required": ["shot", "description", "why"]}},
        },
        "required": ["scenes", "shots", "iconic"],
    }


def film_context(metadata: dict[str, Any] | None, *, include_plot: bool = True) -> str:
    if not metadata:
        return ""
    wiki = metadata.get("wikidata") or {}
    parts = []
    if wiki.get("directors"):
        parts.append("DIRECTED BY: " + ", ".join(wiki["directors"][:3]))
    cast = []
    for row in (wiki.get("cast") or [])[:24]:
        characters = [c for c in row.get("characters") or [] if c]
        cast.append(f"{row['actor']} as {' / '.join(characters)}" if characters else row["actor"])
    if cast:
        parts.append("CAST: " + "; ".join(cast))
    plot = ((metadata.get("wikipedia") or {}).get("plot") or "") if include_plot else ""
    if plot:
        parts.append("PLOT SUMMARY (Wikipedia; context only):\n" + plot[:DEFAULTS["plot_chars"]])
    return "\n".join(parts) + "\n"


def build_prompt(film: FilmRef, chunk: Chunk, parts: int, context: str, dialogue: list[dict[str, Any]]) -> str:
    table = "\n".join(f"S{s['ordinal']}: {clock(float(s['t_start']) - chunk.start)}-{clock(float(s['t_end']) - chunk.start)}"
                      for s in chunk.shots)
    lines = [f"[{clock(float(d['start']) - chunk.start)}-{clock(float(d['end']) - chunk.start)}] {d['text']}"
             for d in dialogue if float(d["end"]) > chunk.start and float(d["start"]) < chunk.end]
    return _PROMPT_TEMPLATE.format(film=film.title, context=context, clip_start=clock(chunk.start),
                                   clip_end=clock(chunk.end), part=chunk.index + 1, parts=parts, shot_table=table,
                                   dialogue="\n".join(lines) if lines else "(none)")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _seconds(value: str) -> float | None:
    match = re.fullmatch(r"\s*(\d+):(\d{1,2}(?:\.\d+)?)\s*", str(value or ""))
    return int(match.group(1)) * 60 + float(match.group(2)) if match else None


def _names(values: Any, limit: int = 8) -> list[str]:
    out = []
    for value in values or []:
        text = str(value).strip()
        if text and text.casefold() not in {v.casefold() for v in out}:
            out.append(text[:60])
    return out[:limit]


def validate_chunk(chunk: Chunk, output: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Normalize one chunk response into unit-keyed records; returns issues too."""
    issues: list[str] = []
    by_ordinal = {s["ordinal"]: s for s in chunk.shots}
    shots: dict[str, dict[str, Any]] = {}
    for row in output.get("shots") or []:
        shot = by_ordinal.get(row.get("shot"))
        if shot is None or shot["unit_id"] in shots:
            continue
        peak = _seconds(row.get("peak"))
        peak_time = None
        if peak is not None:
            candidate = chunk.start + peak
            if float(shot["t_start"]) - 0.5 <= candidate <= float(shot["t_end"]) + 0.5:
                peak_time = round(min(max(candidate, float(shot["t_start"])), float(shot["t_end"])), 3)
        shots[shot["unit_id"]] = {
            "characters": _names(row.get("characters")),
            "action": str(row.get("action") or "").strip()[:300],
            "peak_time": peak_time,
            "emotion": str(row.get("emotion") or "").strip()[:60],
            "line": str(row.get("line") or "").strip()[:300],
            "speaker": str(row.get("speaker") or "").strip()[:60],
            "audio": str(row.get("audio") or "").strip()[:80],
            "fame": min(3, max(0, int(row.get("fame") or 0))),
            "craft": min(3, max(0, int(row.get("craft") or 0))),
            "cut_inside": bool(row.get("cut_inside")),
            "camera_hint": row.get("camera") if row.get("camera") in CAMERA_HINTS else "unclear",
        }
    missing = [s["ordinal"] for s in chunk.shots if s["unit_id"] not in shots]
    if missing:
        issues.append(f"chunk {chunk.index}: {len(missing)} shots missing ({missing[:5]}...)")
    ordinals = [s["ordinal"] for s in chunk.shots]
    first, last = ordinals[0], ordinals[-1]
    scenes = []
    for row in sorted(output.get("scenes") or [], key=lambda r: int(r.get("first_shot") or 0)):
        start = max(first, int(row.get("first_shot") or first))
        end = min(last, int(row.get("last_shot") or start))
        if scenes and start <= scenes[-1]["last_shot"]:
            start = scenes[-1]["last_shot"] + 1
        if end < start:
            continue
        if scenes and start > scenes[-1]["last_shot"] + 1:
            scenes[-1]["last_shot"] = start - 1  # close gaps by extending the previous scene
        elif not scenes and start > first:
            start = first
        scenes.append({
            "first_shot": start, "last_shot": end, "continues_previous": bool(row.get("continues_previous")) and not scenes,
            "title": str(row.get("title") or "").strip()[:120], "summary": str(row.get("summary") or "").strip()[:500],
            "setting": str(row.get("setting") or "").strip()[:120], "characters": _names(row.get("characters"), 12),
            "story_context": str(row.get("story_context") or "").strip()[:300], "tone": str(row.get("tone") or "").strip()[:60],
        })
    if not scenes:
        issues.append(f"chunk {chunk.index}: no scenes")
        scenes = [{"first_shot": first, "last_shot": last, "continues_previous": False, "title": "", "summary": "",
                   "setting": "", "characters": [], "story_context": "", "tone": ""}]
    scenes[-1]["last_shot"] = last
    iconic = []
    for row in output.get("iconic") or []:
        shot = by_ordinal.get(row.get("shot"))
        if shot is not None:
            iconic.append({"unit_id": shot["unit_id"], "description": str(row.get("description") or "")[:200],
                           "why": str(row.get("why") or "")[:300]})
    return {"shots": shots, "scenes": scenes, "iconic": iconic}, issues


def merge_chunks(units: list[dict[str, Any]], receipts: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine validated chunk outputs into film-level scenes and shot records."""
    ordinal_unit = {index: unit for index, unit in enumerate(units, start=1)}
    scenes: list[dict[str, Any]] = []
    shots: dict[str, dict[str, Any]] = {}
    iconic: list[dict[str, Any]] = []
    gap = False
    for receipt in sorted(receipts, key=lambda r: r["chunk"]):
        if receipt.get("refused"):
            gap = True
            continue
        result = receipt["result"]
        for index, scene in enumerate(result["scenes"]):
            if scene["continues_previous"] and scenes and not (gap and index == 0):
                previous = scenes[-1]
                previous["last_shot"] = scene["last_shot"]
                previous["characters"] = _names(previous["characters"] + scene["characters"], 12)
                continue
            scenes.append(dict(scene))
        shots.update(result["shots"])
        iconic.extend(result["iconic"])
        gap = False
    for index, scene in enumerate(scenes):
        first, last = ordinal_unit[scene["first_shot"]], ordinal_unit[scene["last_shot"]]
        scene.update(index=index, first_unit=first["unit_id"], last_unit=last["unit_id"],
                     t_start=float(first["t_start"]), t_end=float(last["t_end"]))
        for ordinal in range(scene["first_shot"], scene["last_shot"] + 1):
            record = shots.get(ordinal_unit[ordinal]["unit_id"])
            if record is not None:
                record["scene"] = index
    return {"scenes": scenes, "shots": shots, "iconic": iconic}


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


def _client():
    from google import genai
    from google.genai import types
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    from pipeline.evidence.http import ipv4_transport
    return genai.Client(api_key=api_key, http_options=types.HttpOptions(
        timeout=900_000, client_args={"transport": ipv4_transport()}))


def _openapi_schema(node: dict[str, Any]) -> Any:
    """The same JSON schema as a ``types.Schema`` (the batch backend enforces only this form)."""
    from google.genai import types
    fields: dict[str, Any] = {"type": node["type"].upper()}
    if "properties" in node:
        fields["properties"] = {name: _openapi_schema(child) for name, child in node["properties"].items()}
        fields["property_ordering"] = list(node["properties"])
        fields["required"] = node.get("required")
    if "items" in node:
        fields["items"] = _openapi_schema(node["items"])
    if "enum" in node:
        fields.update(enum=node["enum"], format="enum")
    for bound in ("minimum", "maximum"):
        if bound in node:
            fields[bound] = node[bound]
    return types.Schema(**fields)


def _generation_config(*, batch: bool = False) -> Any:
    from google.genai import types
    schema = {"response_schema": _openapi_schema(response_schema())} if batch else {"response_json_schema": response_schema()}
    return types.GenerateContentConfig(
        media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
        response_mime_type="application/json",
        thinking_config=types.ThinkingConfig(thinking_level=DEFAULTS["thinking"]),
        **schema,
    )


def _contents(video: Any, fps: float, prompt: str) -> list[Any]:
    """User turn with the clip (a local proxy inlined, or an uploaded File) and the prompt."""
    from google.genai import types
    if isinstance(video, Path):
        clip = types.Part(inline_data=types.Blob(data=video.read_bytes(), mime_type="video/mp4"),
                          video_metadata=types.VideoMetadata(fps=fps))
    else:
        clip = types.Part(file_data=types.FileData(file_uri=video.uri, mime_type="video/mp4"),
                          video_metadata=types.VideoMetadata(fps=fps))
    return [types.Content(role="user", parts=[clip, types.Part.from_text(text=prompt)])]


def response_payload(response: Any, started: float | None = None) -> dict[str, Any]:
    """Text, usage and stop reasons of one GenerateContentResponse (standard or batch)."""
    usage = response.usage_metadata.model_dump(mode="json") if response.usage_metadata else {}
    candidate = (response.candidates or [None])[0]
    finish = str(getattr(candidate, "finish_reason", "") or "")
    feedback = getattr(response, "prompt_feedback", None)
    block = str(getattr(feedback, "block_reason", "") or "") if feedback is not None else ""
    text = (response.text or "") if candidate is not None else ""
    return {"text": text, "usage": usage, "finish_reason": finish, "block_reason": block,
            "elapsed_s": round(time.perf_counter() - started, 1) if started is not None else None,
            "model_version": getattr(response, "model_version", None)}


def call_model(client: Any, model: str, video: Any, fps: float, prompt: str) -> dict[str, Any]:
    started = time.perf_counter()
    response = client.models.generate_content(model=model, contents=_contents(video, fps, prompt),
                                              config=_generation_config())
    return response_payload(response, started)


def cost_usd(model: str, usage: dict[str, Any], *, batch: bool = False) -> float:
    price = PRICES.get(model)
    if not price or not usage:
        return 0.0
    tokens_in = usage.get("prompt_token_count") or 0
    tokens_out = (usage.get("candidates_token_count") or 0) + (usage.get("thoughts_token_count") or 0)
    total = (tokens_in * price["input"] + tokens_out * price["output"]) / 1_000_000
    return round(total * (0.5 if batch else 1.0), 6)


class Blocked(RuntimeError):
    """The prompt itself was refused (for example PROHIBITED_CONTENT); retrying unchanged cannot help."""


def _refused_finish(finish_reason: Any) -> bool:
    return any(marker in str(finish_reason or "") for marker in ("SAFETY", "PROHIBITED", "BLOCKLIST", "SPII"))


class _BudgetStop(Exception):
    pass


_TRANSIENT_MARKERS = ("429", "500", "502", "503", "504", "RESOURCE_EXHAUSTED", "UNAVAILABLE", "DEADLINE",
                      "timed out", "Timeout", "ConnectError", "RemoteProtocolError", "ReadError", "WriteError")


def _transient(exc: BaseException) -> bool:
    """Server-side or network failures that a later attempt can succeed at."""
    message = f"{type(exc).__name__}: {exc}"
    return any(marker in message for marker in _TRANSIENT_MARKERS)


def _call_with_retry(client: Any, model: str, video: Any, fps: float, prompt: str, attempts: int = 3) -> dict[str, Any]:
    delay = 20.0
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = call_model(client, model, video, fps, prompt)
            if response["text"]:
                return response
            if response["block_reason"]:
                raise Blocked(response["block_reason"])
            error = RuntimeError(f"empty response (finish={response['finish_reason']})")
            if _refused_finish(response["finish_reason"]):
                raise Blocked(response["finish_reason"])
        except Blocked:
            raise
        except Exception as exc:  # noqa: BLE001
            if not _transient(exc) or attempt == attempts - 1:
                raise
            error = exc
        time.sleep(delay)
        delay *= 2
    raise error or RuntimeError("model call failed")


# ---------------------------------------------------------------------------
# Plans, receipts and film artifacts (shared by both transports)
# ---------------------------------------------------------------------------


def receipt_path(config: Any, film_id: str, prod: store.Producer, chunk: Chunk) -> Path:
    return (Path(config.paths.assets_dir) / film_id / "evidence" / prod.kind / prod.profile_id
            / f"chunk-{chunk.index:03d}-{chunk.digest()[:10]}.json")


@dataclass
class Budget:
    limit_usd: float
    spent: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def admit(self, estimate: float) -> bool:
        with self.lock:
            if self.spent + estimate > self.limit_usd:
                return False
            self.spent += estimate
            return True

    def settle(self, estimate: float, actual: float) -> None:
        with self.lock:
            self.spent += actual - estimate


def estimate_chunk_usd(model: str, chunk: Chunk, *, batch: bool = False) -> float:
    seconds = chunk.end - chunk.start
    tokens_in = seconds * (66 * chunk.fps + 32) + 6000
    tokens_out = 130 * len(chunk.shots) + 800
    return cost_usd(model, {"prompt_token_count": tokens_in, "candidates_token_count": tokens_out}, batch=batch)


def shots_digest(units: list[dict[str, Any]]) -> str:
    return store.digest([[u["unit_id"], round(float(u["t_start"]), 3), round(float(u["t_end"]), 3)] for u in units])


@dataclass
class FilmPlan:
    film: FilmRef
    units: list[dict[str, Any]]
    chunks: list[Chunk]
    inputs: dict[str, str]
    dialogue: list[dict[str, Any]]
    contexts: tuple[str, str]            # with and without the plot synopsis

    def prompt(self, chunk: Chunk, variant: str) -> str:
        return build_prompt(self.film, chunk, len(self.chunks), self.contexts[0 if variant == "full" else 1], self.dialogue)


def plan_films(config: Any, db: Any, films: list[FilmRef], prod: store.Producer, *,
               force: bool) -> tuple[list[FilmPlan], int]:
    """Films still needing an artifact, with their chunks and prompt inputs; also the cached count."""
    from pipeline.evidence import metadata as metadata_module
    from pipeline.evidence.subtitles import current_dialogue

    plans, cached = [], 0
    for film in films:
        units = film_units(db, film.film_id)
        if not units:
            continue
        dialogue = current_dialogue(config, film.film_id)
        meta_doc = store.read_artifact(config.paths.assets_dir, film.film_id, metadata_module.PRODUCER)
        metadata = meta_doc["data"] if meta_doc else None
        inputs = {"shots": shots_digest(units), "dialogue": store.digest(dialogue), "metadata": store.digest(metadata or {})}
        if not force and store.read_artifact(config.paths.assets_dir, film.film_id, prod, inputs={"shots": inputs["shots"]}):
            cached += 1
            continue
        plans.append(FilmPlan(film, units, plan_chunks(units), inputs, dialogue,
                              (film_context(metadata), film_context(metadata, include_plot=False))))
    return plans, cached


def pending_chunks(config: Any, plan: FilmPlan, prod: store.Producer, *, force: bool = False) -> list[Chunk]:
    return [chunk for chunk in plan.chunks if force or not receipt_path(config, plan.film.film_id, prod, chunk).is_file()]


def write_receipt(config: Any, plan: FilmPlan, prod: store.Producer, chunk: Chunk, response: dict[str, Any], *,
                  model: str, variant: str, transport: str, proxy: dict[str, Any]) -> dict[str, Any]:
    """Validate one response and persist the chunk receipt (resumable unit of work)."""
    try:
        output = json.loads(response["text"])
    except (json.JSONDecodeError, TypeError):
        output = None
    if not isinstance(output, dict):
        raise RuntimeError(f"unparseable response (finish={response['finish_reason']})")
    result, issues = validate_chunk(chunk, output)
    receipt = {"chunk": chunk.index, "first_ordinal": chunk.shots[0]["ordinal"], "last_ordinal": chunk.shots[-1]["ordinal"],
               "t_start": chunk.start, "t_end": chunk.end, "fps": chunk.fps, "model": model, "context": variant,
               "transport": transport, "proxy": proxy, "model_version": response["model_version"],
               "finish_reason": response["finish_reason"], "usage": response["usage"],
               "cost_usd": cost_usd(model, response["usage"], batch=transport == "batch"),
               "elapsed_s": response["elapsed_s"], "issues": issues, "result": result}
    store.write_json(receipt_path(config, plan.film.film_id, prod, chunk), receipt)
    return receipt


def write_refusal(config: Any, plan: FilmPlan, prod: store.Producer, chunk: Chunk, reason: str, *,
                  model: str, transport: str, proxy: dict[str, Any]) -> dict[str, Any]:
    """Persist a terminal receipt for a chunk the model refuses even without the synopsis.

    Non-configurable filters (PROHIBITED_CONTENT) can refuse a clip for what it
    shows. Resubmitting cannot help, so the chunk is closed with no records:
    its shots get no understanding, the film still completes, and the
    artifact lists the refused chunk so nothing is silently missing.
    """
    receipt = {"chunk": chunk.index, "first_ordinal": chunk.shots[0]["ordinal"], "last_ordinal": chunk.shots[-1]["ordinal"],
               "t_start": chunk.start, "t_end": chunk.end, "fps": chunk.fps, "model": model, "context": "refused",
               "refused": str(reason), "transport": transport, "proxy": proxy, "model_version": None,
               "finish_reason": str(reason), "usage": {}, "cost_usd": 0.0, "elapsed_s": 0.0,
               "issues": [f"refused by the model's content filter ({reason}); these shots have no understanding"],
               "result": {"scenes": [], "shots": {}, "iconic": []}}
    store.write_json(receipt_path(config, plan.film.film_id, prod, chunk), receipt)
    return receipt


def split_chunk(chunk: Chunk, parts: int) -> list[Chunk]:
    """Consecutive pieces of a chunk (same index: prompts keep the film-wide part numbering)."""
    size = max(1, -(-len(chunk.shots) // parts))
    return [Chunk(chunk.index, chunk.shots[start:start + size]) for start in range(0, len(chunk.shots), size)]


def combine_results(results: list[dict[str, Any] | None]) -> dict[str, Any]:
    """Merge piece results in order; a refused piece (None) ends any continuing scene."""
    scenes: list[dict[str, Any]] = []
    shots: dict[str, dict[str, Any]] = {}
    iconic: list[dict[str, Any]] = []
    gap = False
    for result in results:
        if result is None:
            gap = True
            continue
        for index, scene in enumerate(result["scenes"]):
            if index == 0 and scene["continues_previous"] and scenes and not gap:
                scenes[-1]["last_shot"] = scene["last_shot"]
                scenes[-1]["characters"] = _names(scenes[-1]["characters"] + scene["characters"], 12)
                continue
            scenes.append(dict(scene))
        shots.update(result["shots"])
        iconic.extend(result["iconic"])
        gap = False
    return {"scenes": scenes, "shots": shots, "iconic": iconic}


def retry_refused(config: Any, db: Any, films: list[FilmRef], *, model: str = DEFAULTS["model"], parts: int = 4,
                  max_usd: float = 10.0, progress: Callable[[str], None] = print) -> dict[str, Any]:
    """Recover chunks the content filter refused: retry smaller pieces at standard price.

    A filter usually reacts to one scene, not a whole ten-minute clip. Each
    piece is tried with the synopsis, then without it; pieces that are still
    refused stay without records. The chunk's receipt becomes a ``split``
    receipt listing what was recovered and what stayed refused, and the film
    is merged again.
    """
    prod = producer(model)
    client = _client()
    budget = Budget(max_usd)
    plans, _cached = plan_films(config, db, films, prod, force=True)
    summary: dict[str, Any] = {"chunks": 0, "pieces_recovered": 0, "pieces_refused": 0, "shots_recovered": 0,
                               "usd": 0.0, "films_done": 0}
    for plan in plans:
        touched = False
        for chunk in plan.chunks:
            path = receipt_path(config, plan.film.film_id, prod, chunk)
            receipt = store.read_json(path)
            if not isinstance(receipt, dict) or not receipt.get("refused"):
                continue
            summary["chunks"] += 1
            results: list[dict[str, Any] | None] = []
            refused, issues, usage, cost, elapsed = [], [], {}, 0.0, 0.0
            for piece in split_chunk(chunk, parts):
                estimate = estimate_chunk_usd(model, piece)
                if not budget.admit(estimate):
                    progress(f"[understanding] retry budget of ${max_usd:.2f} reached; rerun to continue")
                    return summary
                response, reason = None, None
                with tempfile.TemporaryDirectory(prefix="sr-und-", dir=_temp_root(config)) as temporary:
                    proxy = render_proxy(plan.film, piece, Path(temporary))
                    for variant in ("full", "no_plot"):
                        try:
                            response = _call_with_retry(client, model, proxy, piece.fps, plan.prompt(piece, variant))
                            break
                        except Blocked as blocked:
                            reason = str(blocked)
                            if plan.contexts[0] == plan.contexts[1]:
                                break
                piece_cost = cost_usd(model, response["usage"]) if response else 0.0
                budget.settle(estimate, piece_cost)
                cost += piece_cost
                if response is None:
                    results.append(None)
                    refused.append({"first_ordinal": piece.shots[0]["ordinal"], "last_ordinal": piece.shots[-1]["ordinal"],
                                    "reason": reason})
                    summary["pieces_refused"] += 1
                    continue
                try:
                    output = json.loads(response["text"])
                    result, piece_issues = validate_chunk(piece, output if isinstance(output, dict) else {})
                except (json.JSONDecodeError, TypeError):
                    results.append(None)
                    refused.append({"first_ordinal": piece.shots[0]["ordinal"], "last_ordinal": piece.shots[-1]["ordinal"],
                                    "reason": "unparseable response"})
                    continue
                results.append(result)
                issues.extend(piece_issues)
                elapsed += float(response.get("elapsed_s") or 0.0)
                for key, value in (response.get("usage") or {}).items():
                    if isinstance(value, (int, float)):
                        usage[key] = usage.get(key, 0) + value
                summary["pieces_recovered"] += 1
                summary["shots_recovered"] += len(result["shots"])
            if not any(result is not None for result in results):
                progress(f"[understanding] {plan.film.title} part {chunk.index + 1}: every piece was refused again")
                continue
            combined = combine_results(results)
            store.write_json(path, {**{key: receipt[key] for key in ("chunk", "first_ordinal", "last_ordinal", "t_start",
                                                                     "t_end", "fps", "model")},
                                    "context": "split", "refused": None, "split": {"pieces": parts, "refused": refused},
                                    "transport": "standard", "proxy": receipt.get("proxy") or {}, "model_version": None,
                                    "finish_reason": "STOP", "usage": usage, "cost_usd": round(receipt.get("cost_usd", 0.0) + cost, 6),
                                    "elapsed_s": round(elapsed, 2),
                                    "issues": issues + [f"shots S{row['first_ordinal']}-S{row['last_ordinal']} refused by the content filter"
                                                        for row in refused],
                                    "result": combined})
            summary["usd"] = round(summary["usd"] + cost, 4)
            touched = True
            progress(f"[understanding] {plan.film.title} part {chunk.index + 1}: recovered {len(combined['shots'])}/"
                     f"{len(chunk.shots)} shots from {len(results) - len(refused)} of {len(results)} pieces (${cost:.2f})")
        if touched:
            summary["films_done"] += finalize(config, [plan], prod, model, progress)
    return summary


def finalize(config: Any, plans: list[FilmPlan], prod: store.Producer, model: str,
             progress: Callable[[str], None] = print) -> int:
    """Merge every film whose chunks all have receipts into its understanding artifact."""
    done = 0
    for plan in plans:
        receipts = [store.read_json(receipt_path(config, plan.film.film_id, prod, chunk)) for chunk in plan.chunks]
        if any(receipt is None for receipt in receipts):
            continue
        merged = merge_chunks(plan.units, receipts)
        keys = ("chunk", "t_start", "t_end", "fps", "context", "transport", "model_version", "finish_reason", "usage",
                "cost_usd", "elapsed_s", "issues")
        merged.update(model=model, chunks=[{key: receipt.get(key) for key in keys} for receipt in receipts],
                      cost_usd=round(sum(receipt["cost_usd"] for receipt in receipts), 4))
        store.write_artifact(config.paths.assets_dir, plan.film.film_id, prod, merged, inputs=plan.inputs)
        done += 1
        progress(f"[understanding] {plan.film.title}: complete — {len(merged['scenes'])} scenes, "
                 f"{len(merged['shots'])}/{len(plan.units)} shots, {len(merged['iconic'])} iconic moments, "
                 f"${merged['cost_usd']:.2f}")
    return done


def _temp_root(config: Any) -> str:
    root = Path(config.paths.assets_dir) / ".tmp" / "understanding"
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


# ---------------------------------------------------------------------------
# Standard transport: concurrent synchronous calls (pilots, single new films)
# ---------------------------------------------------------------------------


def run(config: Any, db: Any, films: list[FilmRef], *, model: str = DEFAULTS["model"], max_usd: float = 5.0,
        concurrency: int = 6, force: bool = False, chunk_limit: int | None = None,
        progress: Callable[[str], None] = print) -> dict[str, Any]:
    """Process films chunk by chunk (resumable); returns counts and spend."""
    prod = producer(model)
    budget = Budget(max_usd)
    client = _client()
    plans, cached = plan_films(config, db, films, prod, force=force)
    summary = {"films_done": 0, "films_cached": cached, "chunks_done": 0, "chunks_failed": 0, "usd": 0.0}
    jobs = [(plan, chunk) for plan in plans for chunk in pending_chunks(config, plan, prod, force=force)]
    if chunk_limit is not None:
        jobs = jobs[:chunk_limit]
    progress(f"[understanding] {model}: {len(plans)} films to process, {len(jobs)} chunks pending, budget ${max_usd:.2f}")
    lock = threading.Lock()

    def work(job: tuple[FilmPlan, Chunk]) -> None:
        plan, chunk = job
        estimate = estimate_chunk_usd(model, chunk)
        if not budget.admit(estimate):
            raise _BudgetStop()
        variant, refusal = "full", None
        with tempfile.TemporaryDirectory(prefix="sr-und-", dir=_temp_root(config)) as temporary:
            proxy = render_proxy(plan.film, chunk, Path(temporary))
            proxy_info = {"fps": proxy_fps(chunk), "bytes": proxy.stat().st_size}
            try:
                response = _call_with_retry(client, model, proxy, chunk.fps, plan.prompt(chunk, "full"))
            except Blocked as blocked:
                # Some plot synopses trip non-configurable filters; the clip, cast and dialogue remain.
                refusal = str(blocked)
                if plan.contexts[0] != plan.contexts[1]:
                    variant = "no_plot"
                    try:
                        response = _call_with_retry(client, model, proxy, chunk.fps, plan.prompt(chunk, variant))
                        refusal = None
                    except Blocked as again:
                        refusal = str(again)
        if refusal is not None:
            write_refusal(config, plan, prod, chunk, refusal, model=model, transport="standard", proxy=proxy_info)
            budget.settle(estimate, 0.0)
            progress(f"[understanding] {plan.film.title} part {chunk.index + 1}/{len(plan.chunks)}: "
                     f"refused by the content filter ({refusal}); closed without records")
            return
        receipt = write_receipt(config, plan, prod, chunk, response, model=model, variant=variant,
                                transport="standard", proxy=proxy_info)
        budget.settle(estimate, receipt["cost_usd"])
        with lock:
            summary["chunks_done"] += 1
            summary["usd"] = round(summary["usd"] + receipt["cost_usd"], 4)
        progress(f"[understanding] {plan.film.title} part {chunk.index + 1}/{len(plan.chunks)}: "
                 f"{len(receipt['result']['shots'])}/{len(chunk.shots)} shots, {len(receipt['result']['scenes'])} scenes, "
                 f"${receipt['cost_usd']:.3f}, {response['elapsed_s']}s"
                 + (" (without plot)" if variant != "full" else "")
                 + (f" issues={receipt['issues']}" if receipt["issues"] else ""))

    stopped = False
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(work, job): job for job in jobs}
        for future in as_completed(futures):
            plan, chunk = futures[future]
            try:
                future.result()
            except _BudgetStop:
                stopped = True
            except Exception as exc:  # noqa: BLE001 - one failed chunk must not stop the run
                with lock:
                    summary["chunks_failed"] += 1
                progress(f"[understanding] {plan.film.title} part {chunk.index + 1}/{len(plan.chunks)}: "
                         f"failed ({str(exc)[:200]})")
    if stopped:
        progress(f"[understanding] budget of ${max_usd:.2f} reached; rerun to continue (completed chunks are kept)")
    summary["films_done"] = finalize(config, plans, prod, model, progress)
    return summary


# ---------------------------------------------------------------------------
# Batch transport: half price, results within 24 hours (library runs)
# ---------------------------------------------------------------------------
#
# ``submit_batches`` renders and uploads proxies (Files API, kept 48 hours),
# submits batch jobs and records them in a local ledger; ``collect_batches``
# polls the jobs, writes the same chunk receipts as the standard transport,
# retries plot-blocked chunks without the synopsis, deletes uploaded files and
# merges completed films. Both steps are idempotent and safe to rerun.

BATCH_REQUESTS_PER_JOB = 100
_ACTIVE_STATES = {"JOB_STATE_PENDING", "JOB_STATE_QUEUED", "JOB_STATE_RUNNING", "JOB_STATE_UPDATING",
                  "JOB_STATE_CANCELLING", "JOB_STATE_PAUSED"}


def _ledger_path(config: Any) -> Path:
    return Path(config.paths.state_dir) / "evidence" / "understanding-batches.json"


def _read_ledger(config: Any) -> dict[str, Any]:
    ledger = store.read_json(_ledger_path(config))
    return ledger if isinstance(ledger, dict) else {"jobs": []}


def _write_ledger(config: Any, ledger: dict[str, Any]) -> None:
    path = _ledger_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    store.write_json(path, ledger)


def _chunk_key(film_id: str, chunk: Chunk) -> str:
    return f"{film_id}:{chunk.index}:{chunk.digest()[:10]}"


_UPLOAD_BACKOFF = (15.0, 60.0, 180.0)


def _upload(client: Any, path: Path, display_name: str) -> Any:
    """Upload one proxy, retrying transient failures (the client's own retries give up within seconds)."""
    for delay in (*_UPLOAD_BACKOFF, None):
        try:
            return _upload_once(client, path, display_name)
        except Exception as exc:  # noqa: BLE001
            if delay is None or not _transient(exc):
                raise
            time.sleep(delay)
    raise AssertionError("unreachable")


def _upload_once(client: Any, path: Path, display_name: str) -> Any:
    """Upload one proxy and wait until the Files API has processed it."""
    uploaded = client.files.upload(file=str(path), config={"mime_type": "video/mp4", "display_name": display_name})
    deadline = time.monotonic() + 600
    while str(getattr(uploaded.state, "name", uploaded.state)) == "PROCESSING":
        if time.monotonic() > deadline:
            raise RuntimeError(f"upload {uploaded.name} still processing after 10 minutes")
        time.sleep(3)
        uploaded = client.files.get(name=uploaded.name)
    if str(getattr(uploaded.state, "name", uploaded.state)) != "ACTIVE":
        raise RuntimeError(f"upload {uploaded.name} failed: {uploaded.error}")
    return uploaded


def submit_batches(config: Any, db: Any, films: list[FilmRef], *, model: str = DEFAULTS["model"], max_usd: float = 60.0,
                   max_chunks: int | None = None, uploads: int = 6,
                   progress: Callable[[str], None] = print) -> dict[str, Any]:
    """Upload pending chunks and submit them as batch jobs; returns counts and the estimated spend."""
    from google.genai import types

    prod = producer(model)
    client = _client()
    ledger = _read_ledger(config)
    in_flight = {request["key"] for job in ledger["jobs"] if job["state"] in _ACTIVE_STATES for request in job["requests"]}
    plans, _cached = plan_films(config, db, films, prod, force=False)
    todo = [(plan, chunk) for plan in plans for chunk in pending_chunks(config, plan, prod)
            if _chunk_key(plan.film.film_id, chunk) not in in_flight]
    if max_chunks is not None:
        todo = todo[:max_chunks]
    budget = Budget(max_usd)
    todo = [(plan, chunk) for plan, chunk in todo if budget.admit(estimate_chunk_usd(model, chunk, batch=True))]
    progress(f"[understanding:batch] {len(todo)} chunks to submit from {len({p.film.film_id for p, _ in todo})} films "
             f"(estimated ${budget.spent:.2f} at batch prices; {len(in_flight)} already in flight)")
    submitted = 0
    for start in range(0, len(todo), BATCH_REQUESTS_PER_JOB):
        group = todo[start:start + BATCH_REQUESTS_PER_JOB]

        def prepare(item: tuple[FilmPlan, Chunk]) -> dict[str, Any]:
            plan, chunk = item
            key = _chunk_key(plan.film.film_id, chunk)
            with tempfile.TemporaryDirectory(prefix="sr-und-", dir=_temp_root(config)) as temporary:
                proxy = render_proxy(plan.film, chunk, Path(temporary))
                size = proxy.stat().st_size
                uploaded = _upload(client, proxy, key)
            return {"key": key, "film_id": plan.film.film_id, "chunk": chunk.index, "fps": chunk.fps,
                    "file": uploaded.name, "file_uri": uploaded.uri, "variant": "full", "bytes": size}

        with ThreadPoolExecutor(max_workers=max(1, uploads)) as pool:
            futures = [pool.submit(prepare, item) for item in group]
        prepared = []
        for item, future in zip(group, futures):
            try:
                prepared.append((item, future.result()))
            except Exception as exc:  # noqa: BLE001 - render or upload failure: retry this chunk in a later wave
                plan, chunk = item
                progress(f"[understanding:batch] {plan.film.title} part {chunk.index + 1}: not submitted "
                         f"({type(exc).__name__}: {str(exc)[:160]}); it stays pending")
        if not prepared:
            break
        group = [item for item, _request in prepared]
        requests = [request for _item, request in prepared]
        inline = []
        for (plan, chunk), request in zip(group, requests):
            video = types.File(name=request["file"], uri=request["file_uri"], mime_type="video/mp4")
            inline.append(types.InlinedRequest(contents=_contents(video, chunk.fps, plan.prompt(chunk, "full")),
                                               config=_generation_config(batch=True), metadata={"key": request["key"]}))
        try:
            job = client.batches.create(model=model, src=inline,
                                        config={"display_name": f"scene-recall-understanding-{int(time.time())}"})
        except Exception as exc:  # noqa: BLE001 - e.g. enqueued-token limits: stop, keep nothing half-submitted
            for request in requests:
                try:
                    client.files.delete(name=request["file"])
                except Exception:  # noqa: BLE001
                    pass
            progress(f"[understanding:batch] job creation refused ({str(exc)[:200]}); "
                     f"{len(todo) - submitted} chunks left for a later submit")
            break
        ledger["jobs"].append({"name": job.name, "model": model, "profile": prod.profile_id,
                               "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "state": str(job.state.name),
                               "requests": requests})
        _write_ledger(config, ledger)
        submitted += len(requests)
        progress(f"[understanding:batch] submitted {job.name} with {len(requests)} chunks "
                 f"({sum(r['bytes'] for r in requests) / 1e6:.0f} MB uploaded)")
    return {"submitted": submitted, "jobs": len(ledger["jobs"]), "estimated_usd": round(budget.spent, 2)}


def collect_batches(config: Any, db: Any, *, progress: Callable[[str], None] = print) -> dict[str, Any]:
    """Write receipts for finished jobs, retry blocked chunks without the plot, merge complete films."""
    from pipeline.evidence.library import list_films

    client = _client()
    ledger = _read_ledger(config)
    films = {film.film_id: film for film in list_films(db)}
    counts = {"receipts": 0, "retried": 0, "failed": 0, "waiting_jobs": 0, "usd": 0.0}
    touched: dict[tuple[str, str], FilmPlan] = {}
    for job_record in ledger["jobs"]:
        if job_record["state"] not in _ACTIVE_STATES:
            continue
        try:
            job = client.batches.get(name=job_record["name"])
        except Exception as exc:  # noqa: BLE001 - e.g. 503: look again on the next poll
            if not _transient(exc):
                raise
            counts["waiting_jobs"] += 1
            progress(f"[understanding:batch] could not check {job_record['name']} ({str(exc)[:120]}); will retry")
            continue
        job_record["state"] = str(job.state.name)
        if job_record["state"] in _ACTIVE_STATES:
            counts["waiting_jobs"] += 1
            continue
        prod = producer(job_record["model"])
        responses = {}
        for item in (job.dest.inlined_responses if job.dest else None) or []:
            key = (item.metadata or {}).get("key")
            if key:
                responses[key] = item
        for request in job_record["requests"]:
            film = films.get(request["film_id"])
            if film is None:
                continue
            plan_key = (film.film_id, prod.profile_id)
            if plan_key not in touched:
                plans, _ = plan_films(config, db, [film], prod, force=True)
                if not plans:
                    continue
                touched[plan_key] = plans[0]
            plan = touched[plan_key]
            chunk = next((c for c in plan.chunks if _chunk_key(film.film_id, c) == request["key"]), None)
            if chunk is None or receipt_path(config, film.film_id, prod, chunk).is_file():
                continue           # shots changed since submission, or already receipted
            item = responses.get(request["key"])
            payload = response_payload(item.response) if item is not None and item.response is not None else None
            try:
                if payload is not None and payload["text"]:
                    receipt = write_receipt(config, plan, prod, chunk, payload, model=job_record["model"],
                                            variant=request["variant"], transport="batch",
                                            proxy={"fps": proxy_fps(chunk), "bytes": request["bytes"]})
                    counts["receipts"] += 1
                    counts["usd"] = round(counts["usd"] + receipt["cost_usd"], 4)
                elif payload is not None and (payload["block_reason"] or _refused_finish(payload["finish_reason"])):
                    # Rare: retry at standard price, without the synopsis, against the still-uploaded clip.
                    refusal = str(payload["block_reason"] or payload["finish_reason"])
                    proxy = {"fps": proxy_fps(chunk), "bytes": request["bytes"]}
                    if plan.contexts[0] != plan.contexts[1]:
                        video = client.files.get(name=request["file"])
                        try:
                            retry = _call_with_retry(client, job_record["model"], video, chunk.fps,
                                                     plan.prompt(chunk, "no_plot"))
                            receipt = write_receipt(config, plan, prod, chunk, retry, model=job_record["model"],
                                                    variant="no_plot", transport="standard", proxy=proxy)
                            counts["retried"] += 1
                            counts["usd"] = round(counts["usd"] + receipt["cost_usd"], 4)
                            refusal = None
                        except Blocked as again:
                            refusal = str(again)
                    if refusal is not None:
                        write_refusal(config, plan, prod, chunk, refusal, model=job_record["model"],
                                      transport="batch", proxy=proxy)
                        counts["refused"] = counts.get("refused", 0) + 1
                        progress(f"[understanding:batch] {film.title} part {chunk.index + 1}: refused by the content "
                                 f"filter ({refusal}); closed without records")
                else:
                    counts["failed"] += 1
                    reason = (payload or {}).get("block_reason") or (payload or {}).get("finish_reason") \
                        or getattr(item, "error", None) or "no response"
                    progress(f"[understanding:batch] {film.title} part {chunk.index + 1}: failed ({reason}); "
                             "it will be resubmitted")
            except Exception as exc:  # noqa: BLE001 - one chunk must not stop collection
                counts["failed"] += 1
                progress(f"[understanding:batch] {film.title} part {chunk.index + 1}: failed ({str(exc)[:200]})")
        for request in job_record["requests"]:
            try:
                client.files.delete(name=request["file"])
            except Exception:  # noqa: BLE001 - uploads expire after 48 hours anyway
                pass
        _write_ledger(config, ledger)
        progress(f"[understanding:batch] collected {job_record['name']} ({job_record['state']})")
    _write_ledger(config, ledger)
    for (_film_id, profile_id), plan in touched.items():
        model = next(job["model"] for job in ledger["jobs"] if job["profile"] == profile_id)
        counts.setdefault("films_done", 0)
        counts["films_done"] += finalize(config, [plan], producer(model), model, progress)
    return counts


def batch_status(config: Any) -> list[dict[str, Any]]:
    return [{"name": job["name"], "state": job["state"], "created": job["created"], "chunks": len(job["requests"])}
            for job in _read_ledger(config)["jobs"]]


def run_batches(config: Any, db: Any, films: list[FilmRef], *, model: str = DEFAULTS["model"], max_usd: float = 120.0,
                wave_chunks: int = 300, poll_seconds: int = 300,
                progress: Callable[[str], None] = print) -> dict[str, Any]:
    """Drive the batch transport unattended: submit waves, poll, collect, until every chunk has a receipt.

    At most ``wave_chunks`` chunks are in flight at once (Files API storage and
    enqueued-token limits); ``max_usd`` bounds the estimated batch spend of all
    submissions made by this call.
    """
    prod = producer(model)
    spent = 0.0
    totals: dict[str, Any] = {"receipts": 0, "failed": 0, "usd": 0.0}
    failures = 0
    while True:
        try:
            spent, done = _batch_cycle(config, db, films, prod, model=model, max_usd=max_usd, spent=spent,
                                       wave_chunks=wave_chunks, totals=totals, progress=progress)
            failures = 0
        except Exception as exc:  # noqa: BLE001 - an outage must not end an unattended library run
            failures += 1
            if not _transient(exc) or failures > _MAX_FAILED_CYCLES:
                raise
            progress(f"[understanding:batch] cycle failed ({type(exc).__name__}: {str(exc)[:160]}); "
                     f"retrying in {poll_seconds}s ({failures}/{_MAX_FAILED_CYCLES})")
            done = False
        if done:
            return totals
        time.sleep(poll_seconds)


_MAX_FAILED_CYCLES = 12


def _batch_cycle(config: Any, db: Any, films: list[FilmRef], prod: store.Producer, *, model: str, max_usd: float,
                 spent: float, wave_chunks: int, totals: dict[str, Any],
                 progress: Callable[[str], None]) -> tuple[float, bool]:
    """One collect-and-submit round; returns the new estimated spend and whether the run is finished."""
    collected = collect_batches(config, db, progress=progress)
    for key in ("receipts", "failed"):
        totals[key] += collected.get(key, 0)
    totals["usd"] = round(totals["usd"] + collected.get("usd", 0.0), 4)
    ledger = _read_ledger(config)
    in_flight = sum(len(job["requests"]) for job in ledger["jobs"] if job["state"] in _ACTIVE_STATES)
    plans, _cached = plan_films(config, db, films, prod, force=False)
    pending = sum(len(pending_chunks(config, plan, prod)) for plan in plans)
    waiting = pending - in_flight
    progress(f"[understanding:batch] {in_flight} chunks in flight, {max(0, waiting)} to submit, "
             f"{totals['receipts']} collected (${totals['usd']:.2f})")
    if pending == 0:
        return spent, True
    room = wave_chunks - in_flight
    if waiting > 0 and room >= min(waiting, BATCH_REQUESTS_PER_JOB) and spent < max_usd:
        submitted = submit_batches(config, db, films, model=model, max_usd=max_usd - spent,
                                   max_chunks=room, progress=progress)
        spent += submitted["estimated_usd"]
        if submitted["submitted"] == 0 and in_flight == 0:
            progress("[understanding:batch] nothing could be submitted and nothing is in flight; stopping")
            return spent, True
    elif in_flight == 0:
        progress("[understanding:batch] budget reached or nothing left to submit; stopping")
        return spent, True
    return spent, False
