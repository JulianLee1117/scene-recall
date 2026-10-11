/**
 * Back and Forward on the search page, as through pages; the address stays "/".
 *
 * Each history entry is a screen: a search (what made it: the typed text and
 * movies, the Refine clues, the filters and, for an uploaded image, its name),
 * the tab, and the scene open in the player. Tabs, Home and the player move
 * through here, and Back and Forward show their screens the same way; a
 * search shows itself as its results arrive and is recorded when it ends. So
 * Back closes the player before it changes the search, and a search started
 * from the player returns to it.
 *
 * Results stay in memory, and each search remembers where each tab was left;
 * a search no longer held (after a reload) is searched again.
 */
import type { FilmFilters } from "./filmFilters";
import type { MovieSearchDraft } from "./movieMentions";
import type { MatchDrafts, RecipeImageInput } from "./searchRecipe";
import type { ShotFilters } from "./shotFilters";
import type { RecipeImageFacet, RecipeMatchFacet, ResolvedSourceEvidence, SearchResult } from "@/types/api";

export interface SearchSnapshot {
  draft: MovieSearchDraft;
  drafts: MatchDrafts;
  filmFilters: FilmFilters;
  shotFilters: ShotFilters;
  /** An uploaded image is only named here: files stay in memory. */
  image: { facet: RecipeImageFacet; label: string; size: number } | null;
}

export interface ResultWindow {
  hasMore: boolean;
  nextLimit: number | null;
  maxLimit?: number;
}

export interface SearchOutcome {
  results: SearchResult[];
  window: ResultWindow;
  evidence: Partial<Record<RecipeMatchFacet, ResolvedSourceEvidence>>;
  image: RecipeImageInput | null;
}

/** A screen to show, saying only what changes. */
export interface HistoryScreen {
  /** A different search (null: Home). Without an outcome it is searched again. */
  search?: { snapshot: SearchSnapshot | null; outcome: SearchOutcome | undefined };
  tab: string;
  shot: SearchResult | null;
  /** Where to scroll, when the page behind the player changes. */
  scrollY?: number;
}

export interface HistoryView {
  show(screen: HistoryScreen): void;
  /** The tab on screen, for a search's entry. */
  tab(): string;
  scrollY(): number;
}

export interface HistoryLike {
  readonly state: unknown;
  pushState(data: unknown, unused: string): void;
  replaceState(data: unknown, unused: string): void;
  back(): void;
}

export interface SearchHistory {
  /** On load: the page as it opened is the first entry; after a reload, its screen is shown again. */
  start(tab: string): void;
  /** Back or Forward arrived. */
  pop(): void;
  /** The search is about to change: remember where the page is, for the way back. */
  leave(): void;
  /** A finished search: a new entry, or the same one when nothing that made it changed. A failed search has no outcome. */
  record(snapshot: SearchSnapshot, outcome?: SearchOutcome): void;
  /** More results for the search on screen. */
  update(outcome: Partial<SearchOutcome>): void;
  /** Home: a fresh, empty search on this tab. */
  home(tab: string): void;
  switchTab(tab: string): void;
  /** Open a scene in the player; another while one is open replaces it. */
  openShot(shot: SearchResult): void;
  /** Close the player, stepping back over its entry. */
  closeShot(): void;
}

interface Entry {
  search: { id: string; snapshot: SearchSnapshot | null };
  tab: string;
  shot: SearchResult | null;
}

/**
 * History entries live under this field, beside Next.js's own fields, which
 * every write keeps. A new entry shape takes a new field, so a reload passes
 * older ones over.
 */
const FIELD = "sceneRecallV1";
/** Searches whose results stay in memory. */
const KEPT = 24;

/** The same key however the fields were ordered. */
export function stableKey(value: unknown): string {
  return JSON.stringify(value, (_key, item: unknown) =>
    item && typeof item === "object" && !Array.isArray(item)
      ? Object.fromEntries(Object.entries(item as Record<string, unknown>).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)))
      : item);
}

function randomId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
}

