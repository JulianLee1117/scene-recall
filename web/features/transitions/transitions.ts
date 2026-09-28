import type { SearchResult } from "@/types/api";

export type RecipeId = string;
export interface TransitionRecipe {
  [key: string]: string | number | boolean | undefined;
  id: RecipeId;
  duration: number;
  direction: "left" | "right" | "up" | "down";
  easing: "linear" | "smooth" | "snappy";
  intensity: number;
  softness: number;
}
export interface SourceFraming {
  fit: "fit" | "fill";
  anchor_x: number;
  anchor_y: number;
  zoom: number;
}
export const DEFAULT_FRAMING: SourceFraming = { fit: "fit", anchor_x: 0.5, anchor_y: 0.5, zoom: 1 };
export interface TransitionSource {
  film_id: string;
  unit_id?: string | null;
  source_start: number;
  source_end: number;
  framing?: SourceFraming;
}
export interface SourceSelection extends TransitionSource {
  title: string;
}
export interface SourceEndpoint { url: string; time: number; }
export interface TransitionRetime {
  mode: "off" | "rush" | "slow-hit" | "pulse";
  speed: number;
  /** Source seconds affected on each side of the cut. */
  span: number;
  curve: "smooth" | "snappy";
  interpolation: "nearest" | "blend" | "flow";
}
export const DEFAULT_RETIME: TransitionRetime = { mode: "off", speed: 1, span: .5, curve: "smooth", interpolation: "nearest" };
export const restoredRetime = (retime?: TransitionRetime): TransitionRetime => ({ ...DEFAULT_RETIME, ...retime });
export function sameRetime(a?: TransitionRetime, b?: TransitionRetime): boolean {
  const first = restoredRetime(a), second = restoredRetime(b);
  if (first.mode === "off" && second.mode === "off") return true;
  return (Object.keys(DEFAULT_RETIME) as (keyof TransitionRetime)[]).every((key) => first[key] === second[key]);
}
export function speedTitle(retime?: TransitionRetime): string {
  const settings = restoredRetime(retime);
  if (settings.mode === "off") return "Original speed";
  const smoothing = settings.interpolation === "blend" ? " · frame blend" : settings.interpolation === "flow" ? " · optical flow" : "";
  return `${({ rush: "Rush", "slow-hit": "Slow hit", pulse: "Pulse" })[settings.mode]} ${Number(settings.speed.toFixed(2))}×${smoothing}`;
}
export function validateRetime(retime?: TransitionRetime): string | null {
  if (retime === undefined) return null;
  if (!retime || typeof retime !== "object" || Array.isArray(retime) || Object.keys(retime).some((key) => !Object.hasOwn(DEFAULT_RETIME, key))) return "Choose valid speed settings.";
  const settings = restoredRetime(retime);
  if (!["off", "rush", "slow-hit", "pulse"].includes(settings.mode) || !["smooth", "snappy"].includes(settings.curve)
    || !["nearest", "blend", "flow"].includes(settings.interpolation)) return "Choose a supported speed shape, curve and smoothing method.";
  if (!Number.isFinite(settings.span) || settings.span < .1 || settings.span > 2) return "The speed ramp window must be 0.1–2 source seconds per clip.";
  if (!Number.isFinite(settings.speed) || (settings.mode === "slow-hit" ? settings.speed < .25 || settings.speed > 1 : settings.speed < 1 || settings.speed > 4)) return "Choose a speed within this shape’s range.";
  if (settings.mode === "off" && (settings.speed !== 1 || settings.interpolation !== "nearest")) return "Speed off requires original speed with frame sampling.";
  return null;
}

