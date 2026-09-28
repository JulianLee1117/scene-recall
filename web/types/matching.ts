import type { Crop, LabClip, MatchCandidate, MatchCohort } from "./lab";

export type MatchFocus = "auto" | "position" | "shape" | "subject" | "camera";
export interface MatchSearchRequest {
  cohort_id: string;
  reference: { unit_id: string; time: number; subject_point?: { x: number; y: number } | null; region?: Crop | null };
  focus: MatchFocus;
  timing: "fixed" | "nearby";
  film_ids: string[];
  include_source_film: boolean;
  min_incoming_seconds: number;
  allow_reframing: boolean;
}
export interface MatchCue {
  code: string;
  label: string;
  description: string;
  measurements?: Record<string, unknown>;
}
export interface SceneMatch extends MatchCandidate {
  frame_url?: string | null;
  cues?: MatchCue[];
  primary_cue?: string;
}
export interface MatchSearchResult {
  reference?: LabClip;
  reference_summary?: { kind: "people"; count: number };
  candidates?: SceneMatch[];
  candidate?: SceneMatch;
  coverage?: { source?: string; film_count?: number; frame_count?: number; window_count?: number; eligible_window_count?: number; refined_window_count?: number; [key: string]: unknown };
  evaluated_channels?: string[];
  notices?: string[];
  message?: string;
}
export interface MatchSearchJob {
  id: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | "interrupted";
  created_at?: number;
  started_at?: number | null;
  finished_at?: number | null;
  cancel_requested?: boolean;
  progress?: string | null;
  error?: string | null;
  result?: MatchSearchResult | null;
  request?: MatchSearchRequest;
  reference?: LabClip;
}
export interface MatchReference { reference: LabClip; bounds: { t_start: number; t_end: number } }
export type SearchCohort = MatchCohort & {
  position_ready?: boolean;
  library?: { ready: boolean; film_count: number; frame_count: number; unit_count: number; films: { film_id: string; title: string }[] };
};
