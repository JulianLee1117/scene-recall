import type { LibraryFilm } from "@/types/api";

export interface MovieSuggestion {
  film: LibraryFilm & { film_id: string };
  title: string;
  /** Original query offsets of the phrase removed on explicit acceptance. */
  start: number;
  end: number;
  /** Title (and optional @/quotes/year), excluding a preceding in/from. */
  titleStart: number;
  titleEnd: number;
  remainingQuery: string;
}

export function displayFilmTitle(film: LibraryFilm): string {
  const source = film.title?.trim() || film.filename;
  const cleaned = source
    .replace(/\.(mkv|mp4|mov|m4v|avi|webm)$/i, "")
    .replace(/^movie\s*[-–—]\s*/i, "")
    .trim();
  return cleaned || film.filename;
}

type Token = { text: string; start: number; end: number };

function tokens(text: string): Token[] {
  return Array.from(text.matchAll(/[\p{L}\p{N}][\p{L}\p{N}\p{M}]*/gu), (match) => ({
    text: match[0].normalize("NFD").replace(/\p{M}/gu, "").toLowerCase(),
    start: match.index!,
    end: match.index! + match[0].length,
  }));
}

const COMMON_TITLES = new Set(["her", "us", "it", "up"]);
const YEAR = /^(?:18|19|20)\d{2}$/;
/** Edition labels ("[Criterion]", "[Director's Cut]") are not part of a film's name. */
const EDITION = /(\s*\[[^\]]*\])+\s*$/;
const ARTICLES = new Set(["the", "a", "an"]);
/** A typed word may carry one typo from five letters, as in Meilisearch's defaults. */
const TYPO_LENGTH = 5;
const PUNCTUATION_ONLY = /^[\s\p{P}]*$/u;
const QUOTE_PAIRS = new Map([["\"", "\""], ["'", "'"], ["“", "”"], ["‘", "’"]]);

function identity(film: LibraryFilm, title: string) {
  const datedTitle = title.match(/\s+[([]((?:18|19|20)\d{2})[)\]]\s*$/);
  const name = datedTitle ? title.slice(0, datedTitle.index).trim() : title;
  // /library has no year field. Only use an explicit release year in the
  // catalog title or filename; never guess one from the user's query.
  const filenameYear = film.filename.match(/[([]((?:18|19|20)\d{2})[)\]](?:\.[^.]+)?$/);
  return { words: tokens(name), year: datedTitle?.[1] ?? filenameYear?.[1] };
}

/** Whether two words differ by at most one added, dropped, changed or swapped letter. */
function withinOneEdit(a: string, b: string): boolean {
  if (Math.abs(a.length - b.length) > 1) return false;
  let i = 0;
  while (i < a.length && i < b.length && a[i] === b[i]) i += 1;
  if (a.length > b.length) return a.slice(i + 1) === b.slice(i);
  if (a.length < b.length) return a.slice(i) === b.slice(i + 1);
  return a.slice(i + 1) === b.slice(i + 1) || (a[i] === b[i + 1] && a[i + 1] === b[i] && a.slice(i + 2) === b.slice(i + 2));
}

type Fits = (typed: string, word: string) => boolean;
const isWord: Fits = (typed, word) => typed === word;
const beginsWord: Fits = (typed, word) => word.startsWith(typed);
/** A long typed word that begins a title word with one typo, never in the first letter. */
const nearlyBeginsWord: Fits = (typed, word) => typed.length >= TYPO_LENGTH && typed[0] === word[0]
  && [typed.length - 1, typed.length, typed.length + 1].some((length) => length <= word.length && withinOneEdit(typed, word.slice(0, length)));

/** Whether each typed word fits a different title word: 0 in title order, 1 in any order. */
function wordOrder(typed: readonly string[], words: readonly string[], fits: Fits): 0 | 1 | null {
  let at = 0;
  if (typed.every((token) => {
    while (at < words.length && !fits(token, words[at])) at += 1;
    return at++ < words.length;
  })) return 0;
  const used = new Set<number>();
  const place = (i: number): boolean => i === typed.length || words.some((word, j) => {
    if (used.has(j) || !fits(typed[i], word)) return false;
    used.add(j);
    if (place(i + 1)) return true;
    used.delete(j);
    return false;
  });
  return place(0) ? 1 : null;
}

/**
 * How well the words typed after @ name a title (lower is better), or null
 * when they do not. Every typed word must count: each fits a different title
 * word, or is (the start of) the film's release year beside other words.
 *   0    the title starts with them ("the gra"; spaces optional: "grandbud")
 *   1    the same after a leading article ("fren": The French Dispatch)
 *   2, 3 each is a whole title word, in title order or not ("red")
 *   4, 5 each begins a title word, in title order or not ("buda", "hotel gra")
 *   6    the initials ("gbh", "lotr")
 *   7    a beginning with one typo in a word of five letters or more ("budapset")
 * A lone letter only starts a title; anywhere else it would match too much.
 */
