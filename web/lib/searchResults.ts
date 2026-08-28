import type { SearchResult } from "@/types/api";

/** Preserve ranking while keeping only the first result from each film. */
export function bestResultPerFilm(
  results: readonly SearchResult[],
): SearchResult[] {
  const seenFilmIds = new Set<string>();

  return results.filter((result) => {
    if (seenFilmIds.has(result.film_id)) return false;
    seenFilmIds.add(result.film_id);
    return true;
  });
}
