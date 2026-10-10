import type { Acquisition, AcquisitionState, FilmMetadata } from "./types";
import type { IngestJob, LibraryFilm } from "@/types/api";

const LABELS: Record<AcquisitionState, string> = {
  queued: "Queued", downloading: "Downloading", validating: "Checking files",
  needs_review: "Needs your review", importing: "Adding to library", ingest_queued: "Waiting to prepare",
  ingesting: "Preparing for search", cleanup: "Finishing up", ready: "Ready to search", failed: "Needs attention", cancelling: "Cancelling…", cancelled: "Cancelled",
};

export function stateLabel(state: AcquisitionState): string { return LABELS[state] ?? state; }
export function isCancelling(item: Acquisition): boolean {
  return item.status === "cancelling" || item.cancellation_cleanup === "pending"
    || Boolean(item.cancel_requested && item.status !== "cancelled");
}
export function isActive(item: Acquisition): boolean {
  return isCancelling(item) || !["ready", "failed", "cancelled"].includes(item.status);
}
export function canCancel(item: Acquisition): boolean {
  return !isCancelling(item) && !["ready", "cancelled"].includes(item.status);
}
export function cancellationNotice(item?: Acquisition): string {
  const kept = item?.film_path ? " The imported file stays until you dismiss this; dismissing removes it unless the film is already searchable." : "";
  if (item?.status === "cancelled" && item.cancellation_cleanup === "complete") {
    return `Cancelled. Downloaded files were cleaned up.${kept}`;
  }
  if (item?.status === "cancelled") return `Cancelled.${kept}`;
  return `Cancellation requested. Cleanup will finish in the background.${kept}`;
}
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return "Unknown size";
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  const exponent = Math.min(4, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** exponent).toFixed(exponent > 1 ? 1 : 0)} ${["B", "KB", "MB", "GB", "TB"][exponent]}`;
}
export function formatEta(seconds: number | null | undefined): string | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0 || seconds >= 8_640_000) return null;
  if (seconds < 60) return "Less than a minute left";
  if (seconds < 3600) return `${Math.ceil(seconds / 60)} min left`;
  return `${Math.floor(seconds / 3600)}h ${Math.ceil((seconds % 3600) / 60)}m left`;
}
export function metadataFromFields(title: string, year: string, edition: string): FilmMetadata {
  if (!title.trim()) throw new Error("Enter the film title for your library.");
  if (!/^\d{4}$/.test(year.trim()) || Number(year) < 1888 || Number(year) > 2100) {
    throw new Error("Enter a release year between 1888 and 2100.");
  }
  return { title: title.trim(), year: Number(year), edition: edition.trim() };
}

/** Refresh Films when canonical files or ingestion state have changed. */
export function catalogChanged(before: Acquisition[], after: Acquisition[]): boolean {
  const previous = new Map(before.map((item) => [item.id, item]));
  return after.some((item) => {
    const old = previous.get(item.id);
    return Boolean(item.film_path) && (!old || old.film_path !== item.film_path || old.status !== item.status);
  });
}

export function filmPathKey(path: string): string { return path.replaceAll("\\", "/").toLowerCase(); }

/** Every in-progress film has one row and one owner for retry/cancel actions. */
export function belongsInLibrary(film: LibraryFilm, jobs: IngestJob[], items: Acquisition[]): boolean {
  const key = filmPathKey(film.path);
  const latestJob = jobs.filter((job) => filmPathKey(job.path) === key).at(-1);
  if (latestJob && ["queued", "running"].includes(latestJob.status)) return false;
  return !items.some((item) => !["ready", "cancelled"].includes(item.status) && item.film_path && filmPathKey(item.film_path) === key);
}

export function independentJobs(jobs: IngestJob[], items: Acquisition[]): IngestJob[] {
  const latest = new Map<string, IngestJob>();
  for (const job of jobs) latest.set(filmPathKey(job.path), job);
  return [...latest.values()].filter((job) => ["queued", "running"].includes(job.status)
    && !items.some((item) => !["ready", "cancelled"].includes(item.status)
      && (item.ingest_job_id === job.job_id || (item.film_path && filmPathKey(item.film_path) === filmPathKey(job.path)))));
}

export function waitingLabel(job?: IngestJob): string {
  return job?.queue_position === 1 ? "Next in queue"
    : job?.queue_position ? `Waiting · #${job.queue_position}` : "Waiting to prepare";
}

