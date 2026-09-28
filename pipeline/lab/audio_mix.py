"""Shared song-clock envelope and deterministic source-dialogue mix contract."""

from __future__ import annotations

import math


AUDIO_MIX_PROFILE = "source-dialogue-shared-pcm-envelope-v2"
MUSIC_DUCK_ATTACK_SECONDS = .25
MUSIC_DUCK_RELEASE_SECONDS = .5


def music_gain_at(document, song_time):
    """Linear amplitude, also implemented by the live browser audio clock."""
    start, end = document["passage"]["start"], document["passage"]["end"]
    if song_time < start or song_time >= end:
        return 0.0
    gain = 10 ** (document.get("music_gain_db", 0) / 20)
    fade_in, fade_out = document.get("audio_fade_in_seconds", 0), document.get("audio_fade_out_seconds", 0)
    if fade_in:
        gain *= min(1, (song_time - start) / fade_in)
    if fade_out:
        gain *= min(1, (end - song_time) / fade_out)
    duck = 1.0
    for clip in document.get("dialogue_clips", []):
        left = clip["start"]
        right = left + clip["source_end"] - clip["source_start"]
        quiet = 10 ** (clip.get("music_duck_db", -8) / 20)
        attack = clip.get("duck_attack_seconds", MUSIC_DUCK_ATTACK_SECONDS)
        release = clip.get("duck_release_seconds", MUSIC_DUCK_RELEASE_SECONDS)
        if left - attack < song_time < left:
            value = 1 + (quiet - 1) * (song_time - left + attack) / attack
        elif left <= song_time < right:
            value = quiet
        elif right <= song_time < right + release:
            value = quiet + (1 - quiet) * (song_time - right) / release
        else:
            value = 1.0
        duck = min(duck, value)
    return gain * duck


def music_manifest(document, *, offset=0, duration=None):
    passage = document["passage"]
    return {
        "track_id": document["track"]["id"], "start": passage["start"] + offset,
        "end": passage["end"] if duration is None else passage["start"] + offset + duration,
        "envelope_start": passage["start"], "envelope_end": passage["end"], "offset_seconds": offset,
        "fade_in_seconds": document.get("audio_fade_in_seconds", 0),
        "fade_out_seconds": document.get("audio_fade_out_seconds", 0),
        "gain_db": document.get("music_gain_db", 0),
    }


def _number(value):
    if not math.isfinite(value):
        raise ValueError("Audio envelope values must be finite")
    return format(value, ".12g")


def _duck_expression(clips, passage_start):
    envelope = "1"
    for clip in clips:
        left = clip["start"] - passage_start
        right = left + clip["source_end"] - clip["source_start"]
        quiet = 10 ** (clip.get("music_duck_db", -8) / 20)
        attack = clip.get("duck_attack_seconds", MUSIC_DUCK_ATTACK_SECONDS)
        release = clip.get("duck_release_seconds", MUSIC_DUCK_RELEASE_SECONDS)
        a, s, e, r, q = map(_number, (left - attack, left, right, right + release, quiet))
        recovery = f"if(lt(t,{r}),{q}+(1-{q})*(t-({e}))/{_number(release)},1)" if release else "1"
        hold = f"if(lt(t,{e}),{q},{recovery})"
        onset = f"if(lt(t,{s}),1+({q}-1)*(t-({a}))/{_number(attack)},{hold})" if attack else hold
        value = f"if(lt(t,{a}),1,{onset})"
        envelope = f"min({envelope},{value})"
    return envelope


def audio_filter_graph(manifest):
    """Inputs: picture 0, original song 1, shared source-window PCM 2 onward.

    All envelopes run from full-passage zero before an excerpt is trimmed, so
    fades and dialogue crossing a picture cut never restart at that cut.
    """
    music = manifest["music"]
    clips = manifest.get("dialogue_clips", [])
    passage_start = music.get("envelope_start", music["start"])
    passage_duration = music.get("envelope_end", music["end"]) - passage_start
    filters = ["aresample=48000:async=1:first_pts=0", "aformat=sample_fmts=fltp:channel_layouts=stereo",
               f"atrim=duration={_number(passage_duration)}", f"apad=whole_dur={_number(passage_duration)}",
               f"volume={_number(10 ** (music.get('gain_db', 0) / 20))}"]
    if music.get("fade_in_seconds", 0):
        filters.append(f"afade=t=in:st=0:d={_number(music['fade_in_seconds'])}:curve=tri")
    if music.get("fade_out_seconds", 0):
        filters.append(f"afade=t=out:st={_number(passage_duration - music['fade_out_seconds'])}:d={_number(music['fade_out_seconds'])}:curve=tri")
    if clips:
        duck = _duck_expression(clips, passage_start)
        filters.append(f"aeval=exprs='val(0)*({duck})|val(1)*({duck})':c=stereo")
    graph = [f"[1:a:0]{','.join(filters)}[song]"]
    for index, clip in enumerate(clips):
        duration = clip["source_end"] - clip["source_start"]
        filters = ["aresample=48000:async=1:first_pts=0", "aformat=sample_fmts=fltp:channel_layouts=stereo",
                   f"atrim=duration={_number(duration)}", f"apad=whole_dur={_number(duration)}",
                   f"volume={_number(10 ** (clip.get('gain_db', 0) / 20))}"]
        fade_in, fade_out = min(duration, clip.get("fade_in_seconds", .08)), min(duration, clip.get("fade_out_seconds", .12))
        if fade_in:
            filters.append(f"afade=t=in:st=0:d={_number(fade_in)}:curve=tri")
        if fade_out:
            filters.append(f"afade=t=out:st={_number(duration - fade_out)}:d={_number(fade_out)}:curve=tri")
        delay_samples = round((clip["start"] - passage_start) * 48000)
        filters.append(f"adelay={delay_samples}S:all=1")
        graph.append(f"[{index + 2}:a:0]{','.join(filters)}[dialogue{index}]")
    inputs = "[song]" + "".join(f"[dialogue{index}]" for index in range(len(clips)))
    graph.append(f"{inputs}amix=inputs={1 + len(clips)}:duration=first:dropout_transition=0:normalize=0,"
                 f"atrim=start={_number(music.get('offset_seconds', 0))}:duration={_number(manifest['duration'])},"
                 "asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo,apad[mixed]")
    return ";\n".join(graph)
