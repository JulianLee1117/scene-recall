import { formatTime } from "./format";
import type { SearchChannelsDebug, SearchMatch, SearchResult } from "@/types/api";

/** Plain names for the text views a result can match. Product copy only. */
export const TEXT_VIEW_LABELS: Record<string, string> = {
  caption: "Description",
  dialogue: "Dialogue",
  ocr: "On-screen text",
  facets: "Shot details",
  mood: "Mood",
  story: "Story",
  scene: "Scene",
  legacy_combined_text: "Text",
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

/** Shot details and mood are "key: value; …" lists; other views are prose. */
export function readableEvidence(view: string, text: string): string {
  return view === "facets" || view === "mood" ? readableShotDetails(text) : text;
}

/** 5 = among the very best for that finder, 1 = it barely made the list. */
export type Strength = 1 | 2 | 3 | 4 | 5;

export interface MatchRow {
  /** What found the scene: Picture, Story, Dialogue, Look, Framing… */
  label: string;
  strength: Strength;
  /** Full sentence for tooltips and screen readers, e.g. "Ranked 3rd by Picture". */
  note: string;
  /** What it matched: an excerpt, a quoted line or the frame time. */
  detail?: string;
}

export interface MatchBreakdown {
  /** How well the scene fits the description overall, when it was checked. */
  fit?: { strength: Strength; word: string };
  rows: MatchRow[];
}

export function ordinal(value: number): string {
  const tens = value % 100;
  const suffix = tens >= 11 && tens <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[value % 10] ?? "th";
  return `${value}${suffix}`;
}

export function rankStrength(rank: number): Strength {
  if (rank <= 3) return 5;
  if (rank <= 10) return 4;
  if (rank <= 30) return 3;
  if (rank <= 100) return 2;
  return 1;
}

function fitOf(verdict: number): { strength: Strength; word: string } {
  if (verdict >= 0.7) return { strength: 5, word: "Strong fit" };
  if (verdict >= 0.55) return { strength: 4, word: "Good fit" };
  if (verdict >= 0.4) return { strength: 3, word: "Fair fit" };
  if (verdict >= 0.2) return { strength: 2, word: "Loose fit" };
  return { strength: 1, word: "Weak fit" };
}

/** The per-finder ranking behind one clause, when the API reported it. */
function clauseChannels(shot: SearchResult, match: SearchMatch): SearchChannelsDebug | undefined {
  const debug = shot.debug;
  if (!debug) return undefined;
  if (debug.clauses) return debug.clauses[match.clause_id]?.channels;
  // A single-clause search reports its channels at the top level.
  return (shot.matches ?? []).length <= 1 ? debug.channels : undefined;
}

function rowFor(label: string, rank: number, detail?: string): MatchRow {
  return { label, strength: rankStrength(rank), note: `Ranked ${ordinal(rank)} by ${label}`, detail: detail || undefined };
}

function evidenceDetail(match: SearchMatch): string | undefined {
  const evidence = match.evidence;
  if (evidence?.type === "text") return readableEvidence(evidence.view, evidence.text);
  if (evidence?.type === "frame" && typeof evidence.timestamp === "number") return `Closest frame at ${formatTime(evidence.timestamp)}`;
  return undefined;
}

/**
 * Why a scene ranked where it did, in words: each finder that found it
 * (picture, a text view, exact words, a quoted line, or a reference clause),
 * how highly it ranked there, and what it matched. Strongest first.
 */
export function matchBreakdown(shot: SearchResult): MatchBreakdown {
  const rows: MatchRow[] = [];
  let verdict: number | undefined;
  for (const match of shot.matches ?? []) {
    const clause = CLAUSE_LABELS[match.facet] ?? match.facet;
    const channels = match.facet === "all" ? clauseChannels(shot, match) : undefined;
    if (match.facet === "all" && typeof channels?.rerank?.verdict === "number") verdict = channels.rerank.verdict;
    const found: MatchRow[] = [];
    if (channels?.quote && shot.matched_line) {
      found.push(rowFor("Line", channels.quote.rank, `“${shot.matched_line.text}”`));
    }
    if (channels?.txt) {
      const view = channels.txt.matched_text?.view ?? channels.txt.source ?? "";
      const text = channels.txt.matched_text?.text ?? (match.evidence?.type === "text" ? match.evidence.text : "");
      found.push(rowFor(TEXT_VIEW_LABELS[view] ?? "Text", channels.txt.rank, text ? readableEvidence(view, text) : undefined));
    }
    if (channels?.img) {
      const at = channels.img.matched_frame?.timestamp;
      found.push(rowFor("Picture", channels.img.rank, typeof at === "number" ? `Closest frame at ${formatTime(at)}` : undefined));
    }
    if (channels?.lex) found.push(rowFor("Exact words", channels.lex.rank));
    rows.push(...(found.length ? found : [rowFor(clause, match.rank, evidenceDetail(match))]));
  }
  rows.sort((a, b) => b.strength - a.strength);
  return { fit: verdict === undefined ? undefined : fitOf(verdict), rows };
}

/** Short names of what found a scene, strongest first, for the hover label. */
export function foundBy(shot: SearchResult, limit = 3): string[] {
  const { rows } = matchBreakdown(shot);
  const strong = rows.filter((row) => row.strength >= 3);
  return Array.from(new Set((strong.length ? strong : rows.slice(0, 1)).map((row) => row.label))).slice(0, limit);
}
