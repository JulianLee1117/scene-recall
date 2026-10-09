"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { LibraryFilm } from "@/types/api";
import {
  FILM_FACETS,
  activeFacets,
  facetOptions,
  filterFilms,
  filterableFilms,
  narrowScope,
  toggleFilter,
  type FacetOption,
  type FilmFilters,
} from "@/lib/filmFilters";
import { displayFilmTitle } from "@/lib/movieSuggestions";
import { displayTitle } from "@/lib/format";
import DirectionIcon from "./DirectionIcon";

// A list section shows this many values until you search it.
const LIST_IDLE = 6;
const LIST_MATCHES = 8;

interface SearchFilterProps {
  /** The panel opens from the trigger or from an active filter's chip. */
  open: boolean;
  onOpenChange: (open: boolean) => void;
  films: LibraryFilm[];
  filters: FilmFilters;
  onFiltersChange: (filters: FilmFilters) => void;
  /** Movies chosen by name: the search text's @mentions. */
  selectedFilmIds: readonly string[];
  onMoviesChange: (filmIds: string[]) => void;
}

/**
 * Narrows a search to movies by era, genre, director or title. The trigger
 * keeps one size and counts the active sections (ActiveFilters shows them);
 * the panel applies each change as it is made, with counts so a choice
 * never empties the library.
 */
export default function SearchFilter({
  open,
  onOpenChange: setOpen,
  films,
  filters,
  onFiltersChange,
  selectedFilmIds,
  onMoviesChange,
}: SearchFilterProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const catalog = useMemo(() => filterableFilms(films), [films]);
  const active = activeFacets(filters);

  useEffect(() => {
    if (!open) return;
    panelRef.current?.focus();
    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, setOpen]);

  if (catalog.length === 0 && active.length === 0) return null;

  const scope = narrowScope(selectedFilmIds, catalog, filters);
  const searched = scope.excludesAll ? 0 : scope.filmIds.length || catalog.length;
  const summary = searched === catalog.length
    ? `All ${catalog.length} movies`
    : `${searched} of ${catalog.length} movies`;

  // Movies are options too: the ones passing the filters can be picked.
  const passing = new Set(filterFilms(catalog, filters).map((film) => film.id));
  const chosen = new Set(selectedFilmIds);
  const movieOptions: FacetOption[] = catalog
    .map((film) => ({
      value: film.id,
      label: displayTitle(displayFilmTitle(film.film)),
      count: passing.has(film.id) ? 1 : 0,
      selected: chosen.has(film.id),
    }))
    .sort((a, b) => (a.label ?? "").localeCompare(b.label ?? "", undefined, { sensitivity: "base" }));
  // Few enough passing movies are worth showing outright.
  const movieIdle = passing.size <= LIST_MATCHES ? LIST_MATCHES : 0;

  const toggleMovie = (filmId: string) =>
    onMoviesChange(chosen.has(filmId) ? selectedFilmIds.filter((id) => id !== filmId) : [...selectedFilmIds, filmId]);
  const clearAll = () => {
    onFiltersChange({});
    if (selectedFilmIds.length) onMoviesChange([]);
  };

  return (
    <div className="search-filter" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className={`search-filter-trigger${active.length ? " is-active" : ""}`}
        aria-expanded={open}
        aria-controls={panelId}
        aria-haspopup="dialog"
        title="Narrow the search by era, genre, director or movie"
        onClick={() => setOpen(!open)}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" aria-hidden="true">
          <path d="M4 5h16l-6.2 7.4V18l-3.6 1.8v-7.4z" />
        </svg>
        <span>Filter</span>
        {active.length > 0 && <span className="search-filter-count">{active.length}</span>}
        <DirectionIcon name="chevron-down" className="search-filter-chevron" size={12} />
      </button>


      {open && (
        <div
          ref={panelRef}
          id={panelId}
          className="search-filter-panel"
          role="dialog"
          aria-label="Filter movies"
          tabIndex={-1}
        >
          <div className="search-filter-sections">
            {FILM_FACETS.map((facet) => (
              <FilterSection
                key={facet.key}
                label={facet.label}
                list={facet.kind === "list"}
                options={facetOptions(catalog, filters, facet.key)}
                onToggle={(value) => onFiltersChange(toggleFilter(filters, facet.key, value))}
              />
            ))}
            <FilterSection label="Movie" list idle={movieIdle} options={movieOptions} onToggle={toggleMovie} />
          </div>
          <footer className="search-filter-foot">
            <span role="status">{summary}</span>
            {(active.length > 0 || selectedFilmIds.length > 0) && (
              <button type="button" className="search-filter-clear" onClick={clearAll}>
                Clear
              </button>
            )}
            <button
              type="button"
              className="search-filter-done"
              onClick={() => {
                setOpen(false);
                triggerRef.current?.focus();
              }}
            >
              Done
            </button>
          </footer>
        </div>
      )}
    </div>
  );
}

const fold = (text: string) => text.normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase();

/** One facet: every option as a chip, or for long lists, a search field and the top few. */
function FilterSection({
  label,
  list = false,
  idle = LIST_IDLE,
  options,
  onToggle,
}: {
  label: string;
  list?: boolean;
  /** List options shown before searching. */
  idle?: number;
  options: FacetOption[];
  onToggle: (value: string) => void;
}) {
  const [query, setQuery] = useState("");
  const needle = fold(query.trim());
  // Chosen values stay visible; the rest are the top few, or the matches.
  const rest = options.filter((option) => !option.selected);
  const shown = !list ? options : [
    ...options.filter((option) => option.selected),
    ...(needle
      ? rest.filter((option) => fold(option.label ?? option.value).includes(needle)).slice(0, LIST_MATCHES)
      : rest.filter((option) => option.count > 0).slice(0, idle)),
  ];

  return (
    <section className="search-filter-section" aria-label={label}>
      <h3>{label}</h3>
      {list && (
        <label className="search-filter-find">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-4-4" />
          </svg>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={`Find a ${label.toLowerCase()}`}
            aria-label={`Find a ${label.toLowerCase()}`}
          />
        </label>
      )}
      {shown.length > 0 && (
        <div className="search-filter-options">
          {shown.map((option) => {
            const name = option.label ?? option.value;
            return (
              <button
                key={option.value}
                type="button"
                className="search-filter-option"
                aria-pressed={option.selected}
                disabled={!option.selected && option.count === 0}
                title={option.selected || option.count ? name : `${name}: no movies with your other filters`}
                onClick={() => onToggle(option.value)}
              >
                {name}
              </button>
            );
          })}
        </div>
      )}
      {list && needle && !shown.some((option) => !option.selected) && (
        <p className="search-filter-empty">No {label.toLowerCase()} matches “{query.trim()}”</p>
      )}
    </section>
  );
}
