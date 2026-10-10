import type { LabClip } from "@/types/lab";
import type { SearchResult } from "@/types/api";

export const LAB_API = process.env.NEXT_PUBLIC_API_URL ?? "";
export const mediaUrl = (path: string) => `${LAB_API}${path}`;

export class LabError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

export async function labRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${LAB_API}/lab${path}`, {
    ...init,
    headers: { ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }), ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) message = body.detail.map((item: { msg?: string }) => item.msg).filter(Boolean).join(". ");
    } catch { /* A proxy can return a non-JSON failure. */ }
    throw new LabError(message, response.status);
  }
  return response.json();
}

export function seconds(value: number): string {
  if (!Number.isFinite(value)) return "0:00.00";
  const hundredths = Math.round(Math.max(0, value) * 100);
  return `${Math.floor(hundredths / 6000)}:${((hundredths % 6000) / 100).toFixed(2).padStart(5, "0")}`;
}

export function clipFromResult(result: SearchResult, duration = 4, aroundReference = false): LabClip {
  const reference = Math.max(result.t_start, Math.min(result.matched_frame_timestamp ?? result.t_start, result.t_end - .001));
  const start = aroundReference ? Math.max(result.t_start, Math.min(reference - duration / 2, result.t_end - duration)) : result.t_start;
  return {
    id: crypto.randomUUID(), film_id: result.film_id, unit_id: result.unit_id,
    title: result.film_title || result.film_id.replaceAll("_", " "),
    source_start: start, source_end: Math.min(result.t_end, start + duration), locked: false,
    reference_time: Math.max(start, Math.min(reference, Math.min(result.t_end, start + duration) - .001)),
  };
}
