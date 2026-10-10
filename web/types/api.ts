export type SearchChannel = "img" | "txt" | "lex" | "quote" | "spatial";
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

/** Ordering after relevance (backend pipeline/search/priors.py). */
export type RankingPreset = "balanced" | "famous" | "gems";

export interface SearchRecipeRequest {
  clauses: SearchRecipeClause[];
  film_ids?: string[];
  /** Requested authoritative result prefix. Omit for the backend default. */
  limit?: number;
  /** Balanced (default), famous moments first, or hidden gems first. */
  preset?: RankingPreset;
  /** Shot filters applied inside retrieval, e.g. { dialogue: ["none"] } (ADR-0114). */
  shot_filters?: Record<string, string[]>;
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

export interface SearchTextMatchEvidence {
  type: "text";
  view: string;
  text: string;
  /** Retrieval source of these words, scoped to this clause. */
  source?: "quote" | "semantic";
  /** Exact source-line range when quote retrieval supplied one. */
  t_start?: number;
  t_end?: number;
  /** Ordered word overlap for quote evidence, not a semantic similarity. */
  score?: number;
}

export type SearchMatchEvidence =
  | SearchTextMatchEvidence
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
  /** Lexical channel: the query words this scene's text shares. */
  terms?: string[];
  /** Lexical channel: where those words are, "caption" and/or "dialogue". */
  fields?: string[];
  matched_frame?: MatchedFrameDebug;
  matched_text?: {
    feature_id?: string;
    view?: string;
    text?: string;
    profile_id?: string;
  };
}

/** The cross-encoder's verdict on how well a scene fits the description (0-1). */
export interface SearchRerankDebug {
  log_odds?: number;
  verdict?: number;
}

export type SearchChannelsDebug = Partial<Record<SearchChannel, SearchChannelDebug>> & {
  rerank?: SearchRerankDebug;
};

export interface SearchClauseDebug {
  mode?: string;
  final_score?: number;
  relevance?: number | null;
  channels?: SearchChannelsDebug;
  /** How deep each finder looked; a missing channel ranked below this. */
  depth?: number;
}

/** Diagnostic ranking detail; recipe searches key `clauses` by clause id. */
export interface SearchDebug extends SearchClauseDebug {
  final_score: number;
  query_ranks?: Partial<Record<"reference" | "text", number>>;
  clauses?: Partial<Record<string, SearchClauseDebug>>;
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
  /** Display image: the shot's hero frame unless the visual match found it. */
  thumbnail_url?: string;
  hero_url?: string;
  hero_time?: number;
  /** Earned placements: an iconic moment, or a well-made shot people rarely see. */
  badges?: SearchBadge[];
  /** Famous line (Wikiquote) spoken in this shot. */
  famous_line?: string;
  /** What happens in the shot, from the understanding pass. */
  action?: string;
  characters?: string[];
  /** Most important instant inside the shot (film seconds). */
  peak_time?: number;
  /** The stretch showing the same picture as the action peak (never across a dissolve). */
  focus_start?: number;
  focus_end?: number;
  /** The dramatic scene this shot belongs to. */
  scene?: SceneContext;
  /** Other matching shots of the same scene, folded into this card. */
  scene_alternatives?: SceneAlternative[];
  /** Subtitle line matched by a quote-like query, with exact times. */
  matched_line?: MatchedLine;
}

export type SearchBadge = "iconic" | "gem";

export interface SceneContext {
  id: string;
  title: string;
  summary: string;
  t_start?: number;
  t_end?: number;
  shot_count?: number;
}

export interface SceneAlternative {
  unit_id: string;
  t_start: number;
  t_end: number;
  keyframe_url: string;
  keyframe_index: number;
  thumbnail_url?: string;
  preview_url?: string;
  /** The moment its thumbnail shows, when the shot has a chosen best frame. */
  hero_time?: number;
}

export interface MatchedLine {
  t_start: number;
  t_end: number;
  text: string;
  score: number;
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
  /** Filter facets from open metadata; absent until a film's metadata pass runs. */
  year?: number | null;
  directors?: string[];
  /** Genre families, e.g. "Crime", "Sci-fi" (pipeline/search/film_facets.py). */
  genres?: string[];
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
  /** Associated SRTs available for automatic validation or explicit selection. */
  subtitle_review_candidates: SubtitleReviewCandidate[];
}

export interface SubtitleReviewCandidate {
  /** Path relative to the configured incoming directory. */
  relative_path: string;
  filename: string;
  /** Short non-promotional preview; never persisted as metadata. */
  excerpt: string;
  /** Available validation result; final checks happen during import. */
  validation?: string | null;
}

export type SubtitleImportDecision =
  | { action: "auto" }
  | { action: "use_as_english"; relative_path: string }
  | { action: "skip" };

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
  subtitle_decision: SubtitleImportDecision | null;
}

export interface ImportFilmResponse {
  path: string;
  filename: string;
  subtitle_filename: string | null;
  job: IngestJob | null;
}

export type LibraryStorageCategoryId =
  | "source_films"
  | "derived_assets"
  | "indexes"
  | "user_state"
  | "incoming"
  | "managed_archive"
  | "models";

export interface LibraryStorageCategory {
  id: LibraryStorageCategoryId;
  label: string;
  bytes: number;
  file_count: number;
  paths: string[];
  incomplete: boolean;
}

export interface LibraryStorageVolume {
  id: string;
  label: string;
  bytes: number;
  file_count: number;
  free_bytes: number | null;
  total_capacity_bytes: number | null;
  incomplete: boolean;
}

export interface LibraryStorageSnapshot {
  measured_at: string;
  measurement: "logical_file_bytes";
  total_bytes: number;
  file_count: number;
  incomplete: boolean;
  categories: LibraryStorageCategory[];
  volumes: LibraryStorageVolume[];
  issues: Array<{ path: string; reason: string }>;
  omitted_issue_count: number;
  excluded: string[];
}

export interface LibraryStorageResponse {
  status: "scanning" | "ready" | "error";
  snapshot: LibraryStorageSnapshot | null;
  started_at: string | null;
  error: string | null;
}