/** Mirror backend retiming.plan's 2048 source-time trapezoids; quantization is separate. */
export function retimedDuration(sourceDuration: number, retime?: TransitionRetime): number {
  const settings = restoredRetime(retime);
  if (settings.mode === "off" || settings.speed === 1) return sourceDuration;
  const span = Math.min(settings.span, sourceDuration), steps = 2048, step = span / steps;
  let total = sourceDuration - span, previous = 1;
  for (let index = 0; index <= steps; index++) {
    const phase = index / steps;
    const weight = settings.mode === "pulse" ? 1 - Math.abs(2 * phase - 1) : phase;
    const eased = settings.curve === "snappy" ? weight < .5 ? 4 * weight ** 3 : 1 - (-2 * weight + 2) ** 3 / 2 : weight * weight * (3 - 2 * weight);
    const reciprocal = 1 / (1 + (settings.speed - 1) * eased);
    if (index > 0) total += step * (previous + reciprocal) / 2;
    previous = reciprocal;
  }
  return total;
}
export const retimedFrames = (sourceDuration: number, retime?: TransitionRetime): number => Math.max(2, Math.floor(retimedDuration(sourceDuration, retime) * 30 + .5));
export interface TransitionRequest {
  outgoing: TransitionSource;
  incoming: TransitionSource;
  recipe: TransitionRecipe;
  retime?: TransitionRetime;
  output: { aspect: "landscape" | "portrait" | "square"; quality: "draft" | "high" | "export" };
}
export interface RecipeDefinition {
  id: RecipeId;
  name: string;
  description: string;
  defaults: TransitionRecipe;
  controls: (keyof TransitionRecipe)[];
  group?: string;
  control_specs?: ControlSpec[];
}
export interface ControlSpec {
  key: string;
  label: string;
  type: "range" | "select";
  min?: number;
  max?: number;
  step?: number;
  unit?: string;
  advanced?: boolean;
  group?: string;
  options?: ({ value: string | number; label: string } | string)[];
}
export interface TransitionCatalog {
  renderer_version: string;
  recipes: RecipeDefinition[];
  limits: { max_clip_seconds: number; min_clip_seconds?: number; fps?: number; min_transition_seconds?: number; max_transition_seconds?: number };
}
export interface TransitionJob {
  id: string;
  reused?: boolean;
  storage?: { output_bytes: number; retained_bytes: number; original_bytes: number; scratch_bytes: number } | null;
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
  request: TransitionRequest;
  source_titles?: { outgoing: string; incoming: string };
  progress?: string | null;
  error?: string | null;
  cancel_requested?: boolean;
  created_at?: number | string;
  result?: {
    preview_url: string;
    manifest_url: string;
    frame_a_url: string;
    frame_b_url: string;
    frame_a_time: number;
    frame_b_time: number;
    duration: number;
    transition_start: number;
    transition_end: number;
    fps: number;
    renderer_version: string;
    recipe: TransitionRecipe;
    width: number;
    height: number;
    retiming?: {
      source_durations: number[];
      output_durations: number[];
      visible_cut: { requested_edge_or_peak_speed: number; at_picture_cut: { side: "outgoing" | "incoming"; source_time: number; nominal_speed: number; sample_grid_speed: number }[]; note: string };
    };
  } | null;
}

