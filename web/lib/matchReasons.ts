import { formatTime } from "./format";
import type { SearchMatch, SearchResult } from "@/types/api";

/**
 * Plain names for the text views a result can match, the same words the
 * player labels its details with (Scene, Shot, Picture). Product copy only.
 */
export const TEXT_VIEW_LABELS: Record<string, string> = {
  caption: "Picture",
  dialogue: "Dialogue",
  ocr: "On-screen text",
  facets: "Shot details",
  mood: "Mood",
  story: "Shot",
  scene: "Scene",
  legacy_combined_text: "Text",
};

const VALUE_WORDS: Record<string, string> = {
  extreme_close_up: "extreme close-up",
  close_up: "close-up",
  extreme_wide: "extreme wide",
  dawn_dusk: "dawn or dusk",
};

const DETAIL_KEYS: Record<string, string> = {
  framing: "Size",
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
  if (view === "facets" || view === "mood") return readableShotDetails(text);
  // Scene and shot text end each part with a period, even after a title's own ? or !.
  return text.replace(/([?!])\.(?=\s|$)/g, "$1");
}

/**
 * The finders a search can report, in the fixed order Details lists them.
 * A typed description is ranked by four retrieval channels (Visual, Semantic,
 * Lexical, Quote) and then checked by the Rerank model; each category adds
 * its own finder.
 */
export type MatchColumn = "img" | "txt" | "lex" | "quote" | "scene" | "words" | "look" | "composition" | "mood" | "rerank";

const COLUMN_ORDER: MatchColumn[] = ["img", "txt", "lex", "quote", "scene", "words", "look", "composition", "mood", "rerank"];

const COLUMN_LABELS: Record<MatchColumn, string> = {
  img: "Visual",
  txt: "Semantic",
  lex: "Lexical",
  quote: "Quote",
  scene: "Scene",
  words: "Words",
  look: "Look",
  composition: "Framing",
  mood: "Mood",
  rerank: "Rerank",
};

const COLUMN_HINTS: Record<MatchColumn, string> = {
  img: "Visual: image-text embedding. Rank among frames whose picture is closest to your words.",
  txt: "Semantic: text embedding. Rank among scenes whose picture, shot or scene text, dialogue, on-screen text or mood means the closest thing to your words.",
  lex: "Lexical: keyword search (BM25). A scene qualifies when its picture text or dialogue contains at least two of your words; rank by how well they match.",
  quote: "Quote: rank among spoken lines that match your words.",
  scene: "Rank in the Scene category",
  words: "Rank in the Words category",
  look: "Rank in the Look category",
  composition: "Rank in the Framing category",
  mood: "Rank in the Mood category",
  rerank: "Rerank: a cross-encoder's score for how well the top results fit your words, 0 to 1.",
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
  /** Lexical: the query words the scene's text shares, shown as tokens before the detail. */
  terms?: string[];
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
  // Each channel keeps only its top N scenes; a scene ranked lower is absent there.
  const below = (depth?: number) => (depth ? `not in its top ${depth}` : "not in its top results");
  const score = typeof mainDebug?.relevance === "number" ? mainDebug.relevance : undefined;
  const row = (column: MatchColumn, found: Partial<MatchRow> & { matched: boolean; detail: string }): MatchRow => ({
    column,
    label: COLUMN_LABELS[column],
    value: "–",
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
        ? "Picture (above)"
        : `${viewLabel}${text ? ` · ${readableEvidence(view, text)}` : ""}`;
      return row(column, { value: `#${txt.rank}`, rank: txt.rank, matched: true, detail });
    }
    if (column === "lex") {
      const lex = channels?.lex;
      if (!lex) return row(column, { matched: false, detail: "fewer than two of your words in its description or dialogue" });
      const fields = (lex.fields ?? []).map((field) => (TEXT_VIEW_LABELS[field] ?? field).toLowerCase());
      return row(column, {
        value: `#${lex.rank}`,
        rank: lex.rank,
        matched: true,
        terms: lex.terms ?? [],
        detail: fields.length ? `in ${fields.join(" and ")}` : "",
      });
    }
    if (column === "quote") {
      const quote = channels?.quote;
      return quote && shot.matched_line
        ? row(column, { value: `#${quote.rank}`, rank: quote.rank, matched: true, detail: `“${shot.matched_line.text}”` })
        : row(column, { matched: false, detail: "no matching spoken line" });
    }
    if (column === "rerank") {
      const verdict = channels?.rerank?.verdict;
      return typeof verdict === "number"
        ? row(column, { value: verdict.toFixed(2), matched: true, detail: "" })
        : row(column, { matched: false, detail: "not in the top 40 it scores" });
    }
    const match = (shot.matches ?? []).find((item) => item.facet === column);
    return match
      ? row(column, { value: `#${match.rank}`, rank: match.rank, matched: true, detail: evidenceDetail(match) })
      : row(column, { matched: false, detail: below(shot.debug?.depth) });
  });
  return { rows, score };
}

/** The rank within which a finder counts as having found a scene well. */
const FOUND_WELL = 30;

interface Evidence { kind: string; text: string; rank: number }

/**
 * The one line a result shows on hover, named by its kind. When a typed
 * search's words found the shot well, it is the spoken line or the text that
 * matched in meaning, whichever ranked the shot higher; otherwise it is what
 * happens in the shot, as context (else what the picture shows). A category
 * search has no finder ranks, so the words its clauses matched show as they are.
 */
export function hoverEvidence(shot: SearchResult): { kind: string; text: string } | null {
  const rows = matchBreakdown(shot).rows;
  const ranked = rows.some((row) => row.column === "txt" || row.column === "quote");
  const rankOf = (column: MatchColumn) => rows.find((row) => row.column === column && row.matched)?.rank ?? Infinity;
  const view = shot.matched_text_view ?? "";
  const meaning = shot.matched_text ? readableEvidence(view, shot.matched_text) : "";
  const words = [
    shot.matched_line ? { kind: TEXT_VIEW_LABELS.dialogue, text: `“${shot.matched_line.text}”`, rank: rankOf("quote") } : null,
    meaning ? { kind: TEXT_VIEW_LABELS[view] ?? "Text", text: view === "dialogue" ? `“${meaning}”` : meaning, rank: rankOf("txt") } : null,
  ].filter((item): item is Evidence => item !== null);
  const [best] = ranked ? words.filter((item) => item.rank <= FOUND_WELL).sort((a, b) => a.rank - b.rank) : words;
  if (best) return { kind: best.kind, text: best.text };
  if (shot.action) return { kind: TEXT_VIEW_LABELS.story, text: shot.action };
  return shot.caption ? { kind: TEXT_VIEW_LABELS.caption, text: shot.caption } : null;
}
