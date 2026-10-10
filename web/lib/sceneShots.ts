import type { SearchResult } from "@/types/api";
import { displayMoment } from "@/lib/resultMoment";

/** The chosen matches, arranged for browsing the film without changing its best result. */
export function matchingShots(shot: SearchResult): SearchResult[] {
  const alternatives: SearchResult[] = (shot.scene_alternatives ?? []).map((alternative) => ({
    ...alternative,
    film_id: shot.film_id,
    film_title: shot.film_title,
    caption: alternative.caption ?? "",
    preview_url: alternative.preview_url ?? "",
    // Older responses sent only the displayed hero URL and time for alternatives.
    hero_url: alternative.hero_url ?? (alternative.hero_time != null
      && alternative.thumbnail_url?.startsWith("/media/hero/") ? alternative.thumbnail_url : undefined),
  }));
  // Keep the original object: opening on the best match and the caller's source
  // identity remain unchanged even when an earlier matching shot sorts first.
  return [shot, ...alternatives].sort((a, b) => displayMoment(a) - displayMoment(b)
    || a.t_start - b.t_start || a.unit_id.localeCompare(b.unit_id));
}
