/**
 * Shot filters narrow a search to shots by what is in them: dialogue, size,
 * people, camera, color, time and place (ADR-0114). The server defines the
 * facets (GET /search/shot-facets) and applies them inside retrieval; the
 * page only keeps the choices and sends them with each search. The menu picks
 * one value per facet (the API also takes several, as alternatives); facets
 * combine, with each other and with film filters.
 */
export type ShotFilters = Readonly<Record<string, readonly string[]>>;

export interface ShotFacet {
  key: string;
  label: string;
  values: ReadonlyArray<{ value: string; label: string; count: number }>;
}

/** With *key* narrowed to *value*, or without *key* when *value* is "" (Any). */
export function chooseShotFilter(filters: ShotFilters, key: string, value: string): ShotFilters {
  const rest: Record<string, readonly string[]> = { ...filters };
  delete rest[key];
  return value ? { ...rest, [key]: [value] } : rest;
}

export function clearShotFacet(filters: ShotFilters, key: string): ShotFilters {
  return chooseShotFilter(filters, key, "");
}

export function hasShotFilters(filters: ShotFilters): boolean {
  return Object.values(filters).some((values) => values.length > 0);
}

export function sameShotFilters(a: ShotFilters, b: ShotFilters): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  return [...keys].every((key) => {
    const left = a[key] ?? [], right = b[key] ?? [];
    return left.length === right.length && left.every((value) => right.includes(value));
  });
}

/** Active shot filters in menu order with their labels, for the chips beside the result count. */
export function activeShotFacets(filters: ShotFilters, facets: readonly ShotFacet[]) {
  return facets.flatMap((facet) => {
    const chosen = filters[facet.key] ?? [];
    const labels = facet.values.filter((option) => chosen.includes(option.value)).map((option) => option.label);
    return labels.length ? [{ key: facet.key, label: facet.label, values: labels }] : [];
  });
}

/** The request's `shot_filters`, or nothing when none are chosen. */
export function shotFiltersPayload(filters: ShotFilters): Record<string, string[]> | undefined {
  const chosen = Object.entries(filters).filter(([, values]) => values.length > 0);
  return chosen.length ? Object.fromEntries(chosen.map(([key, values]) => [key, [...values]])) : undefined;
}
