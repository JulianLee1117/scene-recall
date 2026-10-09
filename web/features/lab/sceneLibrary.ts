import type { LabClip, MusicMatchEvidence } from "@/types/lab";

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
