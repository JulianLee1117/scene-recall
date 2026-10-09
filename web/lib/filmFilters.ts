import type { LibraryFilm } from "@/types/api";
import { displayTitle } from "./format";
import { displayFilmTitle } from "./movieSuggestions";

/**
 * Film filters narrow a search to movies by era, genre, director or title.
 * They resolve to the film IDs the search API already scopes by, so ranking
 * is unchanged. Values within a facet are alternatives (1990s or 2000s); a
 * facet set to exclude keeps the movies with none of them (not Animation).
 * Facets combine (1990s and Crime). Movies @mentioned in the search text are
 * narrowed by these filters too.
 */
export type FilmFacetKey = "era" | "genre" | "director" | "movie";
export type FilmFilters = Partial<Record<FilmFacetKey, readonly string[]>> & {
  /** Facets whose values are left out rather than kept. */
  exclude?: readonly FilmFacetKey[];
};

export interface FilmFacet {
  key: FilmFacetKey;
  label: string;
  /** A long list gets a search field and leaves out values no movie can match. */
  long?: boolean;
  /** Each film is its own value (its title), so counts say nothing. */
  perFilm?: boolean;
}

/** The filter menu's sections, in order. A new facet is one entry plus its values below. */
export const FILM_FACETS: readonly FilmFacet[] = [
  { key: "era", label: "Era" },
  { key: "genre", label: "Genre", long: true },
  { key: "director", label: "Director", long: true },
  { key: "movie", label: "Movie", long: true, perFilm: true },
];

/** An indexed film with its filter values worked out once. */
export interface FilterableFilm {
  id: string;
  film: LibraryFilm;
  values: Record<FilmFacetKey, readonly string[]>;
}

export interface FacetOption {
  value: string;
  /** Films with this value that also pass the other facets' filters. */
  count: number;
  selected: boolean;
}

// Sparse early decades share one bucket, so no chip holds a single film.
const ERA_MIN_FILMS = 10;

const decadeOf = (year: number) => Math.floor(year / 10) * 10;

/**
 * The year before which films share one "Before …" era, or null when every
 * decade stands alone: the oldest decades merge until they hold enough films.
 */
export function earlyEraBoundary(years: readonly number[]): number | null {
  const counts = new Map<number, number>();
  for (const year of years) counts.set(decadeOf(year), (counts.get(decadeOf(year)) ?? 0) + 1);
  const decades = [...counts.keys()].sort((a, b) => a - b);
  let merged = 0;
  for (const [index, decade] of decades.entries()) {
    merged += counts.get(decade) ?? 0;
    if (merged >= ERA_MIN_FILMS) {
      // One decade needs no merging; merging every decade would leave one chip.
      return index === 0 || index === decades.length - 1 ? null : decade + 10;
    }
  }
  return null;
}

export function eraLabel(year: number, boundary: number | null): string {
  return boundary !== null && year < boundary ? `Before ${boundary}` : `${decadeOf(year)}s`;
}

export function filterableFilms(films: readonly LibraryFilm[]): FilterableFilm[] {
  const indexed = films.filter(
    (film): film is LibraryFilm & { film_id: string } => film.status === "indexed" && Boolean(film.film_id),
  );
  const boundary = earlyEraBoundary(indexed.flatMap((film) => (film.year ? [film.year] : [])));
  return indexed.map((film) => ({
    id: film.film_id,
    film,
    values: {
      era: film.year ? [eraLabel(film.year, boundary)] : [],
      genre: film.genres ?? [],
      director: film.directors ?? [],
      movie: [displayTitle(displayFilmTitle(film))],
    },
  }));
}

export const isExcluded = (filters: FilmFilters, key: FilmFacetKey) => Boolean(filters.exclude?.includes(key));

function passes(film: FilterableFilm, filters: FilmFilters, except?: FilmFacetKey): boolean {
  return FILM_FACETS.every(({ key }) => {
    const chosen = filters[key];
    if (key === except || !chosen?.length) return true;
    return chosen.some((value) => film.values[key].includes(value)) !== isExcluded(filters, key);
  });
}

/** Films passing every filter, or every filter but one facet's (for that facet's counts). */
export function filterFilms(
  films: readonly FilterableFilm[],
  filters: FilmFilters,
  except?: FilmFacetKey,
): FilterableFilm[] {
  return films.filter((film) => passes(film, filters, except));
}

const eraRank = (value: string) => (value.startsWith("Before") ? -Infinity : Number.parseInt(value, 10));
const byName = (a: FacetOption, b: FacetOption) => a.value.localeCompare(b.value, undefined, { sensitivity: "base" });

