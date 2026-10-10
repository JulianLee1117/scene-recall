import { layoutRows } from "./justifiedRows";

/**
 * The Saved board laid out in explicit positions: justified rows of tiles.
 * Every tile is placed by its id, so a tile keeps its element and glides
 * when the order changes.
 */
export interface BoardTile {
  id: string;
  aspect: number;
}

export interface TilePlace {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface BoardLayout {
  height: number;
  tiles: Map<string, TilePlace>;
}

export const BOARD_GAP = 2;

const round = (value: number) => Math.round(value * 100) / 100;

export function layoutBoard(items: BoardTile[], width: number, rowHeight: number, gap = BOARD_GAP): BoardLayout {
  const tiles = new Map<string, TilePlace>();
  if (!items.length) return { height: 0, tiles };
  const rows = layoutRows(items.map((tile) => tile.aspect), width, rowHeight, gap);
  let y = 0;
  let start = 0;
  rows.sizes.forEach((size, row) => {
    const height = rows.heights[row];
    let x = 0;
    for (const tile of items.slice(start, start + size)) {
      const tileWidth = tile.aspect * height;
      tiles.set(tile.id, { x: round(x), y: round(y), width: round(tileWidth), height: round(height) });
      x += tileWidth + gap;
    }
    start += size;
    y += height + gap;
  });
  return { height: round(Math.max(0, y - gap)), tiles };
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
