import type { LabClip } from "@/types/lab";

export interface SourceFrame { time: number; end: number }
export interface MatchFrames {
  frames: SourceFrame[];
  t_start: number;
  t_end: number;
}

export function frameContains(frame: SourceFrame, time: number): boolean {
  return time >= frame.time - 0.000001 && time < frame.end - 0.000001;
}

// Frame intervals, rather than an assumed frame rate, define each edit boundary.
export function frameAt(frames: SourceFrame[], time: number): SourceFrame | undefined {
  return frames.find((frame) => frameContains(frame, time))
    ?? frames.reduce<SourceFrame | undefined>((nearest, frame) =>
      !nearest || Math.abs(frame.time - time) < Math.abs(nearest.time - time) ? frame : nearest, undefined);
}

export function stepFrame(frames: SourceFrame[], time: number, direction: -1 | 1): SourceFrame | undefined {
  const current = frameAt(frames, time);
  if (!current) return undefined;
  return frames[frames.indexOf(current) + direction];
}

export function referenceAt(clip: LabClip, time: number, bounds: Pick<MatchFrames, "t_start" | "t_end">): Partial<LabClip> {
  const reference = Math.max(bounds.t_start, Math.min(bounds.t_end - 0.001, time));
  const duration = Math.min(clip.source_end - clip.source_start, bounds.t_end - bounds.t_start);
  const start = Math.max(bounds.t_start, Math.min(bounds.t_end - duration, reference - 2));
  return {
    source_start: start,
    source_end: start + duration,
    reference_time: reference,
    window_start: null,
    window_end: null,
  };
}
