import type { SearchResult } from "@/types/api";

const validTime = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value) && value >= 0;

/** The moment the card actually shows; retrieval's frame may be a different picture. */
export function displayMoment(shot: SearchResult): number {
  if (validTime(shot.evidence_timestamp)) return shot.evidence_timestamp;
  const thumbnail = shot.thumbnail_url ?? shot.keyframe_url;
  if (thumbnail && thumbnail === shot.hero_url && validTime(shot.hero_time)) return shot.hero_time;
  if (thumbnail && thumbnail === shot.matched_frame_url && validTime(shot.matched_frame_timestamp)) return shot.matched_frame_timestamp;
  // Older compact alternatives carried a hero thumbnail and time, without hero_url.
  if (!shot.hero_url && shot.thumbnail_url && thumbnail !== shot.keyframe_url && validTime(shot.hero_time)) return shot.hero_time;
  if (validTime(shot.matched_frame_timestamp)) return shot.matched_frame_timestamp;
  if (validTime(shot.focus_start)) return shot.focus_start;
  return shot.t_start;
}

/** A Saved preview must still show the picture containing its durable moment. */
export function hoverPreviewUrl(shot: SearchResult): string | null {
  const preview = shot.preview_url?.trim();
  if (!preview) return null;
  if (shot.evidence_timestamp === undefined) return preview;
  const { evidence_timestamp: anchor, focus_start: start, focus_end: end } = shot;
  // Shorter focus spans deliberately retain the wider ingest clip (ADR-0098),
  // so they cannot establish that a Saved preview stays in the same picture.
  return validTime(anchor) && validTime(start) && validTime(end)
    && end - start >= 1 && start <= anchor && anchor < end ? preview : null;
}

/**
 * Where in the hover preview clip the displayed moment lies, in seconds from
 * the clip's start, so playback begins on the picture the card shows. Zero
 * when the backend has not said where the clip starts, or when the moment
 * falls outside the clip (then the clip plays from its start).
 */
export function previewOffset(shot: SearchResult): number {
  const { preview_start: start, preview_end: end } = shot;
  if (!validTime(start) || !validTime(end) || end <= start) return 0;
  const moment = displayMoment(shot);
  if (moment < start || moment > end - PREVIEW_TAIL) return 0;
  return Math.round((moment - start) * 1000) / 1000;
}

/** A moment this close to the clip's end would leave nothing to play. */
const PREVIEW_TAIL = 0.25;

/** Preserve the visible moment; a frame ID is only a hint when it names that moment. */
export function bookmarkAnchor(shot: SearchResult): { evidence_timestamp: number; frame_index: number | null } {
  const timestamp = displayMoment(shot);
  const matchesTime = (value: unknown) => validTime(value) && Math.abs(value - timestamp) < 0.001;
  let frameIndex: number | null = null;
  if (matchesTime(shot.matched_frame_timestamp)) {
    const candidate = shot.matched_frame_index ?? shot.keyframe_index;
    if (Number.isInteger(candidate) && candidate >= 0) frameIndex = candidate;
  }
  if (frameIndex === null) {
    for (const match of shot.matches ?? []) {
      const evidence = match.evidence;
      if (evidence?.type === "frame" && matchesTime(evidence.timestamp)
        && Number.isInteger(evidence.frame_index) && evidence.frame_index >= 0) {
        frameIndex = evidence.frame_index;
        break;
      }
    }
  }
  return { evidence_timestamp: timestamp, frame_index: frameIndex };
}
