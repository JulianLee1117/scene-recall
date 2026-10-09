"use client";

import { activeFacets, clearFacet, type FilmFacetKey, type FilmFilters } from "@/lib/filmFilters";
import ActiveChip from "./ActiveChip";

/**
 * The active film filters, one chip per section ("Era 1990s, 2000s"), beside
 * the result count. A chip opens the Filter menu at its section.
 */
export default function ActiveFilters({
  filters,
  onFiltersChange,
  onEdit,
}: {
  filters: FilmFilters;
  onFiltersChange: (filters: FilmFilters) => void;
  onEdit: (facet: FilmFacetKey) => void;
}) {
  return activeFacets(filters).map(({ key, label, values }) => (
    <ActiveChip
      key={key}
      label={label}
      value={values.join(", ")}
      onEdit={() => onEdit(key)}
      onClear={() => onFiltersChange(clearFacet(filters, key))}
    />
  ));
}
