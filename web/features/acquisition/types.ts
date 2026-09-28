export type AcquisitionState =
  | "queued" | "downloading" | "validating" | "needs_review" | "importing"
  | "ingest_queued" | "ingesting" | "cleanup" | "ready" | "failed" | "cancelling" | "cancelled";

export interface Acquisition {
  id: string;
  revision: number;
  title: string;
  year: number;
  edition: string | null;
  status: AcquisitionState;
  cancel_requested?: boolean;
  cancellation_cleanup?: "pending" | "complete" | null;
  message: string | null;
  error: string | null;
  progress: number | null;
  download_rate: number | null;
  eta_seconds: number | null;
  bytes_total: number | null;
  bytes_done: number | null;
  ingest_job_id: string | null;
  film_path: string | null;
  review: {
    videos: { relative_path: string; name: string; size: number }[];
    subtitles: { relative_path: string; excerpt: string; validation?: string | null }[];
    selected_video?: string | null;
  } | null;
  created_at: number;
  updated_at: number;
}

export interface AcquisitionStatus {
  downloader: { configured: boolean; available: boolean; message: string | null };
  search: { configured: boolean; available: boolean; message: string | null };
  monitor: { running: boolean; last_seen: string | number | null };
}

export interface Release {
  id: string;
  title: string;
  size: number | null;
  seeders: number | null;
  indexer: string;
}

export interface FilmMetadata { title: string; year: number; edition: string }
export type SubtitleDecision = { action: "auto" } | { action: "use"; relative_path: string } | { action: "skip" };
