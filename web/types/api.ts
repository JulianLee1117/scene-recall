export type SearchChannel = "img" | "txt" | "lex" | "spatial";

export interface MatchedFrameDebug {
  frame_id?: string;
  frame_index?: number;
  timestamp?: number;
}

export interface SearchChannelDebug {
  rank: number;
  score: number;
  distance: number | null;
  source?: "frame" | "unit";
  matched_frame?: MatchedFrameDebug;
}

export interface SearchDebug {
  mode?: "reference_image";
  final_score: number;
  channels?: Partial<Record<SearchChannel, SearchChannelDebug>>;
}

export interface SearchResult {
  unit_id: string;
  film_id: string;
  /** Human-readable title joined from the films index by the API. */
  film_title?: string;
  t_start: number;
  t_end: number;
  caption: string;
  keyframe_url: string;
  preview_url: string;
  /** One-based rank in the backend's final result order. */
  rank?: number;
  /** Ranking internals are returned by newer backends and hidden by default. */
  debug?: SearchDebug;
  /** Exact keyframe selected by frame-level visual retrieval. */
  matched_frame_url?: string;
  matched_frame_index?: number;
  matched_frame_timestamp?: number;
}

export interface SearchResponse {
  results: SearchResult[];
}

export interface LibraryFilm {
  filename: string;
  path: string;
  size_gb: number;
  status: "indexed" | "not_indexed";
  /** Stable search identifier. Present for indexed films on newer backends. */
  film_id: string | null;
  /** Human-readable title extracted during ingestion. */
  title: string;
  /** Runtime in seconds. */
  duration: number | null;
}

export interface IngestJob {
  job_id: string;
  path: string;
  filename: string;
  status: "running" | "done" | "error";
  started_at: number;
  finished_at: number | null;
  error: string | null;
  /** Latest pipeline output line, e.g. "[annotate] 150/612". */
  progress?: string | null;
  /** Tail of the pipeline's output (newest last). */
  log?: string[];
}

export interface IngestResponse {
  job_id: string;
  status: string;
}
