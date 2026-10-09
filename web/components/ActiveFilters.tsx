"use client";

import { activeFacets, clearFacet, type FilmFilters } from "@/lib/filmFilters";

/**
 * The active film filters, one chip per section ("Era 1990s, 2000s"), in the
 * row under the toolbar beside the active refinements. A chip opens the
 * Filter panel; its × clears that section.
 */
export default function ActiveFilters({
  filters,
  onFiltersChange,
  onEdit,
  movieCount,
}: {
  filters: FilmFilters;
  onFiltersChange: (filters: FilmFilters) => void;
  onEdit: () => void;
  /** Movies left to search, shown after the chips. */
  movieCount: number;
}) {
  const facets = activeFacets(filters);
  if (facets.length === 0) return null;
  return (
    <>
      {facets.map(({ key, label, values }) => (
        <span key={key} className="filter-summary">
          <button
            type="button"
            className="filter-summary-edit"
            aria-label={`Edit ${label}: ${values.join(", ")}`}
            title={`${label}: ${values.join(", ")}`}
            onClick={onEdit}
          >
            <span className="clue-summary-label">{label}</span>
            <span className="clue-summary-value">{values.join(", ")}</span>
          </button>
          <button
            type="button"
            className="filter-summary-remove"
            aria-label={`Clear ${label}`}
            title={`Clear ${label}`}
            onClick={() => onFiltersChange(clearFacet(filters, key))}
          >
            <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </span>
      ))}
      <span className="filter-summary-count" role="status">
        {movieCount === 1 ? "1 movie" : `${movieCount} movies`}
      </span>
    </>
  );
}
