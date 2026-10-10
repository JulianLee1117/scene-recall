import type { BookmarkRecord } from "@/types/api";
import { displayTitle, filmLabel } from "./format";

/**
 * How the Saved board is laid out. "yours" is the board's own order and the
 * only one that is stored; the others are views over it and never move a
 * thing until one is kept as your order.
 */
export type Arrangement = "yours" | "newest" | "film";

export const ARRANGEMENTS: ReadonlyArray<{ value: Arrangement; label: string }> = [
  { value: "yours", label: "Your order" },
  { value: "newest", label: "Newest" },
  { value: "film", label: "By film" },
];

export interface BoardGroup {
  key: string;
  /** A heading when the arrangement groups; none for the plain board. */
  title: string | null;
  items: BookmarkRecord[];
}

const savedAt = (bookmark: BookmarkRecord) => Date.parse(bookmark.created_at) || 0;
const momentOf = (bookmark: BookmarkRecord) => bookmark.scene?.t_start ?? bookmark.evidence_timestamp;
const filmTitleOf = (bookmark: BookmarkRecord) => displayTitle(bookmark.film_title || filmLabel(bookmark.film_id));

/** The board as the arrangement shows it. The list itself is never changed. */
export function arrangeBoard(bookmarks: BookmarkRecord[], arrangement: Arrangement): BoardGroup[] {
  if (arrangement === "newest") {
    const items = [...bookmarks].sort(
      (a, b) => savedAt(b) - savedAt(a) || a.bookmark_id.localeCompare(b.bookmark_id),
    );
    return [{ key: "newest", title: null, items }];
  }
  if (arrangement === "film") {
    const groups = new Map<string, BoardGroup>();
    for (const bookmark of bookmarks) {
      const group = groups.get(bookmark.film_id) ?? { key: bookmark.film_id, title: filmTitleOf(bookmark), items: [] };
      group.items.push(bookmark);
      groups.set(bookmark.film_id, group);
    }
    // A film's scenes in story order; films by name.
    return [...groups.values()]
      .map((group) => ({ ...group, items: [...group.items].sort((a, b) => momentOf(a) - momentOf(b)) }))
      .sort((a, b) => (a.title ?? "").localeCompare(b.title ?? ""));
  }
  return [{ key: "board", title: null, items: bookmarks }];
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