const OPTION_ORDER: Record<FilmFacetKey, (a: FacetOption, b: FacetOption) => number> = {
  era: (a, b) => eraRank(a.value) - eraRank(b.value),
  genre: byName,
  director: (a, b) => b.count - a.count || byName(a, b),
  movie: byName,
};

/**
 * Every value a facet has in the library, counted among films that pass the
 * other facets' filters. A value with no such film stays listed (so options
 * never jump around) but can't narrow anything.
 */
export function facetOptions(films: readonly FilterableFilm[], filters: FilmFilters, key: FilmFacetKey): FacetOption[] {
  const chosen = new Set(filters[key] ?? []);
  const counts = new Map<string, number>(films.flatMap((film) => film.values[key].map((value) => [value, 0] as const)));
  for (const film of filterFilms(films, filters, key)) {
    for (const value of film.values[key]) counts.set(value, (counts.get(value) ?? 0) + 1);
  }
  return [...counts]
    .map(([value, count]) => ({ value, count, selected: chosen.has(value) }))
    .sort(OPTION_ORDER[key]);
}

/** With *key* holding *values*, or none; its include or exclude setting stays. */
function withValues(filters: FilmFilters, key: FilmFacetKey, values: readonly string[]): FilmFilters {
  const rest = { ...filters };
  delete rest[key];
  return values.length ? { ...rest, [key]: values } : rest;
}

export function toggleFilter(filters: FilmFilters, key: FilmFacetKey, value: string): FilmFilters {
  const current = filters[key] ?? [];
  return withValues(filters, key, current.includes(value) ? current.filter((item) => item !== value) : [...current, value]);
}

/** Whether *key* leaves out its values (true) or keeps them (false). */
export function excludeFacet(filters: FilmFilters, key: FilmFacetKey, exclude: boolean): FilmFilters {
  const others = (filters.exclude ?? []).filter((item) => item !== key);
  const next: FilmFilters = { ...filters, exclude: exclude ? [...others, key] : others };
  if (!next.exclude?.length) delete next.exclude;
  return next;
}

/** Without *key*'s values; *keepMode* leaves its include or exclude setting as it was. */
export function clearFacet(filters: FilmFilters, key: FilmFacetKey, keepMode = false): FilmFilters {
  const cleared = withValues(filters, key, []);
  return keepMode ? cleared : excludeFacet(cleared, key, false);
}

/** Active filters grouped by facet in panel order: one summary chip each. */
export function activeFacets(filters: FilmFilters): Array<FilmFacet & { values: readonly string[]; exclude: boolean }> {
  return FILM_FACETS.flatMap((facet) => (filters[facet.key]?.length
    ? [{ ...facet, values: filters[facet.key]!, exclude: isExcluded(filters, facet.key) }]
    : []));
}

/** A facet's chosen values as one line: "1990s, 2000s", or "not Animation" when excluded. */
export function describeFacet({ values, exclude }: { values: readonly string[]; exclude: boolean }): string {
  return `${exclude ? "not " : ""}${values.join(", ")}`;
}

export function hasFilters(filters: FilmFilters): boolean {
  return activeFacets(filters).length > 0;
}

/** Whether two filter sets narrow the same way: same values and, where any, the same mode. */
export function sameFilters(a: FilmFilters, b: FilmFilters): boolean {
  return FILM_FACETS.every(({ key }) => {
    const left = a[key] ?? [], right = b[key] ?? [];
    return left.length === right.length && left.every((value) => right.includes(value))
      && (!left.length || isExcluded(a, key) === isExcluded(b, key));
  });
}

/**
 * The movies a search covers: the @mentioned ones, or the whole library,
 * narrowed by the filters. Empty `filmIds` without `excludesAll` means the
 * whole library; `excludesAll` means the filters leave nothing to search.
 */
export function narrowScope(
  mentioned: readonly string[],
  films: readonly FilterableFilm[],
  filters: FilmFilters,
): { filmIds: readonly string[]; excludesAll: boolean } {
  if (!hasFilters(filters) || films.length === 0) return { filmIds: mentioned, excludesAll: false };
  const allowed = filterFilms(films, filters);
  if (!mentioned.length && allowed.length === films.length) return { filmIds: [], excludesAll: false };
  const allowedIds = new Set(allowed.map((film) => film.id));
  const filmIds = mentioned.length ? mentioned.filter((id) => allowedIds.has(id)) : allowed.map((film) => film.id);
  return { filmIds, excludesAll: filmIds.length === 0 };
}
