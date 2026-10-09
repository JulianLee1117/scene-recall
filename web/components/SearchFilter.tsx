"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import type { LibraryFilm } from "@/types/api";
import {
  FILM_FACETS,
  activeFacets,
  clearFacet,
  facetOptions,
  filterFilms,
  filterableFilms,
  narrowScope,
  toggleFilter,
  type FacetOption,
  type FilmFacetKey,
  type FilmFilters,
} from "@/lib/filmFilters";
import { displayFilmTitle } from "@/lib/movieSuggestions";
import { displayTitle } from "@/lib/format";
import { useDismiss } from "@/hooks/useDismiss";
import DirectionIcon from "./DirectionIcon";

/** What the Filter menu shows: its overview, or one section's values. */
export type FilterView = "menu" | FilmFacetKey | "movie";

interface Section {
  key: FilmFacetKey | "movie";
  label: string;
  long: boolean;
  options: FacetOption[];
  /** Counts mean something for facets; every movie counts once. */
  counted: boolean;
}

interface SearchFilterProps {
  /** Closed when null. Opens on the overview, or at a section from its chip. */
  view: FilterView | null;
  onViewChange: (view: FilterView | null) => void;
  films: LibraryFilm[];
  filters: FilmFilters;
  onFiltersChange: (filters: FilmFilters) => void;
  /** Movies chosen by name: the search text's @mentions. */
  selectedFilmIds: readonly string[];
  onMoviesChange: (filmIds: string[]) => void;
}

/**
 * Narrows a search to movies by era, genre, director or title. The menu
 * opens on one row per section with its current choice, and drills into a
 * section to pick values. Each change applies at once; counts keep a choice
 * from emptying the library.
 */
