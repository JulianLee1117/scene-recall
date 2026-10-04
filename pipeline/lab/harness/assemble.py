"""Assembly: choose cuts, shots and source windows together over the beat grid.

A lattice beam search. Nodes are grid points (beats; at kinetic and rapid
paces also half-beats and strong off-beat accents), snapped to output frames.
A step places one candidate shot on the span between two grid points. Each
node keeps the best few partial edits that end there.

Every act has duration bounds and a target. The four paces scale theirs with
the music's intensity; a flash and a hold are fixed shapes the concept places
inside a section. A flash cuts on a steady subdivision of the beat (the one
nearest a quarter second), one shot per step, and rewards shots that look
alike so the burst reads as one idea. A hold is one shot across the act (two
when nothing in the pool is long enough).

A cast (``cast``) leads an act's pool: cast shots score a bonus (more for the
act's peak) and keep their cast order within the act; the rest of the pool
only fills time the cast cannot.

A placement is scored on its own:

- relevance to the act's queries;
- the action peak landing on the span's strongest accent (or just after the
  cut when the span has none);
- motion matching the music's loudness;
- duration fitting the act's pace at that energy;
- craft;
- the act's fame target (anchors or fresh footage).

A transition from the previous shot adds eye-trace and screen-direction
continuity. With a moment index (Match Cuts), eye trace becomes a measured
match between the outgoing shot's last frame and the incoming shot's first
(subject, eyes, pose, light, motion; ``matchcuts``), weighted by the edit's
match-cut setting. It penalises the same scene, the same film back to back,
and near-identical framing from the same film (a jump cut).

Locked shots are fixed spans the search must pass through. Nothing here calls
a model; the same inputs always give the same edit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np

from pipeline.lab.harness.music_map import MusicMap
from pipeline.lab.harness.pools import Candidate

ASSEMBLY_CONTRACT = "beat-lattice-assembly-v3"
# Duration bounds and target (seconds) per pace, before energy scaling.
PACE = {"patient": (2.0, 5.0, 12.0), "balanced": (1.0, 2.6, 6.0), "kinetic": (0.5, 1.4, 3.0), "rapid": (0.25, 0.8, 1.8)}
FLASH = (0.125, 0.25, 0.5)     # three to twelve frames at 24 fps; not scaled by intensity
_HOLD_SPLIT_S = 1.5            # a hold splits only when no shot covers it, never into pieces shorter than this
# Per second of screen time (relative to the pace target), so the number of cuts never inflates quality.
QUALITY = {"relevance": 1.0, "motion": 0.25, "craft": 0.1}
# Once per shot: events at the cut and the rhythm of durations.
ALIGNMENT, PACE_PENALTY = 0.45, 0.6
_BEAM = 8
_PER_SPAN = 12
_ACCENT_WINDOW_S = 0.3
_STRONG_ACCENT = 0.6
_CUT_GUARD_S = 0.08            # an accent at the cut itself belongs to the cut, not the shot
# Visual variety: image similarity to the last few shots above the library's usual level costs score.
_SIMILAR_FROM, _SIMILAR_SCALE, _SIMILAR_COST = 0.62, 0.2, 0.5
_RECENCY = (1.0, 0.7, 0.5, 0.35, 0.25, 0.2)
_CLUSTER_REUSE = 0.12          # per earlier shot of the same look, per target-length of screen time
_FILM_REUSE = 0.08             # per earlier shot of the same film, per target-length of screen time
_CAST_BONUS, _PEAK_BONUS = 0.6, 0.4   # per target-length of screen time, for cast shots and the act's peak
_FLASH_STEP_S = 0.25           # a flash cuts on the beat subdivision nearest this


@dataclass
class Act:
    start: float
    end: float
    pool: list[Candidate]
    pace: str = "balanced"             # patient | balanced | kinetic | rapid | flash | hold
    fame: str = "any"                  # anchor | fresh | any
    intent: str = ""
    pace_scale: float = 1.0            # >1 holds longer (a critique found it too busy), <1 cuts faster


def bounds(act: Act) -> tuple[float, float, float]:
    """Shortest, target and longest shot (seconds) for an act, before intensity scaling."""
    if act.pace == "hold":
        length = act.end - act.start
        return min(length, _HOLD_SPLIT_S), length, length
    if act.pace == "flash":
        return FLASH
    low, target, high = PACE.get(act.pace, PACE["balanced"])
    return low * act.pace_scale, target, high * act.pace_scale


@dataclass
class Fixed:
    """A shot the edit must keep (locked or retained) over [start, end)."""

    start: float
    end: float
    candidate: Candidate
    source_start: float


@dataclass
class Placement:
    start: float
    end: float
    candidate: Candidate
    source_start: float
    score: float
    parts: dict[str, float] = field(default_factory=dict)
    accent: dict[str, Any] | None = None
    fixed: bool = False

    @property
    def source_end(self) -> float:
        return self.source_start + (self.end - self.start)


@dataclass
class _State:
    score: float
    placement: Placement | None
    previous: "_State | None"
    units: frozenset[str]
    scenes: dict[str, int]
    films: dict[str, int]
    looks: dict[int, int] = field(default_factory=dict)
    cast: tuple[int, int] | None = None        # (act index, cast rank) of the latest cast shot

    def path(self) -> list[Placement]:
        items, state = [], self
        while state is not None and state.placement is not None:
            items.append(state.placement)
            state = state.previous
        return items[::-1]


def _frame(time: float, origin: float, fps: int) -> float:
    return origin + round((time - origin) * fps) / fps


def flash_division(music: MusicMap) -> int:
    """Shots per beat in a flash: 1, 2 or 4, whichever makes a shot nearest a quarter second."""
    period = music.beat_period()
    return min((1, 2, 4), key=lambda division: abs(math.log(period / division / _FLASH_STEP_S)))


def grid(music: MusicMap, start: float, end: float, pace: str, fps: int = 24) -> list[float]:
    """Candidate cut times inside [start, end], frame-snapped, including both ends."""
    beats = [float(t) for t in music.beats if start < t < end]
    points = set(beats)
    if pace == "flash":
        # A steady pulse: every subdivision of every beat, never the irregular accents.
        division = flash_division(music)
        full = [float(t) for t in music.beats]
        for a, b in zip(full, full[1:]):
            points.update(t for t in (a + k * (b - a) / division for k in range(1, division)) if start < t < end)
        points = {t for t in points if end - t >= FLASH[0]}
    elif pace in ("kinetic", "rapid"):
        full = [float(t) for t in music.beats]
        points.update((a + b) / 2 for a, b in zip(full, full[1:]) if start < (a + b) / 2 < end)
        points.update(accent["time"] for accent in music.accents
                      if accent["strength"] >= _STRONG_ACCENT and start < accent["time"] < end)
    snapped = sorted({_frame(t, music.start, fps) for t in points} | {start, end})
    merged: list[float] = []
    for t in snapped:                       # keep one point per 40 ms, preferring the earlier (beat-side) one
        if merged and t - merged[-1] < 0.04 and t != end:
            continue
        merged.append(t)
    if merged[-1] != end:
        merged.append(end)
    return merged


def pace_penalty(duration: float, act: Act, energy: float) -> float:
    """0 at the act's target duration, increasingly negative away from it (log scale).

    The four paces scale their target with intensity (intense passages cut
    faster); a flash or a hold keeps its shape.
    """
    target = bounds(act)[1]
    if act.pace in PACE:
        target *= (1.6 - 0.9 * energy) * act.pace_scale
    return -(math.log(max(duration, 1e-3) / target) ** 2) / (2 * 0.45 ** 2)


def _segments(candidate: Candidate) -> list[tuple[float, float]]:
    """Usable source intervals: inside the focus span (the picture the evidence describes, so never
    across a dissolve), between hidden cuts, minus near-black stretches (fades)."""
    low, high = candidate.focus or (candidate.t_start, candidate.t_end)
    cuts = sorted(t for t in candidate.hidden_cuts if low < t < high)
    bounds = [low, *cuts, high]
    segments = list(zip(bounds, bounds[1:]))
    for dark_start, dark_end in sorted(candidate.dark_spans):
        kept = []
        for start, end in segments:
            if dark_end <= start or dark_start >= end:
                kept.append((start, end))
                continue
            if dark_start > start:
                kept.append((start, dark_start))
            if dark_end < end:
                kept.append((dark_end, end))
        segments = kept
    return [(start, end) for start, end in segments if end > start]


def span_context(music: MusicMap, start: float, end: float) -> tuple[dict[str, Any] | None, float]:
    """The span's strongest inner accent and its intensity (shared by every candidate)."""
    return music.strongest_accent(start + _CUT_GUARD_S, end - _CUT_GUARD_S), music.intensity(start, end)


