import type { BookmarkRecord } from "@/types/api";
import { displayTitle, filmLabel } from "./format";

/**
 * How the Saved board is ordered. "yours" is the board's own order and the
 * only one that is stored; the others are views over it and never move a
 * thing. Whatever the arrangement, the board stays one board: a view changes
 * the sequence of the scenes, never the shape of the page.
 */
export type Arrangement = "yours" | "newest" | "film";

export const ARRANGEMENTS: ReadonlyArray<{ value: Arrangement; label: string }> = [
  { value: "yours", label: "Your order" },
  { value: "newest", label: "Newest" },
  { value: "film", label: "By film" },
];

const savedAt = (bookmark: BookmarkRecord) => Date.parse(bookmark.created_at) || 0;
const momentOf = (bookmark: BookmarkRecord) => bookmark.scene?.t_start ?? bookmark.evidence_timestamp;
const filmTitleOf = (bookmark: BookmarkRecord) => displayTitle(bookmark.film_title || filmLabel(bookmark.film_id));

/** The board's scenes in the arrangement's sequence. The list itself is never changed. */
export function arrangeBoard(bookmarks: BookmarkRecord[], arrangement: Arrangement): BookmarkRecord[] {
  if (arrangement === "newest") {
    return [...bookmarks].sort(
      (a, b) => savedAt(b) - savedAt(a) || a.bookmark_id.localeCompare(b.bookmark_id),
    );
  }
  if (arrangement === "film") {
    // Films by name, each film's scenes together in story order.
    const titles = new Map(bookmarks.map((bookmark) => [bookmark.film_id, filmTitleOf(bookmark)]));
    const title = (bookmark: BookmarkRecord) => titles.get(bookmark.film_id) ?? "";
    return [...bookmarks].sort(
      (a, b) =>
        title(a).localeCompare(title(b)) ||
        a.film_id.localeCompare(b.film_id) ||
        momentOf(a) - momentOf(b) ||
        a.bookmark_id.localeCompare(b.bookmark_id),
    );
  }
  return bookmarks;
}

/** Whether a scene has been placed by hand yet. */
export const hasUserOrder = (bookmarks: BookmarkRecord[]) =>
  bookmarks.some((bookmark) => bookmark.position != null);

/**
 * `list` with the item at `from` moved so that it sits at insertion index
 * `to` (0 to length, as between the items of the list before the move).
 * A move that changes nothing returns the same list.
 */
export function moveItem<T>(list: T[], from: number, to: number): T[] {
  if (from < 0 || from >= list.length) return list;
  const target = Math.max(0, Math.min(list.length, to));
  if (target === from || target === from + 1) return list;
  const next = [...list];
  const [item] = next.splice(from, 1);
  next.splice(target > from ? target - 1 : target, 0, item);
  return next;
}