export default function SearchFilter({
  view,
  onViewChange,
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
  const close = useCallback(() => onViewChange(null), [onViewChange]);
  useDismiss(view !== null, rootRef, close, triggerRef);
  // Opening or changing sections moves focus into the menu, unless a search field took it.
  useEffect(() => {
    if (view && !panelRef.current?.contains(document.activeElement)) panelRef.current?.focus();
  }, [view]);

  const active = activeFacets(filters);
  if (catalog.length === 0 && active.length === 0) return null;

  const scope = narrowScope(selectedFilmIds, catalog, filters);
  const searched = scope.excludesAll ? 0 : scope.filmIds.length || catalog.length;
  const summary = searched === catalog.length ? `All ${catalog.length} movies` : `${searched} of ${catalog.length} movies`;

  // Movies are a section too: those passing the filters can be picked.
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
  const sections: Section[] = [
    ...FILM_FACETS.map((facet) => ({
      key: facet.key,
      label: facet.label,
      long: Boolean(facet.long),
      options: facetOptions(catalog, filters, facet.key),
      counted: true,
    })),
    { key: "movie", label: "Movie", long: true, options: movieOptions, counted: false },
  ];
  const open = sections.find((section) => section.key === view);

  const toggle = (section: Section, value: string) => {
    if (section.key !== "movie") return onFiltersChange(toggleFilter(filters, section.key, value));
    onMoviesChange(chosen.has(value) ? selectedFilmIds.filter((id) => id !== value) : [...selectedFilmIds, value]);
  };
  const clear = (section: Section) =>
    section.key === "movie" ? onMoviesChange([]) : onFiltersChange(clearFacet(filters, section.key));
  const clearAll = () => {
    onFiltersChange({});
    if (selectedFilmIds.length) onMoviesChange([]);
  };

  return (
    <div className="toolbar-menu-root" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="toolbar-menu-trigger"
        aria-expanded={view !== null}
        aria-controls={panelId}
        aria-haspopup="dialog"
        title="Narrow the search by era, genre, director or movie"
        onClick={() => onViewChange(view ? null : "menu")}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" aria-hidden="true">
          <path d="M4 5h16l-6.2 7.4V18l-3.6 1.8v-7.4z" />
        </svg>
        <span>Filter</span>
        {active.length > 0 && <span className="toolbar-menu-count">{active.length}</span>}
        <DirectionIcon name="chevron-down" className="toolbar-menu-chevron" size={12} />
      </button>

      {view && (
        <div ref={panelRef} id={panelId} className="toolbar-menu" role="dialog" aria-label="Filter movies" tabIndex={-1}>
          {open ? (
            <SectionList
              key={open.key}
              section={open}
              onBack={() => onViewChange("menu")}
              onToggle={(value) => toggle(open, value)}
              onClear={() => clear(open)}
            />
          ) : (
            <>
              <div className="toolbar-menu-list">
                {sections.map((section) => {
                  const picked = section.options.filter((option) => option.selected);
                  return (
                    <button key={section.key} type="button" className="toolbar-menu-row" onClick={() => onViewChange(section.key)}>
                      <span>{section.label}</span>
                      <span className={`toolbar-menu-value${picked.length ? " is-set" : ""}`}>
                        {picked.length ? picked.map((option) => option.label ?? option.value).join(", ") : "Any"}
                      </span>
                      <DirectionIcon name="chevron-right" className="toolbar-menu-chevron" size={12} />
                    </button>
                  );
                })}
              </div>
              <footer className="toolbar-menu-foot">
                <span role="status">{summary}</span>
                {(active.length > 0 || selectedFilmIds.length > 0) && (
                  <button type="button" className="toolbar-menu-link" onClick={clearAll}>
                    Clear all
                  </button>
                )}
              </footer>
            </>
          )}
        </div>
      )}
    </div>
  );
}

const fold = (text: string) => text.normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase();

/** One section's values as a checklist; long lists get a search field. */
function SectionList({
  section,
  onBack,
  onToggle,
  onClear,
}: {
  section: Section;
  onBack: () => void;
  onToggle: (value: string) => void;
  onClear: () => void;
}) {
  const [query, setQuery] = useState("");
  const needle = fold(query.trim());
  const noun = section.label.toLowerCase();
  // Long lists leave out values no movie can match; short ones dim them.
  const shown = section.options.filter((option) =>
    (option.selected || !section.long || option.count > 0) &&
    (!needle || fold(option.label ?? option.value).includes(needle)));

  return (
    <>
      <header className="toolbar-menu-head">
        <button type="button" className="toolbar-menu-back" onClick={onBack} aria-label="Back to all filters">
          <DirectionIcon name="chevron-left" size={13} />
          <span>{section.label}</span>
        </button>
        {section.options.some((option) => option.selected) && (
          <button type="button" className="toolbar-menu-link" onClick={onClear}>Clear</button>
        )}
      </header>
      {section.long && (
        <label className="toolbar-menu-find">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <circle cx="11" cy="11" r="7" />
            <path d="m20 20-4-4" />
          </svg>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={`Find a ${noun}`}
            aria-label={`Find a ${noun}`}
            autoFocus
          />
        </label>
      )}
      <div className="toolbar-menu-list is-scroll" role="group" aria-label={section.label}>
        {shown.map((option) => {
          const name = option.label ?? option.value;
          return (
            <button
              key={option.value}
              type="button"
              className="toolbar-menu-option"
              aria-pressed={option.selected}
              disabled={!option.selected && option.count === 0}
              title={option.selected || option.count ? name : `${name}: no movies with your other filters`}
              onClick={() => onToggle(option.value)}
            >
              <span className="toolbar-menu-label">{name}</span>
              {/* Selected shows a check where the count was; nothing reserves space on the left. */}
              {option.selected ? (
                <svg className="toolbar-menu-check" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="m5 12.5 4.5 4.5L19 7.5" />
                </svg>
              ) : section.counted && <span className="toolbar-menu-tally">{option.count}</span>}
            </button>
          );
        })}
        {shown.length === 0 && (
          <p className="toolbar-menu-empty">{needle ? `No ${noun} matches “${query.trim()}”` : `No ${noun} fits your other filters`}</p>
        )}
      </div>
    </>
  );
}
