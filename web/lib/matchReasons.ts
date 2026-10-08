import { formatTime } from "./format";
import type { SearchResult } from "@/types/api";

/** Plain names for the text views a result can match. Product copy only. */
export const TEXT_VIEW_LABELS: Record<string, string> = {
  caption: "Description",
  dialogue: "Dialogue",
  ocr: "On-screen text",
  facets: "Shot details",
  mood: "Mood",
  story: "Story",
  scene: "Scene",
};

/** What each search clause is called when explaining a match. */
const CLAUSE_LABELS: Record<string, string> = {
  all: "Your description",
  scene: "Scene",
  words: "Words",
  look: "Look",
  composition: "Framing",
  mood: "Mood",
};

const VALUE_WORDS: Record<string, string> = {
  extreme_close_up: "extreme close-up",
  close_up: "close-up",
  extreme_wide: "extreme wide",
  dawn_dusk: "dawn or dusk",
};

const DETAIL_KEYS: Record<string, string> = {
  framing: "Shot",
  "time of day": "Time",
  "camera movement": "Camera",
  palette: "Colors",
};

function readableValue(value: string): string {
  return value
    .split(/,\s*/)
    .map((part) => VALUE_WORDS[part.trim()] ?? part.trim().replaceAll("_", " "))
    .join(", ");
}

/** "framing: close_up; time of day: dawn_dusk" → "Shot: close-up · Time: dawn or dusk". */
export function readableShotDetails(text: string): string {
  return text
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const separator = part.indexOf(":");
      if (separator < 0) return readableValue(part);
      const key = part.slice(0, separator).trim().toLowerCase();
      const label = DETAIL_KEYS[key] ?? key.charAt(0).toUpperCase() + key.slice(1);
      return `${label}: ${readableValue(part.slice(separator + 1).trim())}`;
    })
    .join(" · ");
}

export function readableEvidence(view: string, text: string): string {
  return view === "facets" ? readableShotDetails(text) : text;
}

export interface MatchReason {
  label: string;
  text: string;
}

/**
 * Why a result came back, in words: the matched line, the best text evidence
 * and each search clause that found it. Ranks and scores stay out of the UI.
 */
export function matchReasons(shot: SearchResult): MatchReason[] {
  const reasons: MatchReason[] = [];
  const seen = new Set<string>();
  const add = (label: string, text: string) => {
    const clean = text.trim();
    if (!clean || seen.has(clean)) return;
    seen.add(clean);
    reasons.push({ label, text: clean });
  };

  if (shot.matched_line) {
    add(`Line at ${formatTime(shot.matched_line.t_start)}`, `“${shot.matched_line.text}”`);
  }
  for (const match of shot.matches ?? []) {
    const clause = CLAUSE_LABELS[match.facet] ?? match.facet;
    if (match.evidence?.type === "text") {
      const view = TEXT_VIEW_LABELS[match.evidence.view] ?? match.evidence.view;
      add(`${clause} · ${view}`, readableEvidence(match.evidence.view, match.evidence.text));
    } else if (match.evidence?.type === "frame") {
      // The clause may be typed words or a reference; either way the picture matched.
      const at = typeof match.evidence.timestamp === "number" ? ` at ${formatTime(match.evidence.timestamp)}` : "";
      add(clause, `Picture matches${at}`);
    }
  }
  if (shot.matched_text_view && shot.matched_text) {
    const view = TEXT_VIEW_LABELS[shot.matched_text_view] ?? shot.matched_text_view;
    add(view, readableEvidence(shot.matched_text_view, shot.matched_text));
  }
  return reasons;
}

/** Short names of the clauses that found a result, e.g. ["Your description", "Look"]. */
export function matchedClauseLabels(shot: SearchResult): string[] {
  return Array.from(new Set((shot.matches ?? []).map((match) => CLAUSE_LABELS[match.facet] ?? match.facet)));
}