export function fileSize(bytes?: number): string | null {
  if (bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return null;
  if (bytes >= 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GiB`;
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  return `${Math.ceil(bytes / 1024)} KiB`;
}
export const qualityTitle = (quality: TransitionRequest["output"]["quality"]): string => ({ draft: "Draft · 480p", high: "Review · 720p", export: "Export · 1080p" })[quality];
export function renderFileSummary(job: TransitionJob): string {
  const result = job.result;
  return [result && Number.isFinite(result.width) && Number.isFinite(result.height) ? `${result.width}×${result.height}` : null,
    result && Number.isFinite(result.duration) ? `${result.duration.toFixed(2)}s` : null,
    fileSize(job.storage?.output_bytes)].filter(Boolean).join(" · ");
}

export function cutSpeedTitle(job: TransitionJob): string | null {
  if (restoredRetime(job.request.retime).mode === "off") return null;
  const samples = job.result?.retiming?.visible_cut?.at_picture_cut;
  const a = samples?.find((sample) => sample.side === "outgoing")?.sample_grid_speed;
  const b = samples?.find((sample) => sample.side === "incoming")?.sample_grid_speed;
  return a !== undefined && b !== undefined && Number.isFinite(a) && Number.isFinite(b) ? `At cut A ${a.toFixed(2)}× / B ${b.toFixed(2)}×` : null;
}

export const activeRender = (job: TransitionJob) => job.status === "queued" || job.status === "running";
export function mergeRenderHistory(current: TransitionJob[], incoming: TransitionJob[], preserveCurrent = false): TransitionJob[] {
  return [...current.filter((job) => !incoming.some((item) => item.id === job.id)), ...incoming.map((item) => {
    const previous = current.find((job) => job.id === item.id);
    if (!previous) return item;
    if (!activeRender(previous) && activeRender(item)) return previous;
    if (activeRender(previous) && !activeRender(item)) return item;
    return preserveCurrent ? previous : item;
  })].sort((a, b) => {
    const time = (job: TransitionJob) => typeof job.created_at === "number" ? job.created_at < 1e12 ? job.created_at * 1000 : job.created_at : Date.parse(job.created_at ?? "") || 0;
    return time(b) - time(a);
  });
}
export const recipeTitle = (id: string) => (({ "hard-cut": "Hard cut", "whip-pan": "Camera whip", "soft-wipe": "Soft swipe", "luma-reveal": "Luma reveal", "light-flash": "Clean exposure flash", "dip-to-black": "Dip to black", "cross-dissolve": "Linear-light dissolve", "crash-zoom": "Anchored crash zoom", "highlight-bloom": "Highlight kiss", "film-burn": "Organic edge burn", "lens-sweep": "Prismatic lens sweep", "shutter-flash": "Shutter double hit", "afterimage-flash": "Afterimage flash", "focus-pull": "Defocus bridge", "prism-push": "Prism push" } as Record<string, string>)[id] ?? id.replaceAll("-", " "));

export function sourceFromResult(result: SearchResult): SourceSelection {
  const reference = result.matched_frame_timestamp ?? result.t_start;
  const start = Math.max(result.t_start, Math.min(reference - 1.5, result.t_end - 3));
  return { film_id: result.film_id, unit_id: result.unit_id, title: result.film_title || result.film_id,
    source_start: start, source_end: Math.min(result.t_end, start + 3) };
}

export function sourcePayload(source: TransitionSource): TransitionSource {
  return { film_id: source.film_id, ...(source.unit_id ? { unit_id: source.unit_id } : {}), source_start: source.source_start, source_end: source.source_end,
    ...(source.framing ? { framing: { ...source.framing } } : {}) };
}

export function validatePair(a: TransitionSource | null, b: TransitionSource | null, recipe: TransitionRecipe | null, maxSeconds = 12, retime?: TransitionRetime): string | null {
  if (!a || !b) return "Choose both clips to render a transition.";
  const speedProblem = validateRetime(retime);
  if (speedProblem) return speedProblem;
  const speed = restoredRetime(retime);
  if (recipe && recipe.id !== "hard-cut" && (!Number.isFinite(recipe.duration) || recipe.duration < .1 || recipe.duration > 2))
    return "Transition duration must be between 3 and 60 frames (0.10–2 seconds).";
  for (const [label, source] of [["A", a], ["B", b]] as const) {
    const duration = source.source_end - source.source_start;
    if (!Number.isFinite(source.source_start) || !Number.isFinite(source.source_end) || source.source_start < 0 || duration <= 0)
      return `Clip ${label} needs a valid in and out time.`;
    if (duration > maxSeconds + 1e-6) return `Keep clip ${label} within ${maxSeconds} seconds for this experiment.`;
    if (duration < .2 - 1e-6) return `Clip ${label} needs at least 0.2 seconds of footage.`;
    if (source.framing && ( ![source.framing.anchor_x, source.framing.anchor_y].every((value) => Number.isFinite(value) && value >= 0 && value <= 1)
      || !Number.isFinite(source.framing.zoom) || source.framing.zoom < 1 || source.framing.zoom > 2 || !["fit", "fill"].includes(source.framing.fit)))
      return `Clip ${label} needs valid framing settings.`;
    if (speed.mode !== "off" && speed.span > duration + 1e-8) return `The speed ramp window exceeds clip ${label}. Shorten the ramp or extend the clip.`;
    if (recipe && recipe.id !== "hard-cut" && Math.round(recipe.duration * 30) >= retimedFrames(duration, retime))
      return speed.mode === "off" ? `The transition must be shorter than clip ${label}. Extend the clip or shorten the transition.`
        : `The transition must be shorter than clip ${label} after speed changes (${retimedFrames(duration, retime)} frames). Shorten the transition or adjust Speed.`;
  }
  return recipe ? null : "Choose a transition.";
}

/** Comparisons require exactly the same source windows and framing, independent of recipe/quality. */
export function samePair(a: TransitionRequest, b: TransitionRequest): boolean {
  return a.output.aspect === b.output.aspect && (["outgoing", "incoming"] as const).every((key) =>
    a[key].film_id === b[key].film_id && a[key].source_start === b[key].source_start && a[key].source_end === b[key].source_end &&
    (Object.keys(DEFAULT_FRAMING) as (keyof SourceFraming)[]).every((field) => (a[key].framing ?? DEFAULT_FRAMING)[field] === (b[key].framing ?? DEFAULT_FRAMING)[field]));
}

/** Native evidence has no lab crop; each source window can keep its own still while the current framing changes. */
export function matchingSourceEndpoints(job: TransitionJob | null, working: TransitionRequest | null): { a?: SourceEndpoint; b?: SourceEndpoint } | null {
  if (!job || job.status !== "completed" || !job.result || !working) return null;
  const endpoints: { a?: SourceEndpoint; b?: SourceEndpoint } = {};
  for (const [side, key] of [["outgoing", "a"], ["incoming", "b"]] as const) {
    const saved = job.request[side], source = working[side];
    const url = key === "a" ? job.result.frame_a_url : job.result.frame_b_url;
    const time = key === "a" ? job.result.frame_a_time : job.result.frame_b_time;
    if (saved.film_id !== source.film_id || saved.source_start !== source.source_start || saved.source_end !== source.source_end
      || !Number.isFinite(source.source_start) || !Number.isFinite(source.source_end) || source.source_start < 0 || source.source_end <= source.source_start
      || typeof url !== "string" || !url.trim() || !Number.isFinite(time) || time < source.source_start - 1e-8 || time >= source.source_end) continue;
    endpoints[key] = { url, time };
  }
  return endpoints.a || endpoints.b ? endpoints : null;
}

export function restoredRecipe(saved: TransitionRecipe, definition: RecipeDefinition): TransitionRecipe {
  const recipe = { ...definition.defaults };
  for (const key of Object.keys(recipe)) if (saved[key] !== undefined) recipe[key] = saved[key];
  if (recipe.id !== "hard-cut") recipe.duration = Math.max(3, Math.min(60, Math.round(recipe.duration * 30))) / 30;
  return recipe;
}

/** Compare controls that affect this recipe; unused fields do not make the preview stale. */
export function workingChanges(saved: TransitionRequest, working: TransitionRequest | null, definition?: RecipeDefinition): string[] {
  if (!working) return ["Source pair not loaded"];
  const changes: string[] = [];
  if (!samePair({ ...saved, output: working.output }, working)) changes.push("Source pair");
  if (!sameRetime(saved.retime, working.retime)) changes.push("Speed");
  if (saved.recipe.id !== working.recipe.id) changes.push("Effect");
  else {
    if (Math.round(saved.recipe.duration * 30) !== Math.round(working.recipe.duration * 30)) changes.push("Timing");
    const controls = definition?.controls ?? Object.keys(working.recipe);
    if (controls.some((key) => key !== "duration" && key !== "id" && (saved.recipe[key] ?? definition?.defaults[key]) !== working.recipe[key])) changes.push("Shape");
  }
  if (saved.output.aspect !== working.output.aspect || saved.output.quality !== working.output.quality) changes.push("Output");
  return changes;
}

export const variantTiming = (recipe: TransitionRecipe) => recipe.id === "hard-cut" ? "0f" : `${Math.round(recipe.duration * 30)}f`;

export function sourceScrubBounds(source: TransitionSource, filmDuration = Infinity): { start: number; end: number } {
  const upper = Number.isFinite(filmDuration) && filmDuration > 0 ? filmDuration : Infinity;
  const start = Math.min(Math.max(0, upper - .01), Math.max(0, Number.isFinite(source.source_start) ? source.source_start - 1 : 0));
  return { start, end: Math.min(upper, Math.max(start + .01, Number.isFinite(source.source_end) ? source.source_end + 1 : start + 1)) };
}

/** Browser seeks near a GOP boundary are approximate; this is a nearby preview, not a native endpoint. */
export function sourceInspectionTime(source: TransitionSource, outgoing: boolean): number {
  return outgoing ? Math.max(source.source_start, source.source_end - .1) : source.source_start;
}

/** A trim adjustment never moves the other endpoint or silently changes the selected window. */
export function sourceBoundary(source: TransitionSource, boundary: "source_start" | "source_end", time: number, filmDuration = Infinity): TransitionSource | null {
  if (!Number.isFinite(time)) return null;
  const next = { ...source, [boundary]: Math.round(time * 1000) / 1000 };
  const length = next.source_end - next.source_start;
  if (!Number.isFinite(length) || next.source_start < 0 || next.source_end > filmDuration + 1e-6 || length < .2 - 1e-6 || length > 12 + 1e-6) return null;
  return next;
}

export function beatFrames(bpm: number, beats: number, fps = 30): number {
  if (!Number.isFinite(bpm) || bpm <= 0 || !Number.isFinite(beats) || beats <= 0) return 0;
  return Math.max(3, Math.min(60, Math.round(60 / bpm * beats * fps)));
}

/** At most three legal timings; never silently alters source windows or framing. */
export function timingSweep(request: TransitionRequest, maxSeconds = 12): TransitionRequest[] {
  if (request.recipe.id === "hard-cut") return [];
  const frames = Math.round(request.recipe.duration * 30);
  const unique = [...new Set([frames - 2, frames, frames + 2].map((value) => Math.max(3, Math.min(60, value))))];
  return unique.map((count) => ({ ...request, recipe: { ...request.recipe, duration: count / 30 } }))
    .filter((variant) => !validatePair(variant.outgoing, variant.incoming, variant.recipe, maxSeconds, variant.retime));
}

export function seamCenter(job: TransitionJob): number {
  return job.result ? (job.result.transition_start + job.result.transition_end) / 2 : 0;
}
export function phaseToMedia(job: TransitionJob, phase: number): number {
  if (!job.result) return 0;
  return Math.max(0, Math.min(Math.max(0, job.result.duration - 1 / job.result.fps), seamCenter(job) + phase));
}
export function comparisonBounds(jobs: TransitionJob[], seamOnly: boolean): { start: number; end: number } {
  const ready = jobs.filter((job) => job.result);
  if (!ready.length) return { start: 0, end: 0 };
  const start = Math.min(...ready.map((job) => -seamCenter(job)));
  const end = Math.max(...ready.map((job) => job.result!.duration - 1 / job.result!.fps - seamCenter(job)));
  const halfOverlap = Math.max(...ready.map((job) => (job.result!.transition_end - job.result!.transition_start) / 2));
  return seamOnly ? { start: Math.max(start, -halfOverlap - .65), end: Math.min(end, halfOverlap + .65) } : { start, end };
}

export function handoffReceipt(job: TransitionJob, model: string, prompt: string) {
  if (job.status !== "completed" || !job.result) throw new Error("Render a source pair before exporting an AI handoff.");
  return { schema_version: 1, kind: "external-ai-transition-handoff", generated_result: false,
    source_render_id: job.id, renderer_version: job.result.renderer_version, manifest_url: job.result.manifest_url, request: job.request,
    endpoints: { outgoing: { ...job.request.outgoing, frame_url: job.result.frame_a_url, sampled_time: job.result.frame_a_time }, incoming: { ...job.request.incoming, frame_url: job.result.frame_b_url, sampled_time: job.result.frame_b_time },
      timing_note: "Use the render manifest for exact sampled endpoint timestamps." },
    provider_model: model, prompt, provenance_note: "Provider, model, prompt, and seed are user notes, not verified provider receipts.",
    result_retention: "Returned files can be explicitly imported as durable bridge auditions; this handoff does not submit hosted generation." };
}