def place(candidate: Candidate, start: float, end: float, act: Act, motion_rank: dict[str, float],
          context: tuple[dict[str, Any] | None, float]) -> Placement | None:
    """Best source window for one candidate on one span, or None when it cannot fit."""
    duration = end - start
    segments = [(a, b) for a, b in _segments(candidate) if b - a >= duration - 1e-6]
    if not segments:
        return None
    accent, energy = context
    peak = candidate.peak_time
    anchor_time = peak if peak is not None else candidate.hero_time
    if anchor_time is not None:
        segment = min(segments, key=lambda seg: 0 if seg[0] <= anchor_time <= seg[1]
                      else min(abs(anchor_time - seg[0]), abs(anchor_time - seg[1])))
    else:
        segment = max(segments, key=lambda seg: seg[1] - seg[0])
    low, high = segment[0], segment[1] - duration
    alignment = 0.0
    if peak is not None and accent is not None:
        offset = accent["time"] - start
        source = min(max(peak - offset, low), high)
        miss = abs(source + offset - peak)
        alignment = accent["strength"] * max(0.0, 1.0 - miss / _ACCENT_WINDOW_S)
    elif peak is not None:
        source = min(max(peak - 0.25 * duration, low), high)
        miss = abs(source + 0.25 * duration - peak)
        alignment = 0.5 * max(0.0, 1.0 - miss / max(0.5 * duration, 1e-3))
    elif anchor_time is not None:
        source = min(max(anchor_time - duration / 2, low), high)
    else:
        source = low + (high - low) / 2
    parts = {"relevance": candidate.relevance, "craft": candidate.craft}
    if candidate.unit_id in motion_rank:
        parts["motion"] = 1.0 - abs(energy - motion_rank[candidate.unit_id])
    quality = sum(QUALITY[key] * value for key, value in parts.items())
    if candidate.cast_rank is not None:
        parts["cast"] = _CAST_BONUS + (_PEAK_BONUS if candidate.cast_peak else 0.0)
        quality += parts["cast"]
    if act.fame == "anchor":
        parts["fame"] = 0.3 * candidate.fame + 0.2 * candidate.iconic
    elif act.fame == "fresh":
        parts["fame"] = 0.25 * candidate.gem - 0.25 * candidate.iconic - 0.1 * candidate.fame
    else:
        parts["fame"] = 0.1 * candidate.fame
    quality += parts["fame"]
    parts["alignment"] = alignment
    parts["pace"] = pace_penalty(duration, act, energy)
    score = quality * duration / bounds(act)[1] + ALIGNMENT * alignment + PACE_PENALTY * parts["pace"]
    return Placement(start, end, candidate, round(source, 4), score, parts,
                     accent if alignment > 0 and accent is not None else None)


