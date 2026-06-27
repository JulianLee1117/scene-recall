export interface SearchResult {
  unit_id: string;
  film_id: string;
  t_start: number;
  t_end: number;
  caption: string;
  keyframe_url: string;
  preview_url: string;
}

export interface SearchResponse {
  results: SearchResult[];
}

export interface LibraryFilm {
  filename: string;
  path: string;
  size_gb: number;
  status: "indexed" | "not_indexed";
}

export interface IngestJob {
  job_id: string;
  path: string;
  filename: string;
  status: "running" | "done" | "error";
  started_at: number;
  finished_at: number | null;
  error: string | null;
}

export interface IngestResponse {
  job_id: string;
  status: string;
}
