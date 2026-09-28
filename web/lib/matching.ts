import type { SearchResult } from "@/types/api";
import type { MatchCue, MatchSearchRequest, SceneMatch } from "@/types/matching";

export const MATCH_API = process.env.NEXT_PUBLIC_API_URL ?? "";
export async function matchingRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${MATCH_API}/matching${path}`, {
    ...init, headers: { "Content-Type": "application/json", ...init?.headers }, cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : Array.isArray(body?.detail) ? body.detail.map((item: { msg?: string }) => item.msg).filter(Boolean).join(". ") : `Matching request failed (${response.status}).`);
  }
  return response.json();
}

export function matchSceneHref(shot: Pick<SearchResult, "unit_id" | "t_start" | "t_end" | "matched_frame_timestamp" | "evidence_timestamp">, currentTime?: number): string {
  const fallback = Math.max(shot.t_start, shot.t_end - 0.15);
  const proposed = currentTime ?? shot.matched_frame_timestamp ?? shot.evidence_timestamp ?? fallback;
  const time = Math.max(shot.t_start, Math.min(shot.t_end - 0.001, Number.isFinite(proposed) ? proposed : fallback));
  return `/match?${new URLSearchParams({ unit_id: shot.unit_id, time: String(time) })}`;
}

export function primaryMatchCue(candidate: SceneMatch): MatchCue | undefined {
  return candidate.cues?.find((cue) => cue.code === candidate.primary_cue) ?? candidate.cues?.[0];
}

export const matchJobActive = (job: { status: string } | null) => !!job && ["queued", "running"].includes(job.status);

export function matchSearchKey(request: MatchSearchRequest): string {
  return JSON.stringify({ cohort_id: request.cohort_id, focus: request.focus, timing: request.timing,
    film_ids: [...request.film_ids].sort(), include_source_film: request.include_source_film,
    min_incoming_seconds: request.min_incoming_seconds, allow_reframing: request.allow_reframing,
    reference: { unit_id: request.reference.unit_id, time: request.reference.time,
      subject_point: request.reference.subject_point ?? null, region: request.reference.region ?? null } });
}