function completionRank(typed: readonly string[], words: readonly string[], year: string | undefined): number | null {
  const isYear = (token: string) => Boolean(year && /^\d+$/.test(token) && year.startsWith(token));
  const named = typed.some((token) => !isYear(token)) ? typed.filter((token) => !isYear(token)) : typed;
  const body = ARTICLES.has(words[0]) && words.length > 1 ? words.slice(1) : words;
  const phrase = named.join(" "), joined = named.join("");
  if (words.join(" ").startsWith(phrase) || words.join("").startsWith(joined)) return 0;
  if (body.join(" ").startsWith(phrase) || body.join("").startsWith(joined)) return 1;
  if (joined.length < 2) return null;
  const whole = wordOrder(named, words, isWord);
  if (whole !== null) return 2 + whole;
  const begun = wordOrder(named, words, beginsWord);
  if (begun !== null) return 4 + begun;
  const initials = (list: readonly string[]) => list.map((word) => word[0]).join("");
  if (named.length === 1 && (initials(words).startsWith(joined) || initials(body).startsWith(joined))) return 6;
  return wordOrder(named, words, nearlyBeginsWord) !== null ? 7 : null;
}

function styleReference(query: string, start: number, end: number): boolean {
  return /\b(?:like|inspired\s+by|(?:style|look|vibe|mood)\s+of)\s+(?:(?:in|from)\s+)?["'“‘]?\s*$/i.test(query.slice(0, start))
    || /^["'”’]?\s*[-–—]\s*(?:like|inspired)\b/i.test(query.slice(end));
}

function markerStart(query: string, titleStart: number): number | null {
  const at = titleStart - 1;
  return query[at] === "@" && (at === 0 || /\s/.test(query[at - 1])) ? at : null;
}

function caretPosition(query: string, caret: number): number {
  return Number.isFinite(caret) ? Math.max(0, Math.min(query.length, Math.trunc(caret))) : query.length;
}

function activeMarker(query: string, caret: number): number | null {
  const before = query.slice(0, caret);
  const matches = Array.from(before.matchAll(/(?:^|\s)@(?=[^\s@]|$)/g));
  const last = matches.at(-1);
  if (!last) return null;
  const marker = last.index! + last[0].length - 1;
  return before.slice(marker + 1).includes("@") ? null : marker;
}

function insideEmail(query: string, start: number, end: number): boolean {
  const before = query.slice(0, start).match(/\S*$/)?.[0] ?? "";
  const after = query.slice(end).match(/^\S*/)?.[0] ?? "";
  return `${before}${query.slice(start, end)}${after}`.includes("@");
}

function acceptedSpan(query: string, start: number, end: number) {
  const closing = QUOTE_PAIRS.get(query[start - 1]);
  if (closing && query[end] === closing) { start -= 1; end += 1; }
  const titleStart = start, titleEnd = end;
  const localScope = query.slice(0, start).match(/\b(?:in|from)\s+$/i);
  if (localScope) start = localScope.index!;
  let before = query.slice(0, start).trimEnd();
  let after = query.slice(end).trimStart();
  // Remove separators left dangling by the accepted phrase, not other words.
  if (!after) before = before.replace(/[,;:]\s*$/, "");
  if (!before || (/[,;:]$/.test(before) && /^[,;:]/.test(after))) after = after.replace(/^[,;:]\s*/, "");
  const separator = before && after && !/^[,;:.!?]/.test(after) ? " " : "";
  const remaining = `${before}${separator}${after}`.trim();
  return { start, end, titleStart, titleEnd, remainingQuery: PUNCTUATION_ONLY.test(remaining) ? "" : remaining };
}

/** Complete titles, or explicit @ prefixes; acceptance alone changes text/scope. */
export function getMovieSuggestions(
  query: string,
  films: readonly LibraryFilm[],
  selectedFilmIds: readonly string[],
  caret: number = query.length,
): MovieSuggestion[] {
  const position = caretPosition(query, caret);
  const active = activeMarker(query, position);
  const queryWords = tokens(query);
  if (!queryWords.length) return [];
  const selected = new Set(selectedFilmIds);
  const matches: Array<MovieSuggestion & { words: number; exact: boolean; marker: number | null; rank: number }> = [];
  const recognizedTitles: Array<{ start: number; end: number; words: number }> = [];
  for (const film of films) {
    if (film.status !== "indexed" || !film.film_id) continue;
    const indexedFilm = film as LibraryFilm & { film_id: string };
    const title = displayFilmTitle(film).replace(EDITION, "").trim();
    const { words, year } = identity(film, title);
    if (!words.length) continue;
    for (let i = 0; i <= queryWords.length - words.length; i += 1) {
      if (!words.every((word, j) => word.text === queryWords[i + j].text)) continue;
      const first = queryWords[i];
      const last = queryWords[i + words.length - 1];
      const marker = markerStart(query, first.start);
      if (marker === null && insideEmail(query, first.start, last.end)) continue;
      // An unavailable edition of a longer recognized title must not fall
      // through to an unrelated shorter title contained inside its name.
      recognizedTitles.push({ start: first.start, end: last.end, words: words.length });
      const next = queryWords[i + words.length];
      const explicitYear = next && YEAR.test(next.text)
        && /^[\s()\[\]"'“”‘’.,-]*$/.test(query.slice(last.end, next.start)) ? next : undefined;
      if (explicitYear && explicitYear.text !== year) continue;
      let end = explicitYear?.end ?? last.end;
      if (explicitYear && /^[\s"'“”‘’]*[([]/.test(query.slice(last.end, explicitYear.start))) {
        const close = query.slice(end).match(/^\s*[)\]]/);
        if (close) end += close[0].length;
      }
      // @ is an explicit request to identify a movie, even after "like".
      if (marker === null && styleReference(query, first.start, end)) continue;
      const closingQuote = QUOTE_PAIRS.get(query[first.start - 1]);
      const quoted = Boolean(closingQuote && (closingQuote === query[end] || closingQuote === query[last.end]));
      const scoped = /\b(?:in|from)\s+["'“‘]?\s*$/i.test(query.slice(0, first.start));
      const wholeQuery = i === 0 && words.length + (explicitYear ? 1 : 0) === queryWords.length;
      if (words.length === 1 && COMMON_TITLES.has(words[0].text)
        && marker === null && !wholeQuery && !quoted && !scoped && !explicitYear) continue;
      const start = marker ?? (explicitYear && quoted && closingQuote === query[last.end] ? first.start - 1 : first.start);
      matches.push({ film: indexedFilm, title, ...acceptedSpan(query, start, end), words: words.length, exact: true, marker, rank: 0 });
    }
    // Completion belongs only to an explicit tag continuing to the caret.
    // Ordinary prose never offers partial titles or arbitrary defaults for @.
    const name = words.map((word) => word.text);
    if (active !== null) {
      const typed = tokens(query.slice(active + 1, position)).map((word) => word.text);
      if (!typed.length || typed.join(" ") === name.join(" ")) continue;
      const rank = completionRank(typed, name, year);
      if (rank === null) continue;
      matches.push({ film: indexedFilm, title, ...acceptedSpan(query, active, position), words: words.length, exact: false, marker: active, rank });
    }
  }
  // Editing inside an existing complete title replaces that entire mention,
  // including when a different prefix completion is chosen at that caret.
  const completeEnd = Math.max(0, ...matches.filter((match) => match.exact && match.marker === active
    && match.end >= position).map((match) => match.end));
  if (active !== null && completeEnd > position) {
    for (const match of matches) {
      if (!match.exact && match.marker === active) Object.assign(match, acceptedSpan(query, active, completeEnd));
    }
  }
  const isActive = (match: typeof matches[number]) => active !== null && match.marker === active
    && (match.end >= position || PUNCTUATION_ONLY.test(query.slice(match.end, position)));
  const closedMention = matches.some((match) => match.exact && match.marker === active && !isActive(match));
  const onlyActive = active !== null && (!closedMention || matches.some(isActive));
  // Complete titles first, longest first; then completions by how well they
  // match, shorter titles first.
  matches.sort((a, b) => Number(b.exact) - Number(a.exact)
    || (a.exact ? b.words - a.words : a.rank - b.rank || a.words - b.words)
    || a.start - b.start || a.title.localeCompare(b.title) || a.film.film_id.localeCompare(b.film.film_id));
  const seen = new Set<string>();
  return matches.filter((match) => {
    if (onlyActive && !isActive(match)) return false;
    if (selected.has(match.film.film_id) || seen.has(match.film.film_id)) return false;
    if (match.exact && recognizedTitles.some((other) => other.words > match.words
      && other.start < match.end && match.start < other.end)) return false;
    seen.add(match.film.film_id);
    return true;
  }).slice(0, 4).map(({ film, title, start, end, titleStart, titleEnd, remainingQuery }) => ({ film, title, start, end, titleStart, titleEnd, remainingQuery }));
}

/** Active @ span for the native-input overlay; pass the catalog to stop at a
 * complete title instead of highlighting following description text. */
export function getMovieMentionRange(
  query: string,
  caret: number = query.length,
  films: readonly LibraryFilm[] = [],
): { start: number; end: number } | null {
  const position = caretPosition(query, caret);
  const marker = activeMarker(query, position);
  if (marker === null) return null;
  const coversMarker = (match: MovieSuggestion) => match.start <= marker && match.end > marker;
  const current = getMovieSuggestions(query, films, [], position).filter((match) => coversMarker(match)
    && (match.end >= position || PUNCTUATION_ONLY.test(query.slice(match.end, position))));
  if (current.length) return { start: marker, end: Math.max(...current.map((match) => match.end)) };
  // At the marker itself only complete existing titles can match. This also
  // distinguishes an unknown prefix from a known title followed by prose.
  const completed = getMovieSuggestions(query, films, [], marker + 1).filter(coversMarker);
  if (completed.some((match) => match.end < position && !PUNCTUATION_ONLY.test(query.slice(match.end, position)))) return null;
  return { start: marker, end: position };
}
