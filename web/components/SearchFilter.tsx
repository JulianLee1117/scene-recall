"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { LibraryFilm } from "@/types/api";
import {
  FILM_FACETS,
  activeFacets,
  clearFacet,
  describeFacet,
  facetOptions,
  filterableFilms,
  hasFilters,
  narrowScope,
  sameFilters,
  toggleExclude,
  toggleFilter,
  type FacetOption,
  type FilmFacetKey,
  type FilmFilters,
} from "@/lib/filmFilters";
import {
  activeShotFacets,
  chooseShotFilter,
  hasShotFilters,
  sameShotFilters,
  type ShotFacet,
  type ShotFilters,
} from "@/lib/shotFilters";
import { useDismiss } from "@/hooks/useDismiss";
import { useMenuFit } from "@/hooks/useMenuFit";
import ChoiceRow from "./ChoiceRow";
import DirectionIcon from "./DirectionIcon";

/** Every shot facet starts here: no narrowing. */
const ANY = { value: "", label: "Any" };

/** What the Filter menu shows: its overview, or one movie section's values. */
export type FilterView = "menu" | FilmFacetKey;

/** Everything the Filter menu applies at once. */
export interface SearchFilters {
  /** Which movies: resolved in the browser to the request's film scope. */
  film: FilmFilters;
  /** What is in the shot: sent with the request and applied inside retrieval. */
  shot: ShotFilters;
}

interface Section {
  key: FilmFacetKey;
  label: string;
  long: boolean;
  options: FacetOption[];
  /** Counts mean something for shared values; every movie counts once. */
  counted: boolean;
}

interface SearchFilterProps {
  /** Closed when null. Opens on the overview, or at a section from its chip. */
  view: FilterView | null;
  onViewChange: (view: FilterView | null) => void;
  films: LibraryFilm[];
  /** The server's shot facets; the Shots group appears once they load. */
  shotFacets: readonly ShotFacet[];
  /** What the search uses now. */
  applied: SearchFilters;
  /** Runs the search once with the menu's choices. */
  onApply: (filters: SearchFilters) => void;
  /** Movies @mentioned in the search text, which the filters narrow too. */
  mentionedFilmIds: readonly string[];
}

/**
 * Narrows a search by movie (era, genre, director, title) and by shot
 * (dialogue, size, people, camera, color, time, place). Movie sections drill
 * in; shot facets are a few values each, so they toggle in place. Choices
 * stay a draft, previewed by the movie counts, and apply together when the
 * menu closes (Apply, or a click away): one search however fast you pick.
 */