def _direction(candidate: Candidate, left: float, right: float, *, at_end: bool) -> float:
    """Screen direction (-1 left, +1 right, 0 unknown) from camera pans or subject travel."""
    label = candidate.camera_at(right - 0.5, right) if at_end else candidate.camera_at(left, left + 0.5)
    if label in ("pan_left",):
        return 1.0        # the camera pans left: the scene moves right on screen
    if label in ("pan_right",):
        return -1.0
    if candidate.subject_start and candidate.subject_end:
        travel = candidate.subject_end[0] - candidate.subject_start[0]
        if abs(travel) > 0.08:
            return 1.0 if travel > 0 else -1.0
    return 0.0


def transition(previous: Placement | None, following: Placement, match: float | None = None,
               match_weight: float = 0.0) -> tuple[float, dict[str, float]]:
    """Cut quality from ``previous`` into ``following``; ``match`` is a measured match-cut score in [0, 1]."""
    if previous is None:
        return 0.0, {}
    a, b = previous.candidate, following.candidate
    parts: dict[str, float] = {}
    if a.scene_id and a.scene_id == b.scene_id:
        parts["same_scene"] = -0.6
    if a.film_id == b.film_id:
        parts["same_film"] = -0.15
        if a.subject_size and b.subject_size and abs(math.log(a.subject_size / b.subject_size)) < 0.3:
            parts["jump_cut"] = -0.05
    if match is not None:
        parts["match"] = match_weight * match
    elif a.subject_end and b.subject_start:
        distance = math.dist(a.subject_end, b.subject_start)
        parts["eye_trace"] = 0.12 * max(0.0, 1.0 - distance / 0.4)
    before = _direction(a, previous.source_start, previous.source_end, at_end=True)
    after = _direction(b, following.source_start, following.source_end, at_end=False)
    if before and after:
        parts["direction"] = 0.1 if before == after else -0.05
    if a.aspect and b.aspect and a.film_id != b.film_id:
        # Letterbox jumps (2.39:1 next to 4:3) read as a change of film stock.
        parts["aspect"] = -0.12 * min(1.0, abs(math.log(a.aspect / b.aspect)) / math.log(2.39 / 1.33))
    if a.grade and b.grade and a.film_id != b.film_id:
        jump = abs(a.grade[0] - b.grade[0]) / 0.3 + abs(a.grade[1] - b.grade[1]) / 0.3 + abs(a.grade[2] - b.grade[2]) / 0.15
        parts["grade"] = -0.04 * min(jump, 3.0)
    return sum(parts.values()), parts


