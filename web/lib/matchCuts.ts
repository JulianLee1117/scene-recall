import type { SearchResult } from "@/types/api";

export const MATCH_API = process.env.NEXT_PUBLIC_API_URL ?? "";

export type OutputFormat = "landscape" | "vertical" | "square";
export type MatchFocus = "auto" | "subject" | "shape" | "motion" | "composition" | "color";
export type Crop = [number, number, number, number];            // x, y, width, height in content fractions
export type ContentBox = [number, number, number, number] | null; // x0, y0, x1, y1 in video fractions

export interface MomentShot {
  unit_id: string;
  film_id: string;
  film_title: string;
  scene_id: string | null;
  t_start: number;
  t_end: number;
  time: number;
  aspect: number;
  content_box: ContentBox;
  moments: { time: number; ok: boolean }[];
}

export interface MatchReason {
  code: string;
  label: string;
  strength: number;
  /** The Vision layers that show what this reason measured (none for a reframe). */
  layers?: string[];
}

export interface MomentMatch {
  unit_id: string;
  film_id: string;
  film_title: string;
  scene_id: string | null;
  time: number;
  t_start: number;
  t_end: number;
  score: number;
  parts: Record<string, number>;
  reasons: MatchReason[];
  crop: Crop;
  zoom: number;
  aspect: number;
  content_box: ContentBox;
  frame_url: string;
}

export interface MomentSearchResponse {
  reference: {
    unit_id: string; film_id: string; film_title: string; time: number; t_start: number; t_end: number;
    crop: Crop | null; aspect: number; content_box: ContentBox; frame_url: string;
    subject: { box: number[]; count: number; person: boolean } | null;
  };
  results: MomentMatch[];
  searched: { moments: number; films: number; shots: number; instants: number };
  elapsed_ms: { coarse: number; total: number };
}

export interface MatchSettings {
  focus: MatchFocus;
  output: OutputFormat;
  reframe: boolean;
  includeSameFilm: boolean;
}

/** One clip of a match-cut chain: a source window seen through a crop. */
export interface ChainLink {
  unit_id: string;
  film_id: string;
  film_title: string;
  start: number;
  end: number;
  t_start: number;
  t_end: number;
  crop: Crop | null;
  aspect: number;
  content_box: ContentBox;
}

export const OUTPUT_ASPECT: Record<OutputFormat, number> = { landscape: 16 / 9, vertical: 9 / 16, square: 1 };
export const FOCUS_LABELS: Record<MatchFocus, string> = {
  auto: "Best match", subject: "Subject", shape: "Shape & pose", motion: "Motion", composition: "Composition", color: "Colour",
};
export const DEFAULT_SETTINGS: MatchSettings = { focus: "auto", output: "landscape", reframe: false, includeSameFilm: false };
export const FRAME_SECONDS = 1 / 24;
export const LEAD_IN = 1.5;
export const LEAD_OUT = 2;

