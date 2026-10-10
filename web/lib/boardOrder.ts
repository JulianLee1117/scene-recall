import type { BookmarkRecord } from "@/types/api";
import { displayTitle, filmLabel } from "./format";
import { GREY_CHROMA, type SceneLook } from "./sceneLook";

/**
 * How the Saved board is ordered. "yours" is the board's own order and the
 * only one that is stored; the others are views over it and never move a
 * thing. Whatever the arrangement, the board stays one board: a view changes
 * the sequence of the scenes, never the shape of the page.
 */
export type Arrangement = "yours" | "newest" | "colour" | "era" | "shuffle";

export const ARRANGEMENTS: ReadonlyArray<{ value: Arrangement; label: string }> = [
  { value: "yours", label: "Your order" },
  { value: "newest", label: "Newest" },
  { value: "colour", label: "By colour" },
  { value: "era", label: "By era" },
  { value: "shuffle", label: "Shuffle" },
];

export interface ArrangeContext {
  /** A scene's measured look, by unit id, when known. */
  lookOf?: (unitId: string) => SceneLook | undefined;
  /** The deal for Shuffle: the same seed gives the same board. */
  seed?: number;
}

const savedAt = (bookmark: BookmarkRecord) => Date.parse(bookmark.created_at) || 0;
const momentOf = (bookmark: BookmarkRecord) => bookmark.scene?.t_start ?? bookmark.evidence_timestamp;
const filmTitleOf = (bookmark: BookmarkRecord) => displayTitle(bookmark.film_title || filmLabel(bookmark.film_id));
const unitOf = (bookmark: BookmarkRecord) => bookmark.scene?.unit_id ?? bookmark.source_unit_id;
const byId = (a: BookmarkRecord, b: BookmarkRecord) => a.bookmark_id.localeCompare(b.bookmark_id);

/** The film's year, from its title ("Heat (1995)") or its id ("heat-1995"); null when neither says. */
export function filmYearOf(bookmark: BookmarkRecord): number | null {
  const match = /\((\d{4})\)/.exec(bookmark.film_title || "") ?? /(?:^|-)(\d{4})$/.exec(bookmark.film_id);
  return match ? Number(match[1]) : null;
}

/**
 * Where a look sits in the gradient: colours around the wheel in bands of
 * fifteen degrees, light to dark within a band, then the greys from light to
 * dark.
 */
const lookRank = (look: SceneLook): number[] =>
  look.chroma < GREY_CHROMA ? [1, 0, -look.lightness] : [0, Math.floor(look.hue / 15), -look.lightness];

const compareRanks = (a: number[], b: number[]) => {
  for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) return a[i] - b[i];
  return 0;
};

/** One deal's key for a scene: a hash of the seed and the id, stable for the deal. */
export function dealKey(seed: number, id: string): number {
  let hash = (seed ^ 0x9e3779b9) >>> 0;
  for (let i = 0; i < id.length; i += 1) hash = Math.imul(hash ^ id.charCodeAt(i), 0x01000193) >>> 0;
  hash ^= hash >>> 16;
  hash = Math.imul(hash, 0x85ebca6b) >>> 0;
  hash ^= hash >>> 13;
  hash = Math.imul(hash, 0xc2b2ae35) >>> 0;
  return (hash ^ (hash >>> 16)) >>> 0;
}

/** The board's scenes in the arrangement's sequence. The list itself is never changed. */
export function arrangeBoard(
  bookmarks: BookmarkRecord[],
  arrangement: Arrangement,
  context: ArrangeContext = {},
): BookmarkRecord[] {
  switch (arrangement) {
    case "newest":
      return [...bookmarks].sort((a, b) => savedAt(b) - savedAt(a) || byId(a, b));
    case "colour": {
      // Measured scenes make the gradient; the rest follow it in the board's own order.
      const lookOf = (bookmark: BookmarkRecord) => context.lookOf?.(unitOf(bookmark));
      return [...bookmarks].sort((a, b) => {
        const la = lookOf(a);
        const lb = lookOf(b);
        if (la && lb) return compareRanks(lookRank(la), lookRank(lb)) || byId(a, b);
        return la ? -1 : lb ? 1 : 0;
      });
    }
    case "era": {
      // Films by year, then by name; each film's scenes together in story order. No year: last.
      const year = (bookmark: BookmarkRecord) => filmYearOf(bookmark) ?? Number.POSITIVE_INFINITY;
      return [...bookmarks].sort(
        (a, b) =>
          year(a) - year(b) ||
          filmTitleOf(a).localeCompare(filmTitleOf(b)) ||
          a.film_id.localeCompare(b.film_id) ||
          momentOf(a) - momentOf(b) ||
          byId(a, b),
      );
    }
    case "shuffle": {
      const seed = context.seed ?? 0;
      return [...bookmarks].sort((a, b) => dealKey(seed, a.bookmark_id) - dealKey(seed, b.bookmark_id) || byId(a, b));
    }
    default:
      return bookmarks;
  }
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