def similarity_penalty(state: "_State", option: Placement) -> float:
    """Cost of looking like the recent shots (weighted by recency)."""
    vector = option.candidate.vector
    if vector is None:
        return 0.0
    total, node = 0.0, state
    for weight in _RECENCY:
        if node is None or node.placement is None:
            break
        other = node.placement.candidate.vector
        if other is not None:
            excess = float(vector @ other) - _SIMILAR_FROM
            if excess > 0:
                total += weight * min(excess / _SIMILAR_SCALE, 1.5)
        node = node.previous
    return -_SIMILAR_COST * total


def looks(candidates: Iterable[Candidate], count: int, iterations: int = 12) -> dict[str, int]:
    """Cluster candidates by image embedding (spherical k-means); unit_id -> look."""
    unique = {c.unit_id: c.vector for c in candidates if c.vector is not None}
    if len(unique) <= count:
        return {unit: index for index, unit in enumerate(sorted(unique))}
    units = sorted(unique)
    matrix = np.stack([unique[unit] for unit in units]).astype(np.float32)
    centers = [matrix[0]]                               # deterministic farthest-point initialisation
    closest = matrix @ centers[0]
    for _ in range(count - 1):
        choice = int(np.argmin(closest))
        centers.append(matrix[choice])
        closest = np.maximum(closest, matrix @ matrix[choice])
    centers = np.stack(centers)
    for _ in range(iterations):
        assignment = np.argmax(matrix @ centers.T, axis=1)
        for index in range(count):
            members = matrix[assignment == index]
            if len(members):
                mean = members.mean(axis=0)
                centers[index] = mean / (np.linalg.norm(mean) or 1.0)
    assignment = np.argmax(matrix @ centers.T, axis=1)
    return {unit: int(look) for unit, look in zip(units, assignment)}


def _motion_ranks(pool: Iterable[Candidate]) -> dict[str, float]:
    """Percentile of measured activity (subject motion plus camera movement) within an act's pool."""
    items = [c for c in pool if c.camera_segments or c.motion]
    if not items:
        return {}
    activity = np.array([c.motion * 10 + (0.5 if c.camera_reliability >= 0.5 and c.camera not in (None, "static") else 0.0)
                         for c in items])
    order = np.argsort(np.argsort(activity, kind="stable"), kind="stable") / max(1, len(items) - 1)
    return {c.unit_id: float(rank) for c, rank in zip(items, order)}


