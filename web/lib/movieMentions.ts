import { displayFilmTitle, type MovieSuggestion } from "./movieSuggestions";
import type { LibraryFilm } from "@/types/api";

/** Confirmed catalog identity, anchored to exact UTF-16 native input offsets. */
export interface MovieMention {
  filmId: string;
  text: string;
  start: number;
  end: number;
}

export interface MovieSearchDraft {
  text: string;
  mentions: readonly MovieMention[];
}

export const EMPTY_MOVIE_DRAFT: MovieSearchDraft = { text: "", mentions: [] };

export function validMovieMentions(draft: MovieSearchDraft): MovieMention[] {
  let end = 0;
  return [...draft.mentions].sort((a, b) => a.start - b.start).filter((mention) => {
    if (!mention.filmId || mention.start < end || mention.start < 0 || mention.end <= mention.start
      || mention.end > draft.text.length || draft.text.slice(mention.start, mention.end) !== mention.text
      || !mention.text.startsWith("@")) return false;
    end = mention.end;
    return true;
  });
}

/** Preserve only ranges outside the native edit. Never infer new scope from text.
 * The resulting caret disambiguates edits to repeated adjacent text. */
export function editMovieText(draft: MovieSearchDraft, text: string, caret = text.length): MovieSearchDraft {
  if (text === draft.text) return draft;
  let start = 0, oldEnd = draft.text.length, newEnd = text.length;
  const prefixLimit = Math.min(draft.text.length, text.length, Math.max(0, caret));
  while (start < prefixLimit && draft.text[start] === text[start]) start += 1;
  while (oldEnd > start && newEnd > start && draft.text[oldEnd - 1] === text[newEnd - 1]) { oldEnd -= 1; newEnd -= 1; }
  const delta = newEnd - oldEnd;
  const mentions = validMovieMentions(draft).flatMap((mention) => {
    if (mention.end <= start) return [mention];
    if (mention.start >= oldEnd) return [{ ...mention, start: mention.start + delta, end: mention.end + delta }];
    return [];
  });
  return { text, mentions: validMovieMentions({ text, mentions }) };
}

/** Mask accepted titles before autocomplete, preserving all source offsets. */
export function movieCompletionText(draft: MovieSearchDraft): string {
  let text = draft.text;
  for (const mention of validMovieMentions(draft).reverse()) {
    text = text.slice(0, mention.start) + " ".repeat(mention.end - mention.start) + text.slice(mention.end);
  }
  return text;
}

export function acceptMovieMention(draft: MovieSearchDraft, suggestion: MovieSuggestion): { draft: MovieSearchDraft; caret: number } {
  const start = suggestion.titleStart, end = suggestion.titleEnd;
  const token = `@${suggestion.title}`;
  const suffix = draft.text.slice(end);
  const separator = !suffix || /^[\p{L}\p{N}@]/u.test(suffix) ? " " : "";
  const text = draft.text.slice(0, start) + token + separator + suffix;
  const caret = start + token.length + (separator || /^\s/.test(suffix) ? 1 : 0);
  const next = editMovieText(draft, text, caret);
  return { draft: { text, mentions: [...next.mentions, { filmId: suggestion.film.film_id, text: token, start, end: start + token.length }].sort((a, b) => a.start - b.start) }, caret };
}

/** Span to delete with a mention. Tidy removal (picker) also drops the "in"/"from"
 * that compileMovieDraft ignores, and any whitespace stranded at the end. */
function removalRange(text: string, mention: MovieMention, tidy: boolean): [number, number] {
  let start = mention.start, end = mention.end;
  if (tidy) start -= /\b(?:in|from)\s+$/i.exec(text.slice(0, start))?.[0].length ?? 0;
  if ((start === 0 || /\s/.test(text[start - 1])) && text[end] === " ") end += 1;
  if (/[,;:.!?]/.test(text[end] ?? "") && text[start - 1] === " ") start -= 1;
  if (tidy && end >= text.length) while (start > 0 && /\s/.test(text[start - 1])) start -= 1;
  return [start, end];
}

/** Atomic boundary deletion; ordinary range/inside-token edits remain native. */
export function removeMovieMention(draft: MovieSearchDraft, mention: MovieMention): { draft: MovieSearchDraft; caret: number } {
  const [start, end] = removalRange(draft.text, mention, false);
  const text = draft.text.slice(0, start) + draft.text.slice(end);
  return { draft: editMovieText(draft, text, start), caret: start };
}

/** Picker deselection removes the whole mention, so the visible search text and
 * its compiled query are unchanged and only the scope narrows. New picker
 * selections append mentions without moving existing prose. */
export function setMovieScope(draft: MovieSearchDraft, filmIds: readonly string[], films: readonly LibraryFilm[]): MovieSearchDraft {
  const wanted = new Set(filmIds);
  let next = draft;
  for (const mention of validMovieMentions(draft).reverse()) {
    if (wanted.has(mention.filmId)) continue;
    const [start, end] = removalRange(next.text, mention, true);
    next = editMovieText(next, next.text.slice(0, start) + next.text.slice(end), start);
  }
  const retained = new Set(next.mentions.map((mention) => mention.filmId));
  for (const filmId of wanted) {
    if (retained.has(filmId)) continue;
    const film = films.find((item) => item.film_id === filmId && item.status === "indexed");
    if (!film) continue;
    const token = `@${displayFilmTitle(film)}`;
    const prefix = next.text + (next.text && !/\s$/.test(next.text) ? " " : "");
    next = { text: prefix + token + " ", mentions: [...next.mentions, { filmId, text: token, start: prefix.length, end: prefix.length + token.length }] };
    retained.add(filmId);
  }
  return next;
}

/** Visible prose and catalog scope compile independently into existing search. */
export function compileMovieDraft(draft: MovieSearchDraft): { query: string; filmIds: string[] } {
  const mentions = validMovieMentions(draft);
  if (!mentions.length) return { query: draft.text, filmIds: [] };
  let query = draft.text;
  for (const mention of [...mentions].reverse()) {
    const before = query.slice(0, mention.start).replace(/\b(?:in|from)\s+$/i, "");
    query = before + query.slice(mention.end);
  }
  query = query.replace(/\s+/g, " ").replace(/\s+([,;:.!?])/g, "$1").trim().replace(/^[,;:]+\s*|\s*[,;:]+$/g, "");
  if (/^[\s\p{P}]*$/u.test(query)) query = "";
  return { query, filmIds: [...new Set(mentions.map((mention) => mention.filmId))] };
}
