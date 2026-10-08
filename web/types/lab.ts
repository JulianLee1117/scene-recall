export type ExperimentId = "music-sketch" | "visual-rhymes";
export type LabProjectDeletion = { deleted: string; cleanup_pending?: boolean };
export interface Crop {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface WorkerStatus {
  checked_at: number;
  workers: {
    role: "editor" | "ingest";
    state: "offline" | "stopping" | "busy" | "idle";
    online: boolean;
    mode: "serial" | "separate";
    pid: number | null;
    current_job: { id: string; kind: string; progress: string | null } | null;
    queued_count: number;
  }[];
}
export interface LabClip {
  id: string;
  film_id: string;
  unit_id?: string | null;
  title: string;
  source_start: number;
  source_end: number;
  locked: boolean;
  crop?: Crop | null;
  reference_time?: number | null;
  window_start?: number | null;
  window_end?: number | null;
  region?: Crop | null;
}
export interface LabDialogueClip {
  id: string;
  film_id: string;
  unit_id?: string | null;
  title: string;
  text: string;
  source_start: number;
  source_end: number;
  /** Absolute song time; independent of the video cuts. */
  start: number;
  gain_db: number;
  fade_in_seconds: number;
  fade_out_seconds: number;
  music_duck_db: number;
  source_audio_mode?: "original" | "voice_focus";
  duck_attack_seconds?: number;
  duck_release_seconds?: number;
}
export interface LabEffectSource {
  film_id: string;
  unit_id?: string | null;
  source_start: number;
  crop?: LabClip["crop"];
}
export interface LabEffect {
  id: string;
  kind: "overlay" | "lock_cut" | "zoom_through" | "punch" | "flash" | "echo" | "fill" | "panel" | "strips" | "screen";
  start: number;
  end: number;
  at?: number | null;
  source?: LabEffectSource | null;
  sources?: LabEffectSource[];
  region?: "full" | "eyes" | "mouth" | "face" | "subject";
  classes?: string[];
  edge?: "soft" | "hard" | null;
  matte?: string | null;
  rect?: { x: number; y: number; width: number; height: number } | null;
  turn?: number;
  settle?: boolean;
  align?: "eyes" | "subject" | "none";
  track?: boolean;
  blend?: "normal" | "screen" | "lighten" | "multiply" | "difference" | "luma";
  opacity?: number;
  attack?: number;
  release?: number;
  zoom?: number;
  strength?: number;
  /** Screen corners on the song clock (ADR-0108): top-left, top-right, bottom-right, bottom-left, output fractions. */
  quad?: { t: number; corners: [number, number][] }[];
  radius?: number;
  static?: number;
  push?: number | null;
  title?: string;
}
export interface LabDocument {
  schema_version: number;
  track: { id: string; name: string; duration: number } | null;
  passage: { start: number; end: number };
  brief: string;
  film_ids: string[];
  analysis: Record<string, unknown> | null;
  rhythm: Record<string, unknown> | null;
  clips: LabClip[];
  aspect_ratio: "16:9" | "9:16";
  fps: number;
  audio_fade_in_seconds?: number;
  audio_fade_out_seconds?: number;
  music_gain_db?: number;
  dialogue_clips?: LabDialogueClip[];
  /** Render-time effects on the song clock (ADR-0106); placed through the API, shown in renders only. */
  effects?: LabEffect[];
  music_timeline?: MusicPlan | null;
  direction_plan?: Record<string, unknown> | null;
  planner_settings?: PlannerSettings;
  song_context?: SongContext | null;
  visual_plan?: VisualPlan | null;
  editor_direction?: EditorDirection | null;
}
export interface EditorDirectionRange {
  id: string;
  start: number;
  end: number;
  instruction: string;
}
export interface EditorDirection {
  instruction: string;
  ranges: EditorDirectionRange[];
}
export interface PlannerSettings {
  pacing: "patient" | "balanced" | "kinetic" | "rapid";
  lyric_treatment: "ignore" | "literal" | "metaphorical" | "counterpoint";
  /** Recognizable <-> fresh footage (search ranking preset); absent means balanced. */
  footage?: "balanced" | "famous" | "gems";
  /** How strongly cuts prefer frames that match across the cut; absent means some. */
  match_cuts?: "off" | "some" | "many";
  /** Harness v2: the song's treatment chooses pacing, footage and match cuts; absent means false. */
  auto?: boolean;
}
export interface SongLyric {
  id: string;
  start: number;
  end: number;
  text: string;
  meaning: string;
}
export interface SongContext {
  track_id: string;
  notes: string;
  lyrics: SongLyric[];
}
export interface VisualPlan {
  arc: string;
  motifs: string;
  source: "user" | "ai";
}
export interface SongMeaning {
  vocal_status: "understood" | "partly_understood" | "unclear" | "no_vocals";
  summary: string;
  themes: string[];
  cues: { start: number; end: number; paraphrase: string; confidence: "low" | "medium" | "high" }[];
  uncertainty: string;
}
export type MusicFeedback =
  | "too_literal"
  | "too_similar"
  | "wrong_energy"
  | "unfinished_action";
export type MusicSearchFacet = "all" | "scene" | "words" | "look" | "mood";
export interface MusicSection {
  start: number;
  end: number;
  feeling: string;
  imagery: string;
  query: string;
  energy: number;
  search_facet?: MusicSearchFacet;
}
export interface MusicAlternative {
  clip: LabClip;
  film_title: string;
  reason?: string | null;
  search_evidence?: MusicMatchEvidence | null;
}
export interface MusicMatchEvidence {
  rank: number;
  matched_text: string;
  matched_text_view: string | null;
  matched_frame_index: number | null;
  matched_frame_timestamp: number | null;
  suggested_source_start?: number | null;
  matches: Record<string, unknown>[];
  channels: Record<string, unknown>;
}
export type MusicRecipeFacet = MusicSearchFacet | "composition";
export interface MusicSearchClause {
  kind: "text" | "source";
  facet: MusicRecipeFacet;
  text: string | null;
  reference_id: string | null;
}
export interface FrozenSearchReference {
  reference_id: string;
  clip_id: string;
  film_id: string;
  unit_id: string;
  frame_index: number;
  timestamp: number;
  source_start: number;
  source_end: number;
}
export interface MusicSearchPlan {
  clauses: MusicSearchClause[];
  unverified_requirements: string[];
  references?: FrozenSearchReference[];
}
export interface ResolvedMusicSearch extends MusicSearchPlan {
  capability_version: string;
  min_duration: number;
}
export interface MusicSearchCapabilities {
  version: string;
  facets: {
    facet: MusicRecipeFacet;
    label?: string;
    text_available: boolean | null;
    source_available: boolean | null;
    evidence: string;
  }[];
  recipe: {
    max_clauses: number;
    unique_facets: boolean;
    max_recipes_per_job: number;
    max_primitive_clauses_per_job: number;
    fusion: string;
  };
  source_scope: string;
  unit_scope: string;
  unsupported: string[];
  availability_note: string;
}
export interface MusicDirection {
  query: string;
  search_facet: MusicSearchFacet;
  purpose: string;
  music_cue: string;
  timing_note: string;
  search_plan?: MusicSearchPlan | null;
}
export interface MusicSlot {
  id: string;
  start: number;
  end: number;
  section_index: number;
  clip_id: string | null;
  alternatives: MusicAlternative[];
  reason: string | null;
  search_error: string | null;
  direction?: MusicDirection | null;
  direction_source?: "ai" | "user" | null;
  needs_direction?: boolean;
  feedback?: MusicFeedback | null;
  resolved_search?: ResolvedMusicSearch | null;
  search_evidence?: MusicMatchEvidence | null;
}
export interface MusicPlan {
  track_id: string;
  passage: { start: number; end: number };
  slots: MusicSlot[];
  provisional_timing?: { contract: string; fingerprint: string } | null;
}
export interface LabProject {
  id: string;
  name: string;
  experiment_id: ExperimentId;
  revision: number;
  document: LabDocument;
  created_at: string | number;
  updated_at: string | number;
}
export interface LabJob {
  worker_role?: "editor" | "ingest";
  id: string;
  project_id: string;
  kind: "generate" | "rhythm" | "analyze" | "plan" | "draft" | "render" | "match" | "match-preview" | "next-scene" | "next-scene-preview";
  status:
    "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
  base_revision: number;
  progress?: string | null;
  progress_steps?: string[];
  cancel_requested?: boolean;
  created_at?: string | number;
  started_at?: string | number | null;
  finished_at?: string | number | null;
  error?: string | null;
  result?: { output_url?: string; [key: string]: unknown } | null;
}
export interface LabExperiment {
  id: ExperimentId | "transitions";
  name: string;
  description: string;
  route?: string;
  project_route?: string | null;
  persistence?: "project" | "session";
  /** Frozen experiments stay runnable but get no new work; Labs lists them last. */
  status?: "experimental" | "frozen";
}

export interface NextSceneOptions {
  anchor_slot_id: string;
  intent: string;
  flexible_cut: boolean;
  inspect_frames: boolean;
}
export interface NextSceneScope {
  anchor_slot_id: string;
  next_slot_id: string;
  t0: number;
  t2: number;
  current_cut: number;
  passage_start: number;
  cut_min: number;
  cut_max: number;
  anchor: LabClip;
}
export interface NextSceneCandidate {
  id: string;
  cut: number;
  outgoing: LabClip;
  incoming: LabClip;
  incoming_authority: { film_id: string; unit_id: string; t_start: number; t_end: number };
  reason: string;
  film_title?: string;
  preview_ready: boolean;
  preview_url: string | null;
  preview_error?: string | null;
  search_evidence?: MusicMatchEvidence | null;
  unverified_requirements: string[];
}
export interface NextSceneResult {
  contract: string;
  scope: NextSceneScope;
  candidates: NextSceneCandidate[];
}
export interface NextSceneAdjustment {
  source_start: number;
  cut_time: number;
  crop: Crop | null;
  preview_job_id?: string;
}
export interface NextScenePreviewResult {
  next_scene_job_id: string;
  candidate: NextSceneCandidate;
}

export interface MatchOptions {
  cohort_id: string;
  reference_clip_id: string;
  mode: "image" | "movement";
  movement: "camera" | "subject";
  allow_reframing: boolean;
  focus?: "auto" | "subject" | "camera" | "image";
  timing?: "nearby" | "fixed";
  subject_point?: { x: number; y: number };
}
export interface MatchCandidate {
  id: string;
  film_title: string;
  evidence: string;
  outgoing: LabClip;
  incoming: LabClip;
  preview_ready?: boolean;
  preview_warning?: string;
  preview_url?: string | null;
  crop?: unknown;
}
export interface MatchCohort {
  id: string;
  shot_count: number;
  motion_count: number;
  films: { film_id: string; title: string }[];
  visual_ready: boolean;
  shape_ready?: boolean;
  motion_ready: boolean;
  subject_ready?: boolean;
  subject_note?: string;
  examples: {
    unit_id: string;
    film_id: string;
    t_start: number;
    t_end: number;
    caption: string;
  }[];
  motion_examples?: {
    unit_id: string;
    film_id: string;
    t_start: number;
    t_end: number;
    caption: string;
    reference_time: number;
  }[];
}
