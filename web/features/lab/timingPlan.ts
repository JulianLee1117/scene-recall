import type { LabJob, LabProject } from "@/types/lab";
import { MAX_MUSIC_TIMELINE_SLOTS } from "@/lib/labLimits";

type RecordValue = Record<string, unknown>;
const record = (value: unknown): RecordValue => value && typeof value === "object" && !Array.isArray(value) ? value as RecordValue : {};
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const label = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;

export interface TimingDiagnostics {
  selected_shots: number;
  shortest_seconds: number;
  longest_seconds: number;
  average_seconds_selected: number;
  uniform_timing: boolean;
}

export interface TimingPlanReceipt {
  contract: string;
  artifact_id: string;
  track_id: string;
  passage: { start: number; end: number };
  fps: number;
  end_frames: number[];
  final_end_frames: number[] | null;
  nominal_timing: TimingDiagnostics | null;
  final_timing: TimingDiagnostics | null;
  notes: { start_frame: number; end_frame: number; reason: string; evidence_ids: string[] }[];
  cache_reused: boolean | null;
}

function frames(value: unknown, total: number): number[] | null {
  if (!Array.isArray(value) || !value.length || value.length > MAX_MUSIC_TIMELINE_SLOTS) return null;
  if (value.some((frame, i) => !Number.isSafeInteger(frame) || frame <= (i ? value[i - 1] : 0) || frame > total)) return null;
  return value[value.length - 1] === total ? value : null;
}

function diagnostics(value: unknown, count: number): TimingDiagnostics | null {
  const row = record(value);
  if (row.selected_shots !== count || !finite(row.shortest_seconds) || row.shortest_seconds <= 0 ||
      !finite(row.longest_seconds) || row.longest_seconds < row.shortest_seconds ||
      !finite(row.average_seconds_selected) || row.average_seconds_selected < row.shortest_seconds - 1e-6 ||
      row.average_seconds_selected > row.longest_seconds + 1e-6 || typeof row.uniform_timing !== "boolean") return null;
  return row as unknown as TimingDiagnostics;
}

/** Read recorded facts only. Old or malformed receipts do not acquire invented explanations. */
export function readTimingPlan(value: unknown): TimingPlanReceipt | null {
  const row = record(value), passage = record(row.passage);
  if (!label(row.contract) || !label(row.artifact_id) || !label(row.track_id) ||
      !finite(passage.start) || passage.start < 0 || !finite(passage.end) || passage.end <= passage.start ||
      !finite(row.fps) || row.fps <= 0 || !Array.isArray(row.end_frames)) return null;
  const total = row.end_frames.at(-1);
  // The backend uses Python's rounding; accept either tie at a fractional final frame.
  if (!Number.isSafeInteger(total) || Math.abs(total - (passage.end - passage.start) * row.fps) > .500001) return null;
  const planned = frames(row.end_frames, total);
  if (!planned) return null;
  const final = frames(row.final_end_frames, total);
  const notes: TimingPlanReceipt["notes"] = [];
  for (const value of Array.isArray(row.notes) ? row.notes.slice(0, 32) : []) {
    const note = record(value);
    if (!Number.isSafeInteger(note.start_frame) || !Number.isSafeInteger(note.end_frame) ||
        (note.start_frame as number) < 0 || (note.end_frame as number) <= (note.start_frame as number) ||
        (note.end_frame as number) > total || !label(note.reason)) continue;
    notes.push({ start_frame: note.start_frame as number, end_frame: note.end_frame as number, reason: note.reason,
      evidence_ids: Array.isArray(note.evidence_ids) ? note.evidence_ids.filter(label).slice(0, 64) : [] });
  }
  return {
    contract: row.contract, artifact_id: row.artifact_id, track_id: row.track_id,
    passage: { start: passage.start, end: passage.end }, fps: row.fps, end_frames: planned, final_end_frames: final,
    nominal_timing: diagnostics(row.nominal_timing, planned.length),
    final_timing: final ? diagnostics(row.final_timing, final.length) : null,
    notes, cache_reused: typeof row.cache_reused === "boolean" ? row.cache_reused : null,
  };
}

export function timingPlanForJob(job: LabJob, savedProject?: Pick<LabProject, "id" | "revision" | "document">): TimingPlanReceipt | null {
  if (job.result?.timing_plan != null) return readTimingPlan(job.result.timing_plan);
  // A current document's receipt must never be attributed to an unrelated or failed task.
  if (!savedProject || savedProject.id !== job.project_id || job.status !== "completed" ||
      job.result?.applied !== true || job.result.revision !== savedProject.revision) return null;
  const plan = readTimingPlan(savedProject.document.direction_plan?.timing_plan);
  const { track, passage } = savedProject.document;
  return plan && plan.track_id === track?.id && Math.abs(plan.passage.start - passage.start) < 1e-6 &&
    Math.abs(plan.passage.end - passage.end) < 1e-6 ? plan : null;
}

export function timingFrameTime(plan: TimingPlanReceipt, frame: number): number {
  return frame === plan.end_frames.at(-1) ? plan.passage.end : plan.passage.start + frame / plan.fps;
}
