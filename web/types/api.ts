export type SearchChannel = "img" | "txt" | "lex" | "spatial";
export type SearchFacet =
  | "all"
  | "scene"
  | "words"
  | "look"
  | "composition"
  | "mood";
export type RecipeMatchFacet = Exclude<SearchFacet, "all">;
export type RecipeTextFacet = Exclude<SearchFacet, "composition">;
export type RecipeImageFacet = Extract<
  RecipeMatchFacet,
  "look" | "composition"
>;

export interface RecipeSource {
  unit_id: string;
  frame_index?: number;
}

export type SearchRecipeClause =
  | {
      id: string;
      kind: "text";
      facet: RecipeTextFacet;
      text: string;
    }
  | {
      id: string;
      kind: "source";
      facet: RecipeMatchFacet;
      source: RecipeSource;
    }
  | {
      id: string;
      kind: "image";
      facet: RecipeImageFacet;
    };

export interface SearchRecipeRequest {
  clauses: SearchRecipeClause[];
  film_ids?: string[];
  /** Requested authoritative result prefix. Omit for the backend default. */
  limit?: number;
}

export type SourceInputEvidence =
  | {
      type: "text";
      view: "caption" | "dialogue" | "ocr" | "mood";
      text: string;
    }
  | {
      type: "frame";
      frame_index: number;
      mode: "global_visual" | "spatial_visual";
    }
  | {
      type: "image";
      mode: "global_visual" | "spatial_visual" | "global_spatial_visual";
    };

export type ResolvedRecipeSource =
  | RecipeSource
  | { kind: "uploaded_image" };

export interface ResolvedSourceEvidence {
  clause_id: string;
  facet: RecipeMatchFacet | RecipeImageFacet;
  source: ResolvedRecipeSource;
  adapter:
    | "caption"
    | "dialogue+ocr"
    | "mood"
    | "pe_global"
    | "pe_global+spatial_6x6";
  effective_text?: string;
  evidence: SourceInputEvidence[];
}

export type SearchMatchEvidence =
  | { type: "text"; view: string; text: string }
  | { type: "frame"; frame_index: number; timestamp?: number };

export interface SearchMatch {
  clause_id: string;
  facet: SearchFacet | RecipeImageFacet;
  rank: number;
  evidence?: SearchMatchEvidence;
}

export interface MatchedFrameDebug {
  frame_id?: string;
  frame_index?: number;
  timestamp?: number;
}

export interface SearchChannelDebug {
  rank: number;
  score: number;
  distance: number | null;
  source?: string;
  matched_frame?: MatchedFrameDebug;
  matched_text?: {
    feature_id?: string;
    view?: string;
    text?: string;
    profile_id?: string;
  };
}

export interface SearchDebug {
  mode?: "reference_image" | "reference_image_text";
  final_score: number;
  channels?: Partial<Record<SearchChannel, SearchChannelDebug>>;
  query_ranks?: Partial<Record<"reference" | "text", number>>;
  clauses?: Partial<
    Record<
      "reference" | "text",
      {
        mode?: string;
        final_score?: number;
        channels?: Partial<Record<SearchChannel, SearchChannelDebug>>;
      }
    >
  >;
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
  /** Exact frame displayed by keyframe_url and used by source recipe clauses. */
  keyframe_index: number;
  preview_url: string;
  /** One-based rank in the backend's final result order. */
  rank?: number;
  /** Ranking internals are returned by newer backends and hidden by default. */
  debug?: SearchDebug;
  /** Exact keyframe selected by frame-level visual retrieval. */
  matched_frame_url?: string;
  matched_frame_index?: number;
  matched_frame_timestamp?: number;
  /** Durable source moment supplied when a Saved scene is rehydrated. */
  evidence_timestamp?: number;
  /** Best independent text view supporting this result. */
  matched_text_view?: string;
  matched_text?: string;
  /** Per-clause evidence returned by modular recipe search. */
  matches?: SearchMatch[];
}

export interface SearchResponse {
  results: SearchResult[];
  /** Backend-defined diversity/display page size. Older APIs omit it. */
  display_batch_size?: number;
  /** Size of this authoritative result prefix. */
  limit: number;
  /** Largest result prefix this backend will return. */
  max_limit: number;
  /** Whether at least one more eligible result exists within max_limit. */
  has_more: boolean;
  /** Next prefix size to request, or null once this stream is exhausted. */
  next_limit: number | null;
}

export interface SearchRecipeResponse extends SearchResponse {
  /** Authoritative input derived from each dragged source scene. */
  source_evidence?: ResolvedSourceEvidence[];
}

export type BookmarkAvailability = "indexed" | "source_only" | "missing";

export interface BookmarkRecord {
  bookmark_id: string;
  film_id: string;
  film_title: string;
  source_unit_id: string;
  evidence_timestamp: number;
  frame_index?: number | null;
  created_at: string;
  availability: BookmarkAvailability;
  /** Current indexed scene resolved from the durable film + timestamp anchor. */
  scene: SearchResult | null;
}

export interface BookmarkResponse {
  bookmarks: BookmarkRecord[];
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

export interface IncomingFilm {
  /** Path relative to the configured incoming directory. */
  relative_path: string;
  /** Filename of the main movie selected from this incoming item. */
  filename: string;
  size_gb: number;
  suggested_title: string;
  suggested_year: number | null;
  suggested_edition: string | null;
  suggested_filename: string;
  /** Other video files in the torrent folder that will not be imported. */
  extra_video_count: number;
}

export interface IngestJob {
  job_id: string;
  path: string;
  filename: string;
  status: "queued" | "running" | "done" | "error";
  queued_at: number;
  started_at: number | null;
  finished_at: number | null;
  queue_position: number | null;
  error: string | null;
  /** Latest pipeline output line, e.g. "[annotate] 150/612". */
  progress?: string | null;
  /** Tail of the pipeline's output (newest last). */
  log?: string[];
}

export type IngestResponse = IngestJob;

export interface ImportFilmRequest {
  relative_path: string;
  title: string;
  year: number;
  edition: string | null;
  ingest: boolean;
  confirm_finished: true;
}

export interface ImportFilmResponse {
  path: string;
  filename: string;
  subtitle_filename: string | null;
  job: IngestJob | null;
}