export default function SearchFilter({
  view,
  onViewChange,
  films,
  shotFacets,
  applied,
  onApply,
  mentionedFilmIds,
}: SearchFilterProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const catalog = useMemo(() => filterableFilms(films), [films]);
  const [draft, setDraft] = useState<SearchFilters | null>(null);
  const working = draft ?? applied;
  const changed = !sameFilters(working.film, applied.film) || !sameShotFilters(working.shot, applied.shot);
  const finish = () => {
    if (changed) onApply(working);
    setDraft(null);
    onViewChange(null);
  };
  useDismiss(view !== null, rootRef, finish, triggerRef);
  useMenuFit(view !== null, panelRef);
  // Closed from outside (going home) drops the draft; opening or changing
  // sections moves focus into the menu, unless a search field took it.
  useEffect(() => {
    if (view === null) setDraft(null);
    else if (!panelRef.current?.contains(document.activeElement)) panelRef.current?.focus({ preventScroll: true });
  }, [view]);

  const activeCount = activeFacets(applied.film).length + activeShotFacets(applied.shot, shotFacets).length;
  if (catalog.length === 0 && activeCount === 0) return null;

  const { film: filters, shot } = working;
  const scope = narrowScope(mentionedFilmIds, catalog, filters);
  const searched = scope.excludesAll ? 0 : scope.filmIds.length || catalog.length;
  const summary = searched === catalog.length ? `All ${catalog.length} movies` : `${searched} of ${catalog.length} movies`;
  const sections: Section[] = FILM_FACETS.map((facet) => ({
    key: facet.key,
    label: facet.label,
    long: Boolean(facet.long),
    options: facetOptions(catalog, filters, facet.key),
    counted: !facet.perFilm,
  }));
  const open = sections.find((section) => section.key === view);
  // Edits read the latest draft, so quick successive clicks all count.
  const edit = (change: (current: SearchFilters) => SearchFilters) => setDraft((current) => change(current ?? applied));

  return (
    <div className="toolbar-menu-root" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="toolbar-menu-trigger"
        aria-expanded={view !== null}
        aria-controls={panelId}
        aria-haspopup="dialog"
        title="Narrow the search by movie or by what is in the shot"
        onClick={() => (view ? finish() : onViewChange("menu"))}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" aria-hidden="true">
          <path d="M4 5h16l-6.2 7.4V18l-3.6 1.8v-7.4z" />
        </svg>
        <span>Filter</span>
        {activeCount > 0 && <span className="toolbar-menu-count">{activeCount}</span>}
        <DirectionIcon name="chevron-down" className="toolbar-menu-chevron" size={12} />
      </button>

      {view && (
        <div
          ref={panelRef}
          id={panelId}
          className={`toolbar-menu${shotFacets.length ? " has-shots" : ""}${open ? " is-section" : ""}`}
          role="dialog"
          aria-label="Filter the search"
          tabIndex={-1}
        >
          {/* Movies and Shots side by side; a Movies section opens in its own pane. */}
          <div className="toolbar-menu-panes">
            <div className="toolbar-menu-pane">
              {open ? (
                <SectionList
                  key={open.key}
                  section={open}
                  onBack={() => onViewChange("menu")}
                  onToggle={(value) => edit((current) => ({ ...current, film: toggleFilter(current.film, open.key, value) }))}
                  onExclude={(value) => edit((current) => ({ ...current, film: toggleExclude(current.film, open.key, value) }))}
                  onClear={() => edit((current) => ({ ...current, film: clearFacet(current.film, open.key) }))}
                />
              ) : (
                <div className="toolbar-menu-list is-scroll">
                  <p className="toolbar-menu-heading">Movies</p>
                  {sections.map((section) => {
                    const picked = { values: filters[section.key] ?? [], excluded: filters.exclude?.[section.key] ?? [] };
                    const set = picked.values.length + picked.excluded.length > 0;
                    return (
                      <button key={section.key} type="button" className="toolbar-menu-row" onClick={() => onViewChange(section.key)}>
                        <span className="toolbar-menu-row-text">
                          <span>{section.label}</span>
                          <span className={`toolbar-menu-value${set ? " is-set" : ""}`}>
                            {set ? describeFacet(picked) : "Any"}
                          </span>
                        </span>
                        <DirectionIcon name="chevron-right" className="toolbar-menu-chevron" size={12} />
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
            {shotFacets.length > 0 && (
              <div className="toolbar-menu-pane is-shots">
                <div className="toolbar-menu-list is-scroll">
                  <p className="toolbar-menu-heading">Shots</p>
                  {shotFacets.map((facet) => (
                    <ChoiceRow
                      key={facet.key}
                      label={facet.label}
                      options={[ANY, ...facet.values]}
                      value={shot[facet.key]?.[0] ?? ANY.value}
                      defaultValue={ANY.value}
                      onChange={(value) => edit((current) => ({ ...current, shot: chooseShotFilter(current.shot, facet.key, value) }))}
                    />
                  ))}
                </div>
              </div>
            )}
          </div>
          <footer className="toolbar-menu-foot">
            <span role="status">{summary}</span>
            {(hasFilters(filters) || hasShotFilters(shot)) && (
              <button type="button" className="toolbar-menu-link" onClick={() => setDraft({ film: {}, shot: {} })}>
                Clear all
              </button>
            )}
            <button type="button" className={`toolbar-menu-apply${changed ? " is-primary" : ""}`} onClick={finish}>
              {changed ? "Apply" : "Done"}
            </button>
          </footer>
        </div>
      )}
    </div>
  );
}

/** The no-entry mark: a value left out. */
const ExcludeMark = () => (
  <>
    <circle cx="12" cy="12" r="7.5" />
    <path d="m6.8 17.2 10.4-10.4" />
  </>
);

const fold = (text: string) => text.normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase();

/**
 * One section's values as a checklist: a click chooses a value; the ⊘ that
 * appears beside its count excludes it instead. Long lists get a search field.
 */
function SectionList({
  section,
  onBack,
  onToggle,
  onExclude,
  onClear,
}: {
  section: Section;
  onBack: () => void;
  onToggle: (value: string) => void;
  onExclude: (value: string) => void;
  onClear: () => void;
}) {
  const [query, setQuery] = useState("");
  const needle = fold(query.trim());
  const noun = section.label.toLowerCase();
  // Long lists leave out values no movie can match; short ones dim them.
  const shown = section.options.filter((option) =>
    (option.selected || option.excluded || !section.long || option.count > 0) &&
    (!needle || fold(option.value).includes(needle)));

  return (
    <>
      <header className="toolbar-menu-head">
        <button type="button" className="toolbar-menu-back" onClick={onBack} aria-label="Back to all filters">
          <DirectionIcon name="chevron-left" size={13} />
          <span>{section.label}</span>
        </button>
        {section.options.some((option) => option.selected || option.excluded) && (
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
          const name = option.value;
          const empty = option.count === 0 && !option.selected && !option.excluded;
          return (
            <div key={option.value} className={`toolbar-menu-choice${option.excluded ? " is-excluded" : ""}`}>
              <button
                type="button"
                className="toolbar-menu-option"
                aria-pressed={option.selected}
                aria-label={option.excluded ? `${name}, excluded` : undefined}
                disabled={empty}
                title={option.excluded ? `${name}: excluded; click to clear` : empty ? `${name}: no movies with your other filters` : name}
                onClick={() => onToggle(option.value)}
              >
                <span className="toolbar-menu-label">{name}</span>
                {/* A chosen value shows a check and an excluded one a no-entry mark, where the count was. */}
                {option.selected || option.excluded ? (
                  <svg className="toolbar-menu-check" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    {option.excluded ? <ExcludeMark /> : <path d="m5 12.5 4.5 4.5L19 7.5" />}
                  </svg>
                ) : section.counted && <span className="toolbar-menu-tally">{option.count}</span>}
              </button>
              {!option.excluded && (
                <button
                  type="button"
                  className="toolbar-menu-exclude"
                  aria-label={`Exclude ${name}`}
                  title={`Exclude ${name}`}
                  disabled={empty}
                  onClick={() => onExclude(option.value)}
                >
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
                    <ExcludeMark />
                  </svg>
                </button>
              )}
            </div>
          );
        })}
        {shown.length === 0 && (
          <p className="toolbar-menu-empty">{needle ? `No ${noun} matches “${query.trim()}”` : `No ${noun} fits your other filters`}</p>
        )}
      </div>
    </>
  );
}
