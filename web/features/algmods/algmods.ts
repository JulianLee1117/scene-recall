import type { SearchResult } from "@/types/api";

export interface SourceWindow { film_id: string; unit_id?: string | null; source_start: number; source_end: number; }
export interface SourceSelection extends SourceWindow { title: string; }
export type TreatmentKind = "dots" | "stripes" | "quadtree" | "mosaic";
/** A treatment is its kind plus that kind's parameters; the catalog says which keys exist. */
export type Treatment = { kind: TreatmentKind; style?: "vivid" | "pastel" } & Record<string, unknown>;
export type ParamValue = number | string | boolean | string[];

/** Read a control's value; keys may be dotted ("region.kind"). */
export function getPath(treatment: Treatment, key: string): unknown {
  return key.split(".").reduce<unknown>((node, part) => (node && typeof node === "object" ? (node as Record<string, unknown>)[part] : undefined), treatment);
}
/** A copy of the treatment with a (possibly dotted) key set. */
export function setPath(treatment: Treatment, key: string, value: ParamValue): Treatment {
  const [head, ...rest] = key.split(".");
  if (!rest.length) return { ...treatment, [head]: value };
  const child = (treatment[head] && typeof treatment[head] === "object" ? treatment[head] : {}) as Treatment;
  return { ...treatment, [head]: setPath(child, rest.join("."), value) };
}
export interface RenderRequest { source: SourceWindow; treatment: Treatment; output: { side_by_side: boolean }; }
export interface Control {
  key: string; label: string; type: "range" | "toggle" | "select" | "text"; min?: number; max?: number; step?: number; unit?: string;
  advanced: boolean; options?: { value: string; label: string }[]; placeholder?: string;
}
export interface TreatmentSpec {
  id: TreatmentKind; name: string; description: string; defaults: Treatment; controls: Control[];
  styles?: { id: "vivid" | "pastel"; name: string; description: string; defaults: Treatment }[];
}
export interface Catalog {
  version: string;
  treatments: TreatmentSpec[];
  limits: { min_window_seconds: number; max_window_seconds: number; fps: number; width: number; height: number };
}
export interface RenderResult {
  preview_url: string; manifest_url: string; duration: number; frames: number; fps: number; width: number; height: number;
  version: string; treatment: Treatment; side_by_side: boolean; subject_detection: boolean; crop: number[]; output_sha256: string; manifest_sha256: string;
}
export interface RenderJob {
  id: string; kind: string; status: "queued" | "running" | "completed" | "failed" | "cancelled" | string;
  cancel_requested: boolean; error?: string | null; progress_steps?: string[]; created_at: number;
  request: RenderRequest; source_title: string; result: RenderResult | null;
  storage: { output_bytes: number; retained_bytes: number } | null;
}

export const activeRender = (job: RenderJob) => (job.status === "queued" || job.status === "running") && !job.cancel_requested;

/** A 3-second window around the matched frame, clipped to the shot. */
export function sourceFromResult(result: SearchResult, seconds = 3): SourceSelection {
  const reference = result.matched_frame_timestamp ?? result.t_start;
  const start = Math.max(result.t_start, Math.min(reference - seconds / 2, result.t_end - seconds));
  return { film_id: result.film_id, unit_id: result.unit_id, title: result.film_title || result.film_id,
    source_start: round(start), source_end: round(Math.min(result.t_end, start + seconds)) };
}

export const round = (value: number) => Math.round(value * 100) / 100;

/** One scene on the board: a film window, exact to the hundredth. */
export const sceneKey = (source: SourceWindow) => `${source.film_id}|${round(source.source_start)}|${round(source.source_end)}`;

export function sourcePayload(source: SourceWindow): SourceWindow {
  return { film_id: source.film_id, ...(source.unit_id ? { unit_id: source.unit_id } : {}),
    source_start: source.source_start, source_end: source.source_end };
}

export function validateWindow(source: SourceSelection | null, limits: Catalog["limits"] | undefined): string {
  if (!source) return "Choose a scene first.";
  const length = source.source_end - source.source_start;
  if (!(length >= (limits?.min_window_seconds ?? .2) - 1e-8)) return "The window must be at least 0.2 seconds.";
  if (length > (limits?.max_window_seconds ?? 12) + 1e-8) return `The window may be at most ${limits?.max_window_seconds ?? 12} seconds.`;
  return "";
}

export const specFor = (catalog: Catalog | null, kind: string) => catalog?.treatments.find((row) => row.id === kind) ?? null;

/** The defaults a treatment starts from: a dots style's defaults, or the treatment's own. */
export function baseline(catalog: Catalog | null, treatment: Treatment): Treatment | null {
  const spec = specFor(catalog, treatment.kind);
  if (!spec) return null;
  if (treatment.kind === "dots" && spec.styles) return spec.styles.find((style) => style.id === treatment.style)?.defaults ?? spec.defaults;
  return spec.defaults;
}

/** Human summary: the treatment (and style), then only what differs from its defaults. */
export function treatmentSummary(treatment: Treatment, catalog: Catalog | null): string {
  const spec = specFor(catalog, treatment.kind);
  const base = baseline(catalog, treatment);
  const parts: string[] = [spec ? spec.name.toLowerCase() : treatment.kind];
  if (treatment.kind === "dots" && treatment.style) parts[0] += ` · ${treatment.style}`;
  for (const control of spec?.controls ?? []) {
    const value = getPath(treatment, control.key), standard = base ? getPath(base, control.key) : undefined;
    if (standard === undefined || JSON.stringify(value) === JSON.stringify(standard)) continue;
    const label = control.label.toLowerCase();
    if (control.type === "toggle") parts.push(`${label} ${value ? "on" : "off"}`);
    else if (control.type === "select") parts.push(`${label} ${control.options?.find((option) => option.value === value)?.label.toLowerCase() ?? value}`);
    else if (control.type === "text") parts.push(`${label} ${Array.isArray(value) ? value.join(", ") : String(value)}`);
    else parts.push(`${label} ${value}`);
  }
  return parts.join(" · ");
}

export function sameRequest(a: RenderRequest, b: RenderRequest | null): boolean {
  return !!b && JSON.stringify(a) === JSON.stringify(b);
}
