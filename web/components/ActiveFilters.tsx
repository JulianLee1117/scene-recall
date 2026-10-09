"use client";

import { activeFacets, clearFacet, describeFacet } from "@/lib/filmFilters";
import { activeShotFacets, clearShotFacet, type ShotFacet } from "@/lib/shotFilters";
import ActiveChip from "./ActiveChip";
import type { FilterView, SearchFilters } from "./SearchFilter";

/**
 * The active filters beside the result count, one chip per section
 * ("Era 1990s, 2000s", "Size Close"). A movie chip opens the Filter menu at
 * its section, a shot chip at the overview where shots toggle.
 */
export default function ActiveFilters({
  filters,
  shotFacets,
  onFiltersChange,
  onEdit,
}: {
  filters: SearchFilters;
  shotFacets: readonly ShotFacet[];
  onFiltersChange: (change: Partial<SearchFilters>) => void;
  onEdit: (view: FilterView) => void;
}) {
  return (
    <>
      {activeFacets(filters.film).map((facet) => (
        <ActiveChip
          key={facet.key}
          label={facet.label}
          value={describeFacet(facet)}
          onEdit={() => onEdit(facet.key)}
          onClear={() => onFiltersChange({ film: clearFacet(filters.film, facet.key) })}
        />
      ))}
      {activeShotFacets(filters.shot, shotFacets).map(({ key, label, values }) => (
        <ActiveChip
          key={`shot:${key}`}
          label={label}
          value={values.join(", ")}
          onEdit={() => onEdit("menu")}
          onClear={() => onFiltersChange({ shot: clearShotFacet(filters.shot, key) })}
        />
      ))}
    </>
  );
}
