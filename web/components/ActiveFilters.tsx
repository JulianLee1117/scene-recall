"use client";

import { activeFacets, clearFacet, type FilmFacetKey, type FilmFilters } from "@/lib/filmFilters";
import ActiveChip from "./ActiveChip";

/**
 * The active film filters, one chip per section ("Era 1990s, 2000s"), in the
 * row under the toolbar beside the active refinements, then how many movies
 * are left. A chip opens the Filter menu at its section.
 */
export default function ActiveFilters({
  filters,
  onFiltersChange,
  onEdit,
  movieCount,
}: {
  filters: FilmFilters;
  onFiltersChange: (filters: FilmFilters) => void;
  onEdit: (facet: FilmFacetKey) => void;
  /** Movies left to search, shown after the chips. */
  movieCount: number;
}) {
  const facets = activeFacets(filters);
  if (facets.length === 0) return null;
  return (
    <>
      {facets.map(({ key, label, values }) => (
        <ActiveChip
          key={key}
          label={label}
          value={values.join(", ")}
          onEdit={() => onEdit(key)}
          onClear={() => onFiltersChange(clearFacet(filters, key))}
        />
      ))}
      <span className="active-chip-count" role="status">
        {movieCount === 1 ? "1 movie" : `${movieCount} movies`}
      </span>
    </>
  );
}
