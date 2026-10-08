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

/**
 * The finders a search can report, in the fixed order Details lists them.
 * A typed description is ranked by Picture, Text, Exact words and Spoken
 * line, then checked by Rerank; each category adds its own finder.
 */
export type MatchColumn = "img" | "txt" | "lex" | "quote" | "scene" | "words" | "look" | "composition" | "mood" | "rerank";

const COLUMN_ORDER: MatchColumn[] = ["img", "txt", "lex", "quote", "scene", "words", "look", "composition", "mood", "rerank"];

const COLUMN_LABELS: Record<MatchColumn, string> = {
  img: "Picture",
  txt: "Text",
  lex: "Exact words",
  quote: "Spoken line",
  scene: "Scene",
  words: "Words",
  look: "Look",
  composition: "Framing",
  mood: "Mood",
  rerank: "Rerank",
};

const COLUMN_HINTS: Record<MatchColumn, string> = {
  img: "Rank among frames whose picture is closest to your words",
  txt: "Rank among scenes whose description, dialogue or story means the closest thing to your words",
  lex: "Rank among scenes whose description or dialogue contains your exact words",
  quote: "Rank among spoken lines that match your words",
  scene: "Rank in the Scene category",
  words: "Rank in the Words category",
  look: "Rank in the Look category",
  composition: "Rank in the Framing category",
  mood: "Rank in the Mood category",
  rerank: "A second model's score for how well the scene fits your words, 0 to 1",
};

/** One finder's verdict on a scene: where it ranked it, and on what. */
export interface MatchRow {
  column: MatchColumn;
  label: string;
  /** "#2", "0.52" for the rerank score, or "–" when this finder did not return it. */
  value: string;
  /** The rank, for finders that rank; drives the hover summary. */
  rank?: number;
  matched: boolean;
  /** What matched (frame time, text view and words), or why nothing did. */
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

/** The ranking detail behind one clause, when the API reported it. */
function clauseDebug(shot: SearchResult, match: SearchMatch) {
  const debug = shot.debug;
  if (!debug) return undefined;
  if (debug.clauses) return debug.clauses[match.clause_id];
  // A single-clause search reports its channels at the top level.
  return (shot.matches ?? []).length <= 1 ? debug : undefined;
}

function evidenceDetail(match: SearchMatch): string {
  const evidence = match.evidence;
  if (evidence?.type === "text") {
    return `${TEXT_VIEW_LABELS[evidence.view] ?? evidence.view} · ${readableEvidence(evidence.view, evidence.text)}`;
  }
  if (evidence?.type === "frame" && typeof evidence.timestamp === "number") return `frame ${formatTime(evidence.timestamp)}`;
  return "";
}

/** The finders present anywhere in a result set, in display order. */
export function matchColumns(results: SearchResult[]): MatchColumn[] {
  const present = new Set<MatchColumn>();
  for (const shot of results) {
    for (const match of shot.matches ?? []) {
      if (match.facet !== "all") {
        present.add(match.facet as MatchColumn);
        continue;
      }
      const channels = clauseDebug(shot, match)?.channels;
      if (!channels) continue;
      // Picture and Text always rank a typed description.
      present.add("img");
      present.add("txt");
      for (const column of ["lex", "quote", "rerank"] as const) {
        if (channels[column]) present.add(column);
      }
    }
  }
  return COLUMN_ORDER.filter((column) => present.has(column));
}

/**
 * How each finder ranked one scene: one row per column in a fixed order, so
 * every card in a search reads the same way. A finder that did not return
 * the scene says why: it ranked below that finder's cutoff.
 */
export function matchBreakdown(shot: SearchResult, columns: MatchColumn[] = matchColumns([shot])): MatchBreakdown {
  const main = (shot.matches ?? []).find((match) => match.facet === "all");
  const mainDebug = main ? clauseDebug(shot, main) : undefined;
  const channels = mainDebug?.channels;
  const below = (depth?: number) => (depth ? `below its top ${depth}` : "below its cutoff");
  const score = typeof mainDebug?.relevance === "number" ? mainDebug.relevance : undefined;
  const row = (column: MatchColumn, found: Partial<MatchRow> & { matched: boolean; detail: string }): MatchRow => ({
    column,
    label: COLUMN_LABELS[column],
    value: "–",
    short: COLUMN_LABELS[column],
    hint: COLUMN_HINTS[column],
    ...found,
  });

  const rows = columns.map((column): MatchRow => {
    if (column === "img") {
      const img = channels?.img;
      const at = img?.matched_frame?.timestamp;
      return img
        ? row(column, { value: `#${img.rank}`, rank: img.rank, matched: true, detail: typeof at === "number" ? `frame ${formatTime(at)}` : "" })
        : row(column, { matched: false, detail: below(mainDebug?.depth) });
    }
    if (column === "txt") {
      const txt = channels?.txt;
      if (!txt) return row(column, { matched: false, detail: below(mainDebug?.depth) });
      const view = txt.matched_text?.view ?? txt.source ?? "";
      const viewLabel = TEXT_VIEW_LABELS[view] ?? "Text";
      const text = txt.matched_text?.text ?? (main?.evidence?.type === "text" ? main.evidence.text : "");
      const detail = view === "caption" && text === shot.caption
        ? "Description (above)"
        : `${viewLabel}${text ? ` · ${readableEvidence(view, text)}` : ""}`;
      return row(column, { value: `#${txt.rank}`, rank: txt.rank, matched: true, detail, short: viewLabel });
    }
    if (column === "lex") {
      const lex = channels?.lex;
      return lex
        ? row(column, { value: `#${lex.rank}`, rank: lex.rank, matched: true, detail: "" })
        : row(column, { matched: false, detail: "your words are not in its text" });
    }
    if (column === "quote") {
      const quote = channels?.quote;
      return quote && shot.matched_line
        ? row(column, { value: `#${quote.rank}`, rank: quote.rank, matched: true, detail: `“${shot.matched_line.text}”` })
        : row(column, { matched: false, detail: "no matching line spoken" });
    }
    if (column === "rerank") {
      const verdict = channels?.rerank?.verdict;
      return typeof verdict === "number"
        ? row(column, { value: verdict.toFixed(2), matched: true, detail: "" })
        : row(column, { matched: false, detail: "only the top 40 are reranked" });
    }
    const match = (shot.matches ?? []).find((item) => item.facet === column);
    return match
      ? row(column, { value: `#${match.rank}`, rank: match.rank, matched: true, detail: evidenceDetail(match) })
      : row(column, { matched: false, detail: below(shot.debug?.depth) });
  });
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