def assemble(music: MusicMap, acts: list[Act], *, fixed: Iterable[Fixed] = (), fps: int = 24,
             exclude_units: Iterable[str] = (), beam: int = _BEAM, per_span: int = _PER_SPAN,
             boundaries: list[float] | None = None,
             overrides: dict[tuple[float, float], Act] | None = None, matcher: Any = None) -> list[Placement]:
    """Best edit over the passage as consecutive placements (fixed shots included).

    ``boundaries`` pins every cut (fill mode): each span between consecutive
    boundaries takes exactly one shot and pace bounds are not applied.
    ``overrides`` gives one pinned span its own act (a slot with its own search).
    ``matcher`` (``matchcuts.CutMatcher``) scores every cut on its actual frames.
    """
    fixed = sorted(fixed, key=lambda item: item.start)
    acts = sorted(acts, key=lambda act: act.start)
    if not acts:
        raise ValueError("Assembly needs at least one act")
    excluded = set(exclude_units)
    overrides = {(round(a, 4), round(b, 4)): act for (a, b), act in (overrides or {}).items()}
    ranks = [_motion_ranks(act.pool) for act in acts]
    override_ranks = {key: _motion_ranks(act.pool) for key, act in overrides.items()}
    span = acts[-1].end - acts[0].start
    expected = sum((act.end - act.start) / bounds(act)[1] for act in acts)
    look_of = looks((c for act in [*acts, *overrides.values()] for c in act.pool),
                    max(6, min(40, round(expected / 3)))) if span > 0 else {}

    def act_at(time: float) -> int:
        return next((i for i, act in enumerate(acts) if act.start - 1e-6 <= time < act.end - 1e-6), len(acts) - 1)

    if boundaries is not None:
        points = sorted(set(boundaries))
    else:
        edges = {f.start for f in fixed} | {f.end for f in fixed}
        acts_edges = {act.start for act in acts} | {act.end for act in acts}
        points = sorted({p for act in acts for p in grid(music, act.start, act.end, act.pace, fps)
                         if p in acts_edges or not any(abs(p - edge) < 0.25 for edge in edges)} | edges)
    fixed_at = {round(f.start, 4): f for f in fixed}
    blocked = [(f.start, f.end) for f in fixed]
    states: dict[float, list[_State]] = {points[0]: [_State(0.0, None, None, frozenset(), {}, {})]}
    cache: dict[tuple[str, float, float], Placement | None] = {}
    contexts: dict[tuple[float, float], tuple[dict[str, Any] | None, float]] = {}
    for index, node in enumerate(points[:-1]):
        frontier = states.pop(node, [])
        if not frontier:
            continue
        spans = _spans(points, index, acts[act_at(node)], fixed_at, blocked, boundaries is not None)
        for end, fixed_shot in spans:
            span_act = acts[act_at(node)]
            if fixed_shot is not None:
                options = [Placement(node, end, fixed_shot.candidate, fixed_shot.source_start, 0.0, {}, None, True)]
            else:
                act_index = act_at(node)
                act = acts[act_index]
                rank = ranks[act_index]
                special = overrides.get((round(node, 4), round(end, 4)))
                if special is not None:
                    act, rank = special, override_ranks[(round(node, 4), round(end, 4))]
                span_act = act
                options = []
                context = contexts.get((node, end))
                if context is None:
                    context = contexts[(node, end)] = span_context(music, node, end)
                for candidate in act.pool:
                    if candidate.unit_id in excluded:
                        continue
                    key = (candidate.unit_id, node, end)
                    if key not in cache:
                        cache[key] = place(candidate, node, end, act, rank, context)
                    if cache[key] is not None:
                        options.append(cache[key])
                options.sort(key=lambda p: -p.score)
                options = options[:per_span * 3]
            act_index = act_at(node)
            for state in frontier:
                taken = 0
                matches = cut_matches(matcher, state.placement, options, fps)
                for position, option in enumerate(options):
                    unit = option.candidate.unit_id
                    if unit in state.units and not option.fixed:
                        continue
                    rank = option.candidate.cast_rank
                    if rank is not None and state.cast is not None and state.cast[0] == act_index and rank <= state.cast[1]:
                        continue                  # cast shots keep their order within the act
                    step, _ = transition(state.placement, option, matches[position] if matches is not None else None,
                                         matcher.strength if matcher is not None else 0.0)
                    scene = option.candidate.scene_id
                    reuse = -0.3 * state.scenes.get(scene, 0) if scene else 0.0
                    # Film and look reuse are about screen time, so they scale with duration:
                    # otherwise every extra cut costs more and the search drifts to fewer, longer shots.
                    share = (end - node) / bounds(span_act)[1]
                    overuse = -_FILM_REUSE * state.films.get(option.candidate.film_id, 0) * share
                    look = look_of.get(unit)
                    variety = similarity_penalty(state, option)
                    if span_act.pace == "flash":
                        # A flash is one idea: shots that look alike carry it; a new look breaks it.
                        repeat, variety = 0.0, -0.5 * variety
                    else:
                        repeat = -_CLUSTER_REUSE * state.looks.get(look, 0) * share if look is not None else 0.0
                    total = state.score + option.score + step + reuse + overuse + repeat + variety
                    bucket = states.setdefault(end, [])
                    if len(bucket) >= beam and total <= min(item.score for item in bucket):
                        taken += 1
                        if taken >= per_span and not option.fixed:
                            break
                        continue
                    scenes = dict(state.scenes)
                    if scene:
                        scenes[scene] = scenes.get(scene, 0) + 1
                    films = dict(state.films)
                    films[option.candidate.film_id] = films.get(option.candidate.film_id, 0) + 1
                    seen = dict(state.looks)
                    if look is not None:
                        seen[look] = seen.get(look, 0) + 1
                    cast = (act_index, rank) if rank is not None else state.cast
                    _push(bucket, _State(total, option, state, state.units | {unit}, scenes, films, seen, cast), beam)
                    taken += 1
                    if taken >= per_span and not option.fixed:
                        break
    finals = states.get(points[-1]) or []
    if not finals:
        raise ValueError("No complete edit fits these acts; widen the searches or the pace")
    return max(finals, key=lambda state: state.score).path()


