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

/** One finder's verdict on a scene: where it ranked it, and on what. */
export interface MatchRow {
  /** Picture, Text, Rerank, or a category: Look, Framing, Scene, Words, Mood. */
  label: string;
  /** "#2", "0.52" for the rerank score, or "–" when this finder did not return it. */
  value: string;
  /** The rank, for finders that rank; drives the hover summary. */
  rank?: number;
  matched: boolean;
  /** What matched: the frame time, or the text view and its words. */
  detail: string;
  /** Short name for hover labels: Picture, Story, Dialogue, Look… */
  short: string;
  /** What this finder measures, for the label's tooltip. */
  hint: string;
}

export interface MatchBreakdown {
  rows: MatchRow[];
  /** The final ordering score, when the search reports one. */
  score?: number;
}

/** The per-finder ranking behind one clause, when the API reported it. */
function clauseChannels(shot: SearchResult, match: SearchMatch): SearchChannelsDebug | undefined {
  const debug = shot.debug;
  if (!debug) return undefined;
  if (debug.clauses) return debug.clauses[match.clause_id]?.channels;
  // A single-clause search reports its channels at the top level.
  return (shot.matches ?? []).length <= 1 ? debug.channels : undefined;
}

const NOT_MATCHED = "–";

function evidenceDetail(match: SearchMatch): string {
  const evidence = match.evidence;
  if (evidence?.type === "text") {
    return `${TEXT_VIEW_LABELS[evidence.view] ?? evidence.view} · ${readableEvidence(evidence.view, evidence.text)}`;
  }
  if (evidence?.type === "frame" && typeof evidence.timestamp === "number") return `frame ${formatTime(evidence.timestamp)}`;
  return "";
}

/**
 * How each finder ranked a scene. A typed description always reports the
 * same rows (Picture, Text, Rerank) so scenes compare at a glance; a finder
 * that did not return the scene shows "–". Each extra category adds its own
 * row with the scene's rank in that category.
 */
export function matchBreakdown(shot: SearchResult): MatchBreakdown {
  const rows: MatchRow[] = [];
  let score: number | undefined;
  for (const match of shot.matches ?? []) {
    const clause = CLAUSE_LABELS[match.facet] ?? match.facet;
    const channels = match.facet === "all" ? clauseChannels(shot, match) : undefined;
    if (!channels) {
      rows.push({ label: clause, value: `#${match.rank}`, rank: match.rank, matched: true, detail: evidenceDetail(match), short: clause, hint: `Rank among scenes matching ${clause}` });
      continue;
    }
    if (typeof shot.debug?.relevance === "number") score = shot.debug.relevance;
    const img = channels.img;
    const at = img?.matched_frame?.timestamp;
    rows.push({
      label: "Picture",
      value: img ? `#${img.rank}` : NOT_MATCHED,
      rank: img?.rank,
      matched: Boolean(img),
      detail: img ? (typeof at === "number" ? `frame ${formatTime(at)}` : "") : "not in picture results",
      short: "Picture",
      hint: "Rank among frames whose picture is closest to your words",
    });
    const txt = channels.txt;
    const view = txt?.matched_text?.view ?? txt?.source ?? "";
    const viewLabel = TEXT_VIEW_LABELS[view] ?? "Text";
    const text = txt?.matched_text?.text ?? (match.evidence?.type === "text" ? match.evidence.text : "");
    rows.push({
      label: "Text",
      value: txt ? `#${txt.rank}` : NOT_MATCHED,
      rank: txt?.rank,
      matched: Boolean(txt),
      detail: txt
        ? view === "caption" && text === shot.caption
          ? "Description (above)"
          : `${viewLabel}${text ? ` · ${readableEvidence(view, text)}` : ""}`
        : "not in text results",
      short: viewLabel,
      hint: "Rank among scenes whose descriptions, dialogue or story best match your words",
    });
    if (channels.lex) {
      rows.push({ label: "Keywords", value: `#${channels.lex.rank}`, rank: channels.lex.rank, matched: true, detail: "", short: "Keywords", hint: "Rank by exact word matches" });
    }
    if (channels.quote && shot.matched_line) {
      rows.push({ label: "Line", value: `#${channels.quote.rank}`, rank: channels.quote.rank, matched: true, detail: `“${shot.matched_line.text}”`, short: "Line", hint: "Rank among spoken lines matching your words" });
    }
    const verdict = channels.rerank?.verdict;
    rows.push({
      label: "Rerank",
      value: typeof verdict === "number" ? verdict.toFixed(2) : NOT_MATCHED,
      matched: typeof verdict === "number",
      detail: typeof verdict === "number" ? "" : "not reranked (only the top 40 are)",
      short: "Rerank",
      hint: "A second model's check of how well the scene fits your words, 0 to 1",
    });
  }
  return { rows, score };
}

/** What found a scene well (top 30 in that finder), for the hover label. */
export function foundBy(shot: SearchResult, limit = 3): string[] {
  const ranked = matchBreakdown(shot).rows
    .filter((row) => row.matched && typeof row.rank === "number")
    .sort((a, b) => (a.rank ?? 0) - (b.rank ?? 0));
  const strong = ranked.filter((row) => (row.rank ?? Infinity) <= 30);
  return Array.from(new Set((strong.length ? strong : ranked.slice(0, 1)).map((row) => row.short))).slice(0, limit);
}