// Stages before the film is searchable.
const INGEST_STAGES: Record<string, string> = {
  probe: "Checking the film", dialogue: "Preparing dialogue", shots: "Finding scenes",
  media: "Creating previews", keyframes: "Preparing scene images", embed: "Preparing visual search",
  annotate: "Describing scenes", frames: "Making scenes searchable", publish: "Making scenes searchable",
  text: "Preparing text search", "text-features": "Preparing text search",
  "embed+annotate+write": "Making scenes searchable",
};

// Evidence passes run after the film is already searchable.
const EVIDENCE_STAGES: Record<string, string> = {
  metadata: "Looking up film details", subtitles: "Syncing subtitles",
  understanding: "Understanding the story", highlights: "Picking key moments",
  measure: "Measuring camera, subjects and color", moments: "Indexing moments for match cuts",
  hero: "Choosing the best frames", synthesis: "Ranking iconic moments and hidden gems",
  compile: "Assembling scene details", "text-views": "Updating text search",
  "match-index": "Updating match cuts", evidence: "Finishing scene details",
};

/** A pipeline line that names a known stage, e.g. "[understanding] Elf part 7/11"; never a timestamp or library warning. */
function stageOf(line: string): string | null {
  const stage = line.match(/^\[([a-z][a-z0-9+_-]*)\]/)?.[1] ?? null;
  return stage && (INGEST_STAGES[stage] || EVIDENCE_STAGES[stage]) ? stage : null;
}

/**
 * What a running ingest is doing, in words. The pipeline also prints model
 * warnings, so read the newest line that names a stage rather than the last line.
 */
export function preparationProgress(progress?: string | null, log: readonly string[] = []): string {
  const line = [progress ?? "", ...[...log].reverse()].find((candidate) => stageOf(candidate)) ?? "";
  const stage = stageOf(line) ?? "";
  if (EVIDENCE_STAGES[stage]) {
    // Gemini watches the film in chunks; its count is parts, not scenes.
    const part = stage === "understanding" ? line.match(/\bpart\s+(\d+)\s*\/\s*(\d+)\b/i) : null;
    return `Searchable · ${EVIDENCE_STAGES[stage]}${part ? ` · part ${part[1]} of ${part[2]}` : ""}`;
  }
  const label = INGEST_STAGES[stage] ?? "Preparing scenes for search";
  const count = line.match(/\b(\d+)\s*\/\s*(\d+)\b/);
  return count && Number(count[2]) > 0 ? `${label} · ${Number(count[1]).toLocaleString()} of ${Number(count[2]).toLocaleString()}` : label;
}

export function acquisitionHint(item: Acquisition, job?: IngestJob): string {
  if (isCancelling(item)) return `${item.error ? "Cleanup is pending and will retry automatically." : "Stopping managed work and cleaning up downloaded files."}${item.film_path ? " The imported file stays until you dismiss this; dismissing removes it unless the film is already searchable." : ""}`;
  switch (item.status) {
    case "queued": return "The download starts when a slot is free.";
    case "downloading": return item.message?.includes("stopped in qBittorrent") ? "Download paused. Resume it in qBittorrent."
      : item.message === "Waiting for peers" ? "Waiting for people sharing this download." : "";
    case "validating": return "Checking that the download is complete and the film plays.";
    case "needs_review": return "Confirm the film or its English subtitles to continue.";
    case "importing": return "Adding the verified film to your library.";
    case "ingest_queued": return "Starts automatically when its turn arrives. You can leave this page.";
    case "ingesting": return preparationProgress(job?.progress);
    case "cleanup": return "The film is searchable. Finishing release-file archival.";
    case "ready": return "Available in your library.";
    case "failed": return "Review the issue below, then try again or cancel and clean up the download.";
    case "cancelling": return "Stopping managed work and cleaning up downloaded files.";
    case "cancelled": return cancellationNotice(item);
  }
}
