/**
 * Justified rows for the result moodboard. Each row holds the run of tiles
 * whose full-width height lands closest to the target row height, so rows
 * stay even. Tiles keep their film's frame shape: a tile's width is its
 * aspect times the row height. Only a final row that ran out of tiles is
 * left short.
 */

export type RowSize = "large" | "small";

/** Target row height in px. */
export function rowHeightFor(viewportWidth: number, size: RowSize = "large"): number {
  if (size === "small") return viewportWidth <= 600 ? 76 : 96;
  if (viewportWidth <= 600) return 92;
  return Math.round(Math.min(240, Math.max(140, viewportWidth * 0.14)));
}

/** The aspect exactly as CSS receives it (four decimals). */
export function cssAspect(aspect: number): number {
  return Number(aspect.toFixed(4));
}

export interface RowLayout {
  /** Tiles per row, in order. */
  sizes: number[];
  /** Each row's height at full width (the short last row keeps the target). */
  heights: number[];
  /** The last row ran out of tiles before reaching the width. */
  lastIsShort: boolean;
}

export function layoutRows(aspects: number[], width: number, rowHeight: number, gap: number): RowLayout {
  const sizes: number[] = [];
  const heights: number[] = [];
  let lastIsShort = false;
  let start = 0;
  while (start < aspects.length) {
    let best = 1;
    let bestHeight = Infinity;
    let bestScore = Infinity;
    let sum = 0;
    let crossed = false;
    for (let count = 1; start + count <= aspects.length; count += 1) {
      sum += aspects[start + count - 1];
      const height = (width - gap * (count - 1)) / sum;
      const score = Math.abs(Math.log(height / rowHeight));
      if (score < bestScore) {
        best = count;
        bestHeight = height;
        bestScore = score;
      }
      if (height <= rowHeight) {
        crossed = true;
        break; // adding more tiles only makes the row shorter
      }
    }
    start += best;
    sizes.push(best);
    lastIsShort = !crossed && start === aspects.length && bestHeight > rowHeight;
    heights.push(lastIsShort ? rowHeight : bestHeight);
  }
  return { sizes, heights, lastIsShort };
}

/**
 * How many tiles to show: whole rows only, at least `minRows` and at least
 * `floor` tiles. While more results can arrive, the last row is held back
 * (it may still change); a single row always shows.
 */
export function visibleTileCount(
  layout: RowLayout,
  { floor, minRows, exhausted }: { floor: number; minRows: number; exhausted: boolean },
): { count: number; rows: number; atEnd: boolean } {
  const { sizes } = layout;
  const available = exhausted || sizes.length <= 1 ? sizes.length : sizes.length - 1;
  let count = 0;
  let rows = 0;
  while (rows < available && (rows < minRows || count < floor)) {
    count += sizes[rows];
    rows += 1;
  }
  return { count, rows, atEnd: exhausted && rows === sizes.length };
}
