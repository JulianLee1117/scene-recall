export interface ProjectInfo {
  schema_version: 1;
  scope: string;
  models: {
    visual_encoder: string;
    text_encoder: string;
    annotator: string;
    annotator_provider: string;
    annotator_image_detail: string;
    annotator_reasoning_effort: string;
    whisper: string;
  };
  thresholds: { subsegment_min_duration: number; flash_min_duration: number; keyframe_short_shot_s: number };
  retrieval: {
    weights: { img: number; txt: number; lex: number };
    candidate_limit: number;
    result_window: number;
    max_result_limit: number;
    diversity: { page_size: number; film_results_per_page_target: number; film_repeat_rank_strength: number };
  };
  ingest: { annotation_concurrency: number };
  lab: {
    music_provider: string;
    music_model: string;
    planner_model: string;
    music_prompt_version: string;
    planner_prompt_version: string;
    context_profile: string | null;
    footage_inspection: boolean;
    beat_checkpoint_configured: boolean;
    beat_device: string;
  };
}

export interface GuideStep {
  id: string;
  title: string;
  summary: string;
  detail: string[];
  method: string;
  model?: string;
  channels?: { title: string; description: string }[];
  output: string;
  sources: string[];
}

export interface GuideSection {
  id: string;
  title: string;
  summary: string;
  introduction: string;
  steps: GuideStep[];
  note: string;
}

export interface GlossaryTerm {
  term: string;
  definition: string;
}

export interface GlossaryGroup {
  id: string;
  title: string;
  summary: string;
  terms: GlossaryTerm[];
}
