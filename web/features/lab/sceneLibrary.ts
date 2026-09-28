import type { SearchResult } from "@/types/api";
import type { LabClip, MusicAlternative, MusicMatchEvidence } from "@/types/lab";

/** A shorter drop must still contain the retrieved visual moment when possible. */
export function fitDraggedScene(clip: LabClip, duration: number, evidence: MusicMatchEvidence | null): LabClip {
  const moment = evidence?.matched_frame_timestamp;
  if (typeof moment !== "number" || !Number.isFinite(moment) ||
      moment < clip.source_start || moment >= clip.source_end ||
      duration >= clip.source_end - clip.source_start) return clip;
  const start = Math.max(clip.source_start,
    Math.min(clip.source_end - duration, moment - duration / 2));
  return { ...clip, source_start: start, source_end: start + duration };
}

/** Center an editable source window on retrieval evidence, within its shot. */
export function libraryAlternative(row: SearchResult, duration: number): MusicAlternative {
  const length = Math.min(duration, row.t_end - row.t_start);
  const timestamp = row.matched_frame_timestamp;
  const moment = typeof timestamp === "number" && Number.isFinite(timestamp) &&
    timestamp >= row.t_start && timestamp <= row.t_end
    ? timestamp : (row.t_start + row.t_end) / 2;
  const start = Math.max(row.t_start, Math.min(row.t_end - length, moment - length / 2));
  return {
    clip: {
      id: `library-${row.unit_id}`,
      film_id: row.film_id,
      unit_id: row.unit_id,
      title: row.caption,
      source_start: start,
      source_end: start + length,
      locked: false,
    },
    film_title: row.film_title || "",
    search_evidence: {
      rank: row.rank ?? 1,
      matched_text: row.matched_text ?? "",
      matched_text_view: row.matched_text_view ?? null,
      matched_frame_index: row.matched_frame_index ?? row.keyframe_index ?? null,
      matched_frame_timestamp: timestamp ?? null,
      matches: [],
      channels: {},
    },
  };
}