def cut_matches(matcher: Any, previous: Placement | None, options: list[Placement], fps: int = 24) -> np.ndarray | None:
    """Measured match of the previous shot's last frame against each option's first frame (None without a matcher)."""
    if matcher is None or previous is None:
        return None
    out_row = matcher.row(previous.candidate.unit_id, previous.source_end - 1.0 / fps)
    if out_row is None:
        return None
    return matcher.scores(out_row, [matcher.row(option.candidate.unit_id, option.source_start) for option in options])


def _spans(points: list[float], index: int, act: Act, fixed_at: dict[float, Fixed], blocked: list[tuple[float, float]],
           pinned: bool) -> list[tuple[float, Fixed | None]]:
    """Reachable span ends from one node.

    A span may only end where what remains before the next hard boundary (the
    act's end or a fixed shot) is nothing or at least the pace minimum, so the
    search never walks into a node that forces a too-short shot.
    """
    node = points[index]
    shot = fixed_at.get(round(node, 4))
    if shot is not None:
        return [(shot.end, shot)]
    if pinned or act.pace == "flash":           # a flash steps through its pulse one shot at a time
        return [(points[index + 1], None)]
    low, _target, high = bounds(act)
    limit = min([act.end] + [a for a, _b in blocked if a >= node - 1e-6])
    spans = []
    for end in points[index + 1:]:
        duration = end - node
        if end > limit + 1e-6 or duration > high + 1e-6:
            break
        remainder = limit - end
        if duration >= low - 1e-6 and (remainder <= 1e-6 or remainder >= low - 1e-6):
            spans.append((end, None))
    if not spans:
        # The gap to the boundary is shorter than the pace allows, or the grid
        # is sparser than its maximum: take the whole gap, or the next point.
        following = next((p for p in points[index + 1:] if p <= limit + 1e-6 and p - node >= low - 1e-6), None)
        spans.append((limit if limit - node <= high + 1e-6 or following is None else following, None))
    return spans