function isEntry(value: unknown): value is Entry {
  const entry = value as Partial<Entry> | null | undefined;
  return Boolean(entry && typeof entry.tab === "string" && typeof entry.search?.id === "string");
}

/** A search on a tab: the page behind the player. */
const place = (entry: Entry) => `${entry.search.id} ${entry.tab}`;

export function createSearchHistory(history: HistoryLike, view: HistoryView, newId: () => string = randomId): SearchHistory {
  const kept = new Map<string, SearchOutcome>();
  const positions = new Map<string, number>();
  let onScreen: Entry | null = null;
  let closing = false;

  const current = (): Entry | null => {
    const entry = (history.state as Record<string, unknown> | null | undefined)?.[FIELD];
    return isEntry(entry) ? entry : null;
  };
  const write = (method: "pushState" | "replaceState", entry: Entry) => {
    const state = history.state && typeof history.state === "object" ? history.state : {};
    history[method]({ ...state, [FIELD]: entry }, "");
  };
  const leave = () => {
    if (onScreen) positions.set(place(onScreen), view.scrollY());
  };
  const keep = (id: string, outcome: SearchOutcome) => {
    kept.delete(id);
    kept.set(id, outcome);
    while (kept.size > KEPT) kept.delete(kept.keys().next().value as string);
  };
  const show = (entry: Entry) => {
    const before = onScreen;
    onScreen = entry;
    const screen: HistoryScreen = { tab: entry.tab, shot: entry.shot };
    if (before?.search.id !== entry.search.id) screen.search = { snapshot: entry.search.snapshot, outcome: kept.get(entry.search.id) };
    if (!before || place(before) !== place(entry)) screen.scrollY = positions.get(place(entry)) ?? 0;
    view.show(screen);
  };
  const go = (entry: Entry) => {
    leave();
    write("pushState", entry);
    show(entry);
  };
  const atHome = (entry: Entry | null, tab: string) => entry?.search.snapshot === null && entry.tab === tab && !entry.shot;

  return {
    start(tab) {
      const entry = current();
      if (entry && !atHome(entry, tab)) {
        show(entry);
        return;
      }
      onScreen = entry ?? { search: { id: newId(), snapshot: null }, tab, shot: null };
      if (!entry) write("replaceState", onScreen);
    },
    pop() {
      closing = false;
      const entry = current();
      if (!entry) return;
      leave();
      show(entry);
    },
    leave,
    record(snapshot, outcome) {
      const entry = current();
      if (entry?.search.snapshot && stableKey(entry.search.snapshot) === stableKey(snapshot)) {
        if (outcome) keep(entry.search.id, outcome);
        else kept.delete(entry.search.id);
        return;
      }
      const search = { id: newId(), snapshot };
      if (outcome) keep(search.id, outcome);
      onScreen = { search, tab: view.tab(), shot: null };
      write("pushState", onScreen);
    },
    update(outcome) {
      const id = onScreen?.search.id;
      const previous = id === undefined ? undefined : kept.get(id);
      if (id !== undefined && previous) keep(id, { ...previous, ...outcome });
    },
    home(tab) {
      const fresh: Entry = { search: { id: newId(), snapshot: null }, tab, shot: null };
      if (!atHome(current(), tab)) {
        go(fresh);
        return;
      }
      // Home again starts it afresh, in place.
      write("replaceState", fresh);
      show(fresh);
    },
    switchTab(tab) {
      const entry = current();
      if (!entry) view.show({ tab, shot: null, scrollY: 0 });
      else if (entry.tab !== tab) go({ search: entry.search, tab, shot: null });
    },
    openShot(shot) {
      const entry = current();
      if (!entry) {
        view.show({ tab: view.tab(), shot });
        return;
      }
      if (!entry.shot) {
        go({ ...entry, shot });
        return;
      }
      write("replaceState", { ...entry, shot });
      show({ ...entry, shot });
    },
    closeShot() {
      const entry = current();
      view.show({ tab: entry?.tab ?? view.tab(), shot: null });
      if (!entry?.shot || closing) return;
      closing = true;
      history.back();
    },
  };
}
