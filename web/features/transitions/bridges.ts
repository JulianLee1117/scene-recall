export interface BridgeRequest {
  parent_render_id: string;
  trim_start: number;
  trim_end?: number | null;
  playback_duration?: number | null;
  provenance: { provider?: string | null; model?: string | null; prompt?: string | null; seed?: number | null };
}

export interface BridgeJob {
  id: string;
  status: string;
  request: BridgeRequest;
  input: { name: string; size: number; media: { duration: number }; provenance_status: string };
  progress?: string | null;
  error?: string | null;
  cancel_requested?: boolean;
  result?: { preview_url: string; manifest_url: string; original_url: string; duration: number;
    transition_start: number; transition_end: number; fps: number; width: number; height: number } | null;
}

export const MAX_BRIDGE_UPLOAD = 128 * 1024 * 1024;
export const bridgePending = (job: BridgeJob) => job.status === "queued" || job.status === "running";
export const bridgeSourceTimingProblem = (retime?: { mode?: string }) => retime?.mode && retime.mode !== "off"
  ? "Render a version with Speed off before importing or generating an AI bridge." : "";

export function bridgeRequest(parent: string, trimStart: string, trimEnd: string, playbackDuration: string,
  provider: string, model: string, prompt: string): BridgeRequest {
  const start = trimStart.trim() === "" ? 0 : Number(trimStart);
  const end = trimEnd.trim() === "" ? undefined : Number(trimEnd);
  const duration = playbackDuration.trim() === "" ? undefined : Number(playbackDuration);
  if (!Number.isFinite(start) || start < 0 || start >= 30) throw new Error("Bridge in time must be between 0 and 30 seconds.");
  if (end !== undefined && (!Number.isFinite(end) || end > 30 || end - start < .08 - 1e-8))
    throw new Error("Bridge out time must leave at least 0.08 seconds and stay within 30 seconds.");
  if (duration !== undefined && (!Number.isFinite(duration) || duration < .08 || duration > 30))
    throw new Error("Bridge playback length must be between 0.08 and 30 seconds.");
  return { parent_render_id: parent, trim_start: start, ...(end === undefined ? {} : { trim_end: end }),
    ...(duration === undefined ? {} : { playback_duration: duration }),
    provenance: { provider: provider.trim() || null, model: model.trim() || null, prompt: prompt.trim() || null } };
}

export { BRIDGE_PROMPTS, BRIDGE_DIRECTIONS, buildBridgePrompt, type BridgeDirectionOptions } from "./directions";
