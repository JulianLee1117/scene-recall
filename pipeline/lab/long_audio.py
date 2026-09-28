"""Compose bounded listening results without a full-song hosted audio request.

Original per-part observations remain immutable in ``parts``. The top-level
view is a deterministic, source-time projection, not another model inference.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import re

from pydantic import Field

from pipeline.lab.limits import MAX_AUDIO_PARTS, MAX_AUDIO_PART_SECONDS
from pipeline.lab.long_form import partition_passage
from pipeline.lab.music import (AudioEvent, EditBeat, EmotionalSegment, Interpretation,
                                digest, validate_interpretation)
from pipeline.lab.song_meaning import WholeSongMeaning


PARTS_CONTRACT = "bounded-audio-parts-v1"
FIELDS = ("summary", "segments", "edit_beats", "events", "song_meaning")


class LongInterpretation(Interpretation):
    summary: str = Field(min_length=1, max_length=4100 * MAX_AUDIO_PARTS)
    segments: list[EmotionalSegment] = Field(min_length=1, max_length=8 * MAX_AUDIO_PARTS)
    edit_beats: list[EditBeat] = Field(default_factory=list, max_length=32 * MAX_AUDIO_PARTS)
    events: list[AudioEvent] = Field(default_factory=list, max_length=32 * MAX_AUDIO_PARTS)
    song_meaning: WholeSongMeaning


def _overlaps(left, right):
    return left["start"] < right["end"] and right["start"] < left["end"]


def _label(passage, text):
    return f"[{passage['start']:.3f}–{passage['end']:.3f}s] {text}"


def _clip(items, passage, *, minimum=0.):
    result = []
    for item in items:
        if _overlaps(item, passage):
            clipped = {**deepcopy(item), "start": max(item["start"], passage["start"]),
                       "end": min(item["end"], passage["end"])}
            if clipped["end"] - clipped["start"] >= minimum:
                result.append(clipped)
    return result


def _validated_parts(parts, passage):
    if not isinstance(parts, list) or not 1 <= len(parts) <= MAX_AUDIO_PARTS:
        raise ValueError("Whole-song analysis requires one to seven bounded audio parts")
    track_id, previous = None, None
    for part in parts:
        if not isinstance(part, dict) or set(part) != {"passage", "analysis"}:
            raise ValueError("Invalid bounded audio part")
        scope, analysis = part["passage"], part["analysis"]
        if (not isinstance(scope, dict) or set(scope) != {"start", "end"}
                or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                       for value in scope.values())
                or not 0 < scope["end"] - scope["start"] <= MAX_AUDIO_PART_SECONDS
                or not _overlaps(scope, passage)):
            raise ValueError("Audio parts must be bounded and overlap their derived view")
        if previous is not None and abs(previous - scope["start"]) > 1e-6:
            raise ValueError("Audio parts must be contiguous and ordered")
        previous = scope["end"]
        if not isinstance(analysis, dict) or "parts" in analysis:
            raise ValueError("Audio parts must contain original bounded listening results")
        validate_interpretation(analysis, scope, require_edit_beats=True)
        provenance = analysis.get("provenance") or {}
        if not provenance.get("track") or provenance.get("passage") != scope:
            raise ValueError("Audio part has incompatible track or passage provenance")
        track_id = track_id or provenance["track"]
        if provenance["track"] != track_id:
            raise ValueError("Audio parts must belong to the same imported track")
        for key, source in (("events_provenance", "ai-observed"),
                            ("song_meaning_provenance", "ai-heard-paraphrase")):
            profile = analysis.get(key) or {}
            if (profile.get("source") != source or profile.get("track") != track_id
                    or profile.get("passage") != scope or profile.get("interpretation_id") != digest(provenance)):
                raise ValueError("Audio part observations have incompatible provenance")
    if parts[0]["passage"]["start"] > passage["start"] or parts[-1]["passage"]["end"] < passage["end"]:
        raise ValueError("Audio parts must cover their complete derived view")
    return track_id


def compose_analysis(parts, passage):
    """Concatenate evidence, keeping original scopes and IDs in the stored parts."""
    track_id = _validated_parts(parts, passage)
    manifest = [{"passage": deepcopy(part["passage"]),
                 "interpretation_id": digest(part["analysis"]["provenance"])} for part in parts]
    provenance = {"track": track_id, "passage": deepcopy(passage), "contract": PARTS_CONTRACT, "parts": manifest}
    summaries, segments, moments, events = [], [], [], []
    meaning_summaries, uncertainties, themes, cues, statuses = [], [], [], [], []
    for part, reference in zip(parts, manifest):
        scope, analysis = part["passage"], part["analysis"]
        summaries.append(_label(scope, analysis["summary"]))
        segments.extend(_clip(analysis["segments"], passage))
        # A partial edge moment shorter than a frame is context, not a legal
        # editable shot. Its neighboring segment still covers the exact scope.
        moments.extend(_clip(analysis["edit_beats"], passage, minimum=1 / 24 - 1e-6))
        for event in _clip(analysis.get("events", []), passage):
            event["id"] = "part-" + digest({"part": reference, "event": event["id"]})[:32]
            events.append(event)
        meaning = analysis["song_meaning"]
        local_cues = _clip(meaning["cues"], passage)
        statuses.append(meaning["vocal_status"])
        cues.extend(local_cues)
        if local_cues or meaning["vocal_status"] in {"unclear", "no_vocals"}:
            meaning_summaries.append(_label(scope, meaning["summary"]))
        if local_cues:
            themes.extend(theme for theme in meaning["themes"] if theme not in themes)
        if meaning["uncertainty"]:
            uncertainties.append(_label(scope, meaning["uncertainty"]))
    if cues:
        status = "understood" if all(status in {"understood", "no_vocals"} for status in statuses) else "partly_understood"
    else:
        status = "no_vocals" if all(status == "no_vocals" for status in statuses) else "unclear"
        themes = []
    meaning = {"vocal_status": status, "summary": "\n".join(meaning_summaries) or
               "No timestamped understood vocal meaning overlaps this passage.",
               "themes": themes, "cues": cues, "uncertainty": "\n".join(uncertainties)}
    profiles = {"track": track_id, "passage": deepcopy(passage), "aggregation_contract": PARTS_CONTRACT,
                "parts": deepcopy(manifest)}
    result = {"summary": "\n".join(summaries), "segments": segments, "edit_beats": moments,
              "events": events, "song_meaning": meaning, "provenance": provenance,
              "events_provenance": {**profiles, "source": "ai-observed"},
              "song_meaning_provenance": {**deepcopy(profiles), "source": "ai-heard-paraphrase"},
              "parts": deepcopy(parts), "time_base": "source-track-seconds"}
    LongInterpretation.model_validate({key: result[key] for key in FIELDS})
    return result


def validate_long_interpretation(analysis, passage):
    expected = compose_analysis(analysis["parts"], passage)
    for key in (*FIELDS, "provenance", "events_provenance", "song_meaning_provenance", "time_base"):
        if analysis.get(key) != expected[key]:
            raise ValueError("Whole-song analysis has stale or inconsistent derived evidence")
    return LongInterpretation.model_validate({key: analysis[key] for key in FIELDS})


def _numeric_equivalent(left, right):
    """JSON-equivalent numbers, without treating booleans as integers."""
    if type(left) in (int, float) and type(right) in (int, float):
        return math.isfinite(left) and math.isfinite(right) and left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(_numeric_equivalent(value, right[key]) for key, value in left.items())
    if isinstance(left, list):
        return len(left) == len(right) and all(_numeric_equivalent(a, b) for a, b in zip(left, right))
    return left == right


def recover_numeric_analysis(analysis, config, *, track_id, passage):
    """Restore authentic original number types on a private generation input.

    Browser JSON can turn 0.0 into 0, changing the byte-sensitive interpretation
    digest without changing any observation. Never recalculate a claimed ID or
    trust a changed observation: every original part must exist at its recorded
    cache ID, pass normal validation and match all saved fields numerically.
    None leaves the caller's ordinary stale-analysis fallback in control.
    """
    try:
        if not isinstance(analysis, dict) or "parts" not in analysis:
            return None
        provenance = analysis.get("provenance") or {}
        if provenance.get("track") != track_id or not _numeric_equivalent(provenance.get("passage"), passage):
            return None
        try:
            validate_long_interpretation(analysis, passage)
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
        else:
            return None  # Valid analysis needs neither compatibility work nor disk reads.
        saved_parts = analysis["parts"]
        if not isinstance(saved_parts, list) or not 1 <= len(saved_parts) <= MAX_AUDIO_PARTS:
            return None
        root = (config.paths.assets_dir / "lab" / "interpretations").resolve()
        parts = []
        for part in saved_parts:
            if not isinstance(part, dict) or set(part) != {"passage", "analysis"}:
                return None
            saved = part["analysis"]
            identity = saved["events_provenance"]["interpretation_id"]
            if (not isinstance(identity, str) or re.fullmatch(r"[0-9a-f]{64}", identity) is None
                    or saved["song_meaning_provenance"]["interpretation_id"] != identity):
                return None
            cache = (root / f"{identity}.json").resolve()
            if not cache.is_relative_to(root):
                return None
            original = json.loads(cache.read_text(encoding="utf-8"))
            if (digest(original["provenance"]) != identity or not _numeric_equivalent(saved, original)
                    or original["provenance"].get("track") != track_id
                    or not _numeric_equivalent(part["passage"], original["provenance"].get("passage"))):
                return None
            parts.append({"passage": deepcopy(original["provenance"]["passage"]), "analysis": original})
        restored = compose_analysis(parts, passage)
        for key in (*FIELDS, "parts", "provenance", "events_provenance", "song_meaning_provenance", "time_base"):
            if not _numeric_equivalent(analysis.get(key), restored[key]):
                return None
        # Preserve unrelated derived diagnostics; only authenticated listening
        # fields and their exact original provenance are replaced privately.
        result = {**deepcopy(analysis), **restored}
        validate_long_interpretation(result, passage)
        return result
    except (OSError, UnicodeError, KeyError, TypeError, ValueError, AttributeError, OverflowError):
        return None


def analysis_for_passage(analysis, passage):
    """Return exact cached audio evidence or an explicitly derived clipped view."""
    scope = (analysis.get("provenance") or {}).get("passage")
    if not scope or not scope["start"] <= passage["start"] < passage["end"] <= scope["end"]:
        raise ValueError("Requested analysis must stay inside its original music passage")
    if "parts" not in analysis:
        # Older saved analyses have valid broad evidence without modern event
        # profiles. They can be reused, but must not acquire invented provenance.
        validate_interpretation(analysis, scope)
        if scope == passage:
            return deepcopy(analysis)
        if not all(key in analysis for key in ("events_provenance", "song_meaning_provenance", "song_meaning", "edit_beats")):
            view = {key: deepcopy(analysis[key]) for key in FIELDS if key in analysis}
            for key in ("segments", "edit_beats", "events"):
                if key in view:
                    view[key] = _clip(view[key], passage, minimum=1 / 24 - 1e-6 if key == "edit_beats" else 0.)
            if view.get("song_meaning"):
                meaning = view["song_meaning"]
                meaning["cues"] = _clip(meaning.get("cues", []), passage)
                if not meaning["cues"] and meaning["vocal_status"] in {"understood", "partly_understood"}:
                    meaning.update(vocal_status="unclear", themes=[], summary="No supported vocal meaning overlaps this passage.")
            view["provenance"] = {**deepcopy(analysis["provenance"]), "passage": deepcopy(passage),
                "source_passage": deepcopy(scope), "projection_contract": "legacy-music-view-v1"}
            # Keep absent/unverified observation profiles absent. This is broad
            # context, not a newly heard or verified event interpretation.
            validate_interpretation(view, passage)
            return view
    if "parts" in analysis:
        validate_long_interpretation(analysis, scope)
        parts = [part for part in analysis["parts"] if _overlaps(part["passage"], passage)]
    else:
        parts = [{"passage": scope, "analysis": analysis}]
        _validated_parts(parts, scope)
    if len(parts) == 1 and parts[0]["passage"] == passage:
        return deepcopy(parts[0]["analysis"])
    return compose_analysis(parts, passage)


def rhythm_for_passage(rhythm, passage):
    """Project already measured rhythm; preserve its original RMS normalization."""
    provenance = rhythm.get("provenance") or {}
    scope = provenance.get("passage")
    if not scope or not scope["start"] <= passage["start"] < passage["end"] <= scope["end"]:
        return {}
    if passage == scope:
        return deepcopy(rhythm)
    result = deepcopy(rhythm)
    result["provenance"] = {**provenance, "passage": deepcopy(passage),
                            "source_passage": deepcopy(provenance.get("source_passage", scope)),
                            "projection_contract": "source-time-rhythm-view-v1"}
    for key in ("beats", "downbeats", "markers"):
        values = rhythm.get(key, [])
        result[key] = [value for value in values if isinstance(value, (float, int)) and not isinstance(value, bool)
                       and math.isfinite(value) and passage["start"] <= value <= passage["end"]]
    if result.get("marker_source") == "user":
        if result.get("passage") == scope and result.get("track_id") == provenance.get("track"):
            result["passage"] = deepcopy(passage)
        else:
            result["markers"] = []
    for key in ("waveform", "intensity"):
        values = rhythm.get(key)
        if (not isinstance(values, list) or not values or len(values) > 10000
                or rhythm.get("waveform_start") != scope["start"] or rhythm.get("waveform_end") != scope["end"]):
            result.pop(key, None)
            continue
        width = (scope["end"] - scope["start"]) / len(values)
        count = max(1, math.ceil((passage["end"] - passage["start"]) / width))
        step = (passage["end"] - passage["start"]) / count
        projected = []
        for index in range(count):
            left, right = passage["start"] + index * step, passage["start"] + (index + 1) * step
            first = max(0, math.floor((left - scope["start"]) / width))
            last = min(len(values), math.ceil((right - scope["start"]) / width))
            weighted = [(values[i], min(right, scope["start"] + (i + 1) * width) -
                         max(left, scope["start"] + i * width)) for i in range(first, last)]
            weighted = [(value, weight) for value, weight in weighted if weight > 0]
            projected.append(max(value for value, _ in weighted) if key == "waveform" else
                             sum(value * weight for value, weight in weighted) / sum(weight for _, weight in weighted))
        result[key] = projected
    result.update(waveform_start=passage["start"], waveform_end=passage["end"])
    return result


def interpret_long_audio(config, source, document, job_id, progress):
    from pipeline.lab import music
    from pipeline.lab.music_evidence import music_evidence

    passages = partition_passage(document["passage"])
    parts = []
    for index, passage in enumerate(passages):
        def report(message, number=index + 1):
            progress(f"Music section {number} of {len(passages)} · {message}")

        report("Preparing bounded audio")
        identity = {"track": document["track"]["id"], "passage": passage, "profile": music.AUDIO_PROFILE}
        audio_path = config.paths.assets_dir / "lab" / "audio" / (digest(identity) + ".wav")
        music._decode_audio(source, audio_path, passage["start"], passage["end"])
        local = {**document, "passage": passage, "rhythm": rhythm_for_passage(document.get("rhythm") or {}, passage)}
        timeline = document.get("music_timeline")
        if timeline:
            local["music_timeline"] = {**timeline, "slots": _clip(timeline["slots"], passage)}
        analysis = music.interpret_audio(config, document["track"]["id"], passage, audio_path,
                                         document.get("brief", ""), f"{job_id}-part-{index + 1}", report,
                                         music_evidence(local, include_analysis=False))
        parts.append({"passage": passage, "analysis": analysis})
    progress("Combining the song's timestamped listening evidence")
    return compose_analysis(parts, document["passage"])
