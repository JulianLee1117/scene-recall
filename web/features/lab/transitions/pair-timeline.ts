import { DEFAULT_RETIME, retimedDuration, retimedFrames, validateRetime, type SourceSelection, type TransitionRecipe, type TransitionRetime } from "./transitions";

export type PairSide = "a" | "b";
export type TrimEdge = "source_start" | "source_end";
export interface PairPosition { side: PairSide; sourceTime: number; }
export interface ClipTiming {
  source: SourceSelection;
  start: number;
  duration: number;
  sourceKnots: number[];
  outputKnots: number[];
}
export interface PairLayout {
  a: ClipTiming | null;
  b: ClipTiming | null;
  duration: number;
  overlap: number;
  overlapStart: number;
  overlapEnd: number;
  maxOverlapFrames: number;
  approximate: boolean;
}
export const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));
const validSource = (source: SourceSelection | null): source is SourceSelection => !!source
  && Number.isFinite(source.source_start) && Number.isFinite(source.source_end)
  && source.source_start >= 0 && source.source_end > source.source_start;

function timing(source: SourceSelection | null, side: PairSide, settings: TransitionRetime): ClipTiming | null {
  if (!validSource(source)) return null;
  const length = source.source_end - source.source_start;
  const active = settings.mode !== "off" && settings.speed !== 1;
  if (!active) return { source, start: 0, duration: retimedFrames(length, settings) / 30, sourceKnots: [0, length], outputKnots: [0, length] };
  const span = Math.min(settings.span, length), begin = side === "a" ? length - span : 0;
  const sourceKnots = [0], outputKnots = [0];
  if (begin > 0) { sourceKnots.push(begin); outputKnots.push(begin); }
  let elapsed = begin, previous = 1;
  for (let index = 0; index <= 2048; index++) {
    const phase = index / 2048;
    const weight = settings.mode === "pulse" ? 1 - Math.abs(2 * phase - 1) : side === "a" ? phase : 1 - phase;
    const eased = settings.curve === "snappy" ? weight < .5 ? 4 * weight ** 3 : 1 - (-2 * weight + 2) ** 3 / 2 : weight * weight * (3 - 2 * weight);
    const reciprocal = 1 / (1 + (settings.speed - 1) * eased);
    if (index) { elapsed += span / 2048 * (previous + reciprocal) / 2; sourceKnots.push(begin + phase * span); outputKnots.push(elapsed); }
    previous = reciprocal;
  }
  if (side === "b" && length > span) { sourceKnots.push(length); outputKnots.push(elapsed + length - span); }
  return { source, start: 0, duration: retimedFrames(length, settings) / 30, sourceKnots, outputKnots };
}

/** Draft output geometry; source inspection uses the nominal speed integral, not decoded frame PTS. */
export function pairLayout(a: SourceSelection | null, b: SourceSelection | null, recipe: TransitionRecipe | null, retime: TransitionRetime): PairLayout {
  const settings = validateRetime(retime) ? DEFAULT_RETIME : retime;
  const first = timing(a, "a", settings), second = timing(b, "b", settings);
  const maxOverlapFrames = first && second ? Math.min(60, Math.round(first.duration * 30) - 1, Math.round(second.duration * 30) - 1) : 0;
  const requested = recipe && recipe.id !== "hard-cut" && Number.isFinite(recipe.duration) ? Math.round(recipe.duration * 30) : 0;
  const overlap = first && second ? clamp(requested, 0, maxOverlapFrames) / 30 : 0;
  const overlapEnd = first?.duration ?? 0, overlapStart = Math.max(0, overlapEnd - overlap);
  if (second) second.start = first ? overlapStart : 0;
  return { a: first, b: second, duration: Math.max(0, (first?.duration ?? 0) + (second?.duration ?? 0) - overlap), overlap,
    overlapStart, overlapEnd, maxOverlapFrames, approximate: settings.mode !== "off" && settings.speed !== 1 };
}