def _push(bucket: list[_State], state: _State, beam: int) -> None:
    for i, other in enumerate(bucket):        # one state per last shot: keep the better
        if other.placement is not None and state.placement is not None and \
                other.placement.candidate.unit_id == state.placement.candidate.unit_id:
            if state.score > other.score:
                bucket[i] = state
            return
    bucket.append(state)
    if len(bucket) > beam:
        bucket.sort(key=lambda item: -item.score)
        del bucket[beam:]


def alternatives(result: list[Placement], acts: list[Act], music: MusicMap, *, count: int = 5,
                 matcher: Any = None) -> list[list[Placement]]:
    """For each placement, the best other candidates for the same span given its neighbours."""
    used = {p.candidate.unit_id for p in result}
    options: list[list[Placement]] = []
    for index, chosen in enumerate(result):
        if chosen.fixed:
            options.append([])
            continue
        act_index = next((i for i, act in enumerate(acts) if act.start - 1e-6 <= chosen.start < act.end - 1e-6), 0)
        act = acts[act_index]
        ranks = _motion_ranks(act.pool)
        context = span_context(music, chosen.start, chosen.end)
        previous = result[index - 1] if index else None
        following = result[index + 1] if index + 1 < len(result) else None
        scored = []
        for candidate in act.pool:
            if candidate.unit_id in used:
                continue
            placement = place(candidate, chosen.start, chosen.end, act, ranks, context)
            if placement is None:
                continue
            weight = matcher.strength if matcher is not None else 0.0
            into = cut_matches(matcher, previous, [placement])
            total = placement.score + transition(previous, placement, None if into is None else float(into[0]), weight)[0]
            if following is not None:
                out = cut_matches(matcher, placement, [following])
                total += transition(placement, following, None if out is None else float(out[0]), weight)[0]
            total += _neighbour_similarity(placement, result, index)
            scored.append((total, placement))
        scored.sort(key=lambda item: -item[0])
        options.append([placement for _total, placement in scored[:count]])
    return options


def _neighbour_similarity(placement: Placement, result: list[Placement], index: int) -> float:
    """The variety cost of an alternative against the chosen shots around its slot."""
    vector = placement.candidate.vector
    if vector is None:
        return 0.0
    total = 0.0
    for distance, weight in enumerate(_RECENCY[:3], start=1):
        for neighbour in (index - distance, index + distance):
            if 0 <= neighbour < len(result) and result[neighbour].candidate.vector is not None:
                excess = float(vector @ result[neighbour].candidate.vector) - _SIMILAR_FROM
                if excess > 0:
                    total += weight * min(excess / _SIMILAR_SCALE, 1.5)
    return -_SIMILAR_COST * total


def reason(placement: Placement, previous: Placement | None, matcher: Any = None) -> str:
    """A short, factual explanation of why this shot sits here."""
    c = placement.candidate
    text = []
    what = c.action or c.caption[:140]
    if what:
        text.append(what.rstrip(".") + ".")
    if placement.accent is not None and c.peak_time is not None:
        text.append(f"Its peak lands on the accent at {placement.accent['time']:.2f}s.")
    if placement.parts.get("motion", 0) >= 0.8:
        text.append("Its movement matches the music's intensity here.")
    if previous is not None:
        match = cut_matches(matcher, previous, [placement])
        _, parts = transition(previous, placement, None if match is None else float(match[0]), 1.0)
        if parts.get("match", 0) >= 0.6:
            text.append("It cuts in on a frame that matches the previous shot's last one.")
        elif parts.get("eye_trace", 0) >= 0.08:
            text.append("The subject continues from where the previous shot left the eye.")
        if parts.get("direction", 0) > 0:
            text.append("Screen direction continues across the cut.")
    if c.iconic:
        text.append("A recognizable moment.")
    elif c.gem:
        text.append("A lesser-known, well-crafted shot.")
    return " ".join(text)[:600] or "Chosen for relevance and timing."
