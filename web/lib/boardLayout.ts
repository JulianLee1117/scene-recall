import { layoutRows } from "./justifiedRows";

/**
 * The Saved board laid out in explicit positions: justified rows of tiles,
 * with a heading before each film group. Every tile is placed by its id, so
 * a tile keeps its element and glides when the order or the arrangement
 * changes.
 */
export type BoardBlock =
  | { kind: "tile"; id: string; aspect: number }
  | { kind: "heading"; key: string };

export interface TilePlace {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface BoardLayout {
  height: number;
  tiles: Map<string, TilePlace>;
  /** Each heading's top, by group key. */
  headings: Map<string, number>;
}

export const BOARD_GAP = 2;
/** A heading's line plus the room below it. */
export const HEADING_HEIGHT = 30;
/** The room above a heading that follows other rows. */
export const GROUP_GAP = 26;

const round = (value: number) => Math.round(value * 100) / 100;

export function layoutBoard(blocks: BoardBlock[], width: number, rowHeight: number, gap = BOARD_GAP): BoardLayout {
  const tiles = new Map<string, TilePlace>();
  const headings = new Map<string, number>();
  let y = 0;
  let index = 0;
  while (index < blocks.length) {
    const block = blocks[index];
    if (block.kind === "heading") {
      if (y > 0) y += GROUP_GAP;
      headings.set(block.key, y);
      y += HEADING_HEIGHT;
      index += 1;
      continue;
    }
    const run: Array<{ id: string; aspect: number }> = [];
    while (index < blocks.length && blocks[index].kind === "tile") {
      const tile = blocks[index] as { id: string; aspect: number };
      run.push(tile);
      index += 1;
    }
    const rows = layoutRows(run.map((tile) => tile.aspect), width, rowHeight, gap);
    let start = 0;
    rows.sizes.forEach((size, row) => {
      const height = rows.heights[row];
      let x = 0;
      for (const tile of run.slice(start, start + size)) {
        const tileWidth = tile.aspect * height;
        tiles.set(tile.id, { x: round(x), y: round(y), width: round(tileWidth), height: round(height) });
        x += tileWidth + gap;
      }
      start += size;
      y += height + gap;
    });
    y -= gap;
  }
  return { height: round(Math.max(0, y)), tiles, headings };
}

/**
 * Where a lifted tile would go, from the pointer over the board as it is
 * laid out now, with the lifted tile at its tentative place. Returns the
 * insertion index into `order` (0 to length), or null when nothing should
 * change: over the lifted tile's own place, or between rows.
 */
export function placeAt(layout: BoardLayout, order: string[], liftedId: string, x: number, y: number): number | null {
  const own = layout.tiles.get(liftedId);
  if (own && inside(own, x, y)) return null;
  const others = order
    .map((id, index) => ({ id, index, place: layout.tiles.get(id) }))
    .filter((entry): entry is { id: string; index: number; place: TilePlace } => entry.id !== liftedId && entry.place !== undefined);
  if (!others.length) return null;
  const hit = others.find(({ place }) => inside(place, x, y));
  if (hit) return x < hit.place.x + hit.place.width / 2 ? hit.index : hit.index + 1;
  // Above the board or below it: the ends.
  if (y < Math.min(...others.map(({ place }) => place.y))) return 0;
  if (y > Math.max(...others.map(({ place }) => place.y + place.height))) return order.length;
  // In a row but off its tiles: the nearest tile of that row, by side.
  const row = others.filter(({ place }) => y >= place.y && y <= place.y + place.height);
  if (!row.length) return null;
  const nearest = row.reduce((best, entry) => (distance(entry.place, x) < distance(best.place, x) ? entry : best));
  return x < nearest.place.x + nearest.place.width / 2 ? nearest.index : nearest.index + 1;
}

const inside = (place: TilePlace, x: number, y: number) =>
  x >= place.x && x <= place.x + place.width && y >= place.y && y <= place.y + place.height;
const distance = (place: TilePlace, x: number) =>
  x < place.x ? place.x - x : x > place.x + place.width ? x - place.x - place.width : 0;