function interpolate(value: number, inputs: number[], outputs: number[]): number {
  if (value <= inputs[0]) return outputs[0];
  const last = inputs.length - 1;
  if (value >= inputs[last]) return outputs[last];
  let low = 0, high = last;
  while (high - low > 1) { const middle = (low + high) >> 1; if (inputs[middle] <= value) low = middle; else high = middle; }
  const fraction = (value - inputs[low]) / (inputs[high] - inputs[low]);
  return outputs[low] + fraction * (outputs[high] - outputs[low]);
}

export function sequenceAtSourceTime(layout: PairLayout, position: PairPosition | null): number | null {
  if (!position || !Number.isFinite(position.sourceTime)) return null;
  const clip = layout[position.side];
  if (!clip) return null;
  return clip.start + clamp(interpolate(position.sourceTime - clip.source.source_start, clip.sourceKnots, clip.outputKnots), 0, clip.duration);
}

export function sourceAtSequenceTime(layout: PairLayout, sequenceTime: number, preferredSide?: PairSide | null): PairPosition | null {
  if (!Number.isFinite(sequenceTime)) return null;
  const time = clamp(sequenceTime, 0, layout.duration);
  const side = !layout.a ? "b" : !layout.b ? "a" : time < layout.overlapStart ? "a" : time > layout.overlapEnd ? "b"
    : preferredSide ?? (time < (layout.overlapStart + layout.overlapEnd) / 2 ? "a" : "b");
  return sourceAtClipTime(layout, side, time);
}

export function sourceAtClipTime(layout: PairLayout, side: PairSide, sequenceTime: number): PairPosition | null {
  const clip = layout[side];
  if (!clip || !Number.isFinite(sequenceTime)) return null;
  const sourceTime = clip.source.source_start + interpolate(sequenceTime - clip.start, clip.outputKnots, clip.sourceKnots);
  // The selected out point is exclusive. This is a browser inspection target, not a native-frame promise.
  return { side, sourceTime: clamp(sourceTime, clip.source.source_start, clip.source.source_end - 1e-6) };
}

export function timelineTrimLimits(source: SourceSelection, edge: TrimEdge, recipe: TransitionRecipe | null, retime: TransitionRetime, filmDuration = Infinity): { min: number; max: number } | null {
  if (!validSource(source) || validateRetime(retime)) return null;
  const span = retime.mode === "off" ? 0 : retime.span;
  const base = Math.max(.2, span);
  const overlapFrames = recipe && recipe.id !== "hard-cut" && Number.isFinite(recipe.duration) ? Math.round(recipe.duration * 30) : 0;
  // Once the full source ramp fits, extending the source changes output duration one-for-one.
  const minimum = Math.max(base, (overlapFrames + .5) / 30 - (retimedDuration(base, retime) - base) + 1e-7);
  const bounds = edge === "source_start"
    ? { min: Math.max(0, source.source_end - 12), max: source.source_end - minimum }
    : { min: source.source_start + minimum, max: Math.min(source.source_start + 12, Number.isFinite(filmDuration) ? filmDuration : Infinity) };
  return bounds.max < bounds.min ? null : bounds;
}

export function timelineTrim(source: SourceSelection, edge: TrimEdge, time: number, recipe: TransitionRecipe | null, retime: TransitionRetime, filmDuration = Infinity): number | null {
  const bounds = timelineTrimLimits(source, edge, recipe, retime, filmDuration);
  return bounds && Number.isFinite(time) ? clamp(Math.round(clamp(time, bounds.min, bounds.max) * 1000) / 1000, bounds.min, bounds.max) : null;
}

export function timelineDuration(layout: PairLayout, seconds: number): number | null {
  return Number.isFinite(seconds) && layout.maxOverlapFrames >= 3 ? clamp(Math.round(seconds * 30), 3, layout.maxOverlapFrames) / 30 : null;
}