export async function matchRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${MATCH_API}/matching/moments${path}`, {
    ...init, headers: { "Content-Type": "application/json", ...init?.headers }, cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    throw new Error(typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((item: { msg?: string }) => item.msg).filter(Boolean).join(". ")
      : `Match request failed (${response.status}).`);
  }
  return response.json();
}

export const mediaUrl = (path: string) => `${MATCH_API}${path}`;

export async function playbackUrl(filmId: string, signal?: AbortSignal): Promise<string> {
  const response = await fetch(`${MATCH_API}/video/${encodeURIComponent(filmId)}/playback`, { signal, cache: "no-store" });
  if (!response.ok) throw new Error("This film cannot be played right now.");
  const body = await response.json();
  return `${MATCH_API}${body.url}`;
}

/** The instant a search result points at: its matched frame, else its hero frame, else near its end. */
export function resultTime(shot: Pick<SearchResult, "t_start" | "t_end"> & Partial<Pick<SearchResult, "matched_frame_timestamp" | "evidence_timestamp">>): number {
  const fallback = Math.max(shot.t_start, shot.t_end - 0.3);
  const proposed = shot.matched_frame_timestamp ?? shot.evidence_timestamp ?? fallback;
  return Math.max(shot.t_start, Math.min(shot.t_end - 0.05, Number.isFinite(proposed) ? proposed : fallback));
}

export function matchHref(unitId: string, time: number, settings?: Partial<MatchSettings>): string {
  const params = new URLSearchParams({ unit_id: unitId, time: time.toFixed(3) });
  if (settings?.focus && settings.focus !== "auto") params.set("focus", settings.focus);
  if (settings?.output && settings.output !== "landscape") params.set("output", settings.output);
  if (settings?.reframe) params.set("reframe", "1");
  if (settings?.includeSameFilm) params.set("same_film", "1");
  return `/match?${params}`;
}

export function settingsFrom(params: URLSearchParams): MatchSettings {
  const focus = params.get("focus") as MatchFocus | null;
  const output = params.get("output") as OutputFormat | null;
  return {
    focus: focus && focus in FOCUS_LABELS ? focus : DEFAULT_SETTINGS.focus,
    output: output && output in OUTPUT_ASPECT ? output : DEFAULT_SETTINGS.output,
    reframe: params.get("reframe") === "1",
    includeSameFilm: params.get("same_film") === "1",
  };
}

export interface Placement { left: number; top: number; width: number; height: number }

/**
 * Where to draw a full media frame (percent of an output box) so that a crop
 * of its content fills the box, letterboxed when the crop's shape differs.
 *
 * ``frameAspect`` is the shape of the whole media frame; ``contentBox`` is the
 * content picture inside it (``null`` when the media is the content picture,
 * as for the workspace's still frames); ``crop`` is in content fractions.
 */
export function placement(crop: Crop | null, frameAspect: number, contentBox: ContentBox, outputAspect: number): Placement {
  const [x0, y0, x1, y1] = contentBox ?? [0, 0, 1, 1];
  const [cx, cy, cw, ch] = crop ?? [0, 0, 1, 1];
  const rx = x0 + cx * (x1 - x0), ry = y0 + cy * (y1 - y0);
  const rw = cw * (x1 - x0), rh = ch * (y1 - y0);
  const regionAspect = frameAspect * rw / rh;
  const shownWidth = regionAspect >= outputAspect ? 1 : regionAspect / outputAspect;
  const shownHeight = regionAspect >= outputAspect ? outputAspect / regionAspect : 1;
  const offsetX = (1 - shownWidth) / 2, offsetY = (1 - shownHeight) / 2;
  const width = shownWidth / rw, height = shownHeight / rh;
  return { left: (offsetX - rx * width) * 100, top: (offsetY - ry * height) * 100, width: width * 100, height: height * 100 };
}

/** Whether a crop has the output's shape (a chain keeps its crops only while the format stays the same). */
export function cropFits(crop: Crop, contentAspect: number, output: OutputFormat): boolean {
  const shape = contentAspect * crop[2] / crop[3];
  const wanted = output === "landscape" ? contentAspect : OUTPUT_ASPECT[output];
  return Math.abs(Math.log(shape / wanted)) < 0.03;
}

/** Whole-frame aspect of a film from its content picture's aspect and content box. */
export function frameAspect(contentAspect: number, contentBox: ContentBox): number {
  if (!contentBox) return contentAspect;
  const [x0, y0, x1, y1] = contentBox;
  return contentAspect * (y1 - y0) / (x1 - x0);
}

/** The usable grid instant nearest a time (the index describes four instants a second). */
export function nearestMoment(shot: MomentShot, time: number): number {
  const usable = shot.moments.filter((moment) => moment.ok);
  const pool = usable.length ? usable : shot.moments;
  if (!pool.length) return time;
  return pool.reduce((best, moment) => Math.abs(moment.time - time) < Math.abs(best - time) ? moment.time : best, pool[0].time);
}

/** Default out-point for a newly chained clip: two seconds in, inside its shot. */
export function defaultOut(link: Pick<ChainLink, "start" | "t_end">): number {
  return Math.min(link.t_end - 0.05, link.start + LEAD_OUT);
}

export function percent(value: number): string {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}
