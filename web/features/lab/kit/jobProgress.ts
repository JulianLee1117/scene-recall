import type { LabJob, WorkerStatus } from "@/types/lab";

const jobNames: Record<LabJob["kind"], string> = {
  generate: "Generate edit", rhythm: "Detect beats", analyze: "Analyze music",
  plan: "Plan scenes", draft: "Find scenes", render: "Export video",
  "next-scene": "Find next scene", "next-scene-preview": "Prepare scene preview",
};

export const isJobActive = (job: LabJob) => job.status === "queued" || job.status === "running";
export const jobName = (job: LabJob) => jobNames[job.kind] ?? "Editor task";

export function queuedWorkerSummary(job: LabJob, status: WorkerStatus | null): string | null {
  if (job.status !== "queued" || job.cancel_requested || !status) return null;
  const role = job.worker_role ?? "editor";
  const worker = status.workers.find((row) => row.role === role);
  if (!worker) return null;
  const label = role === "editor" ? "Editor" : "Library";
  if (!worker.online) return `${label} worker is offline. Start the workers to continue.`;
  if (worker.state === "stopping") return `${label} worker is stopping. This task stays saved in the queue.`;
  if (worker.current_job && worker.current_job.id !== job.id) {
    if (role === "ingest" && worker.current_job.kind === "ingest") return "Waiting for film ingestion to release the GPU.";
    if (worker.mode === "serial") return "Waiting for the shared worker. Separate workers let editing run alongside ingestion.";
    return `Waiting for the current ${label.toLowerCase()} task to finish.`;
  }
  return `Queued for the ${label.toLowerCase()} worker.`;
}

function timestamp(value: string | number | null | undefined): number | null {
  const parsed = typeof value === "number" ? value * 1000 : typeof value === "string" ? Date.parse(value) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

/** Use durable worker timestamps; missing timing stays unknown, never restarts at mount. */
export function jobElapsed(job: LabJob, now: number): number | null {
  const start = timestamp(job.started_at) ?? timestamp(job.created_at);
  const end = isJobActive(job) ? now : timestamp(job.finished_at);
  return start === null || end === null ? null : Math.max(0, Math.floor((end - start) / 1000));
}

export function elapsedLabel(elapsed: number): string {
  return elapsed < 60 ? `${elapsed}s` : `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`;
}

function message(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function jobSummary(job: LabJob, notice?: string): string {
  if (job.cancel_requested && isJobActive(job)) return "Stopping after the current operation…";
  if (isJobActive(job)) return message(job.progress) ?? (job.status === "queued" ? "Waiting for the worker" : "Starting…");
  if (job.status === "failed") return message(job.error) ?? "The task could not finish. You can start it again when ready.";
  if (job.status === "interrupted") return "The worker stopped before finishing. Review the saved edit before trying again.";
  if (job.status === "cancelled") return "Cancelled. Your saved project is available for editing.";
  if (job.result?.applied === false) return "Finished without replacing your edit. The project changed while this task ran.";
  return message(notice) ?? message(job.result?.message) ?? (job.result?.output_url ? "Video ready." : "Finished.");
}

export function jobSteps(job: LabJob): string[] {
  const rows = Array.isArray(job.progress_steps) ? job.progress_steps.filter((row) => typeof row === "string" && row.trim()) : [];
  const progress = message(job.progress);
  const steps = progress && rows.at(-1) !== progress ? [...rows, progress] : rows;
  return steps.slice(-80).filter((row, index, all) => index === 0 || row !== all[index - 1]);
}

export function jobStateLabel(job: LabJob): string {
  if (job.cancel_requested && isJobActive(job)) return "Stopping";
  if (job.status === "completed" && job.result?.applied === false) return "Not applied";
  return { queued: "Queued", running: "Running", completed: "Complete", failed: "Failed", interrupted: "Interrupted", cancelled: "Cancelled" }[job.status];
}

export function jobCounts(job: LabJob): { label: string; count: number }[] {
  return [["Offered candidates", job.result?.candidate_count], ["Selected scenes", job.result?.selected_count], ["Unfilled shots", job.result?.remaining_gaps]]
    .flatMap(([label, count]) => typeof count === "number" && Number.isSafeInteger(count) && count >= 0 ? [{ label: String(label), count }] : []);
}
