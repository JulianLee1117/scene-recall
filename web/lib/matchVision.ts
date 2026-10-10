/**
 * Vision: what the match index saw at one instant (GET /matching/moments/vision),
 * turned into shapes for an SVG overlay. Layers arrive described by kind, in
 * fractions of the content picture (letterbox removed), so any version of the
 * moments producer draws the same way; a new kind needs only a case here.
 * Shapes are laid out in the picture's own shape (aspect x 100 by 100), so
 * circles stay round and edge angles stay true.
 */

type Point = [number, number];

export type VisionLayer = { key: string; label: string } & (
  | { kind: "regions"; items: { label: string; score: number; box: [number, number, number, number]; mask: string[] }[] }
  | { kind: "skeletons"; joints: string[]; edges: [number, number][]; items: { points: [number, number, number][] }[] }
  | { kind: "points"; items: { label: string; at: Point }[] }
  | { kind: "field"; columns: number; rows: number; cells: [number, number, number, number][] }
  | { kind: "grid"; columns: number; rows: number; values?: number[]; colors?: string[] }
  | { kind: "vectors"; seconds: number; zoom: number | null; items: { label: string; from: Point; to: Point }[] }
);

export interface MomentVision {
  unit_id: string;
  film_id: string;
  time: number;
  usable: boolean;
  aspect: number;
  /** The index, the film's moments profile and, for the current producer, its models. */
  source: { index: string; profile: string | null; models: Record<string, string> | null };
  layers: VisionLayer[];
}

export type VisionShape =
  | { kind: "cell"; layer: string; x: number; y: number; w: number; h: number; fill: string; opacity: number; area?: boolean }
  | { kind: "box"; layer: string; x: number; y: number; w: number; h: number; tint: string }
  | { kind: "label"; layer: string; x: number; y: number; text: string; tint: string }
  | { kind: "line"; layer: string; x1: number; y1: number; x2: number; y2: number; arrow?: boolean; strength?: number }
  | { kind: "dot"; layer: string; x: number; y: number; r: number; ring?: boolean };

/** Layers shown until you choose: what a match leans on most. */
export const DEFAULT_VISION_LAYERS = ["objects", "pose", "eyes"];
/** Tints for successive objects, quiet enough to see the picture through. */
export const VISION_TINTS = ["#e8b060", "#78c4e6", "#96dc8c", "#e67896", "#be96f0", "#f0dc78"];
/** Keypoint confidence at which a body part counts as shown, as the scorer reads it. */
const SEEN = 0.35;
/** A finer grid is drawn in blocks of cells, so each block stays big enough to read. */
const GRID_COLUMNS = 16;

const gray = (value: number) => {
  const level = Math.round(Math.max(0, Math.min(1, value)) * 255);
  return `rgb(${level},${level},${level})`;
};

/** One fill for a block of grid cells: their mean grey, or mean colour. */
function meanFill(block: readonly (number | string)[]): string {
  if (typeof block[0] === "number") return gray(block.reduce<number>((sum, value) => sum + Number(value), 0) / block.length);
  const channel = (at: number) => Math.round(block.reduce<number>((sum, value) => sum + parseInt(String(value).slice(at, at + 2), 16), 0) / block.length);
  return `rgb(${channel(1)},${channel(3)},${channel(5)})`;
}

export function visionShapes(vision: MomentVision, visible: ReadonlySet<string>): { width: number; height: number; shapes: VisionShape[] } {
  const width = vision.aspect * 100, height = 100;
  const shapes: VisionShape[] = [];
  for (const layer of vision.layers) {
    if (!visible.has(layer.key)) continue;
    const key = layer.key;
    switch (layer.kind) {
      case "grid": {
        const values: readonly (number | string)[] = layer.colors ?? layer.values ?? [];
        const step = Math.max(1, Math.ceil(layer.columns / GRID_COLUMNS));
        const columns = Math.ceil(layer.columns / step), rows = Math.ceil(layer.rows / step);
        const cw = width / columns, ch = height / rows;
        for (let row = 0; row < rows; row += 1) {
          for (let column = 0; column < columns; column += 1) {
            const block: (number | string)[] = [];
            for (let y = row * step; y < Math.min(layer.rows, (row + 1) * step); y += 1) {
              for (let x = column * step; x < Math.min(layer.columns, (column + 1) * step); x += 1) block.push(values[y * layer.columns + x]);
            }
            shapes.push({ kind: "cell", layer: key, x: column * cw, y: row * ch, w: cw, h: ch, fill: meanFill(block), opacity: 0.55, area: true });
          }
        }
        break;
      }
      case "regions":
        layer.items.forEach((item, index) => {
          const tint = VISION_TINTS[index % VISION_TINTS.length];
          const [x0, y0, x1, y1] = item.box;
          const columns = item.mask[0]?.length ?? 0;
          const cw = (x1 - x0) * width / Math.max(columns, 1), ch = (y1 - y0) * height / Math.max(item.mask.length, 1);
          // One cell per run of filled squares in a row, so a silhouette stays a few shapes.
          item.mask.forEach((row, r) => {
            for (let c = 0; c < columns; c += 1) {
              if (row[c] !== "1") continue;
              let end = c;
              while (row[end + 1] === "1") end += 1;
              shapes.push({ kind: "cell", layer: key, x: x0 * width + c * cw, y: y0 * height + r * ch, w: (end - c + 1) * cw, h: ch, fill: tint, opacity: 0.38 });
              c = end;
            }
          });
          shapes.push({ kind: "box", layer: key, x: x0 * width, y: y0 * height, w: (x1 - x0) * width, h: (y1 - y0) * height, tint });
          shapes.push({ kind: "label", layer: key, x: x0 * width, y: y0 * height, text: `${item.label} ${item.score.toFixed(2)}`, tint });
        });
        break;
      case "skeletons":
        for (const person of layer.items) {
          for (const [a, b] of layer.edges) {
            const from = person.points[a], to = person.points[b];
            if (from && to && from[2] >= SEEN && to[2] >= SEEN) {
              shapes.push({ kind: "line", layer: key, x1: from[0] * width, y1: from[1] * height, x2: to[0] * width, y2: to[1] * height });
            }
          }
          for (const [x, y, confidence] of person.points) {
            if (confidence >= SEEN) shapes.push({ kind: "dot", layer: key, x: x * width, y: y * height, r: 0.7 });
          }
        }
        break;
      case "points":
        for (const item of layer.items) shapes.push({ kind: "dot", layer: key, x: item.at[0] * width, y: item.at[1] * height, r: 2.6, ring: true });
        break;
      case "field": {
        const cw = width / layer.columns, ch = height / layer.rows;
        for (const [column, row, angle, strength] of layer.cells) {
          const half = 0.42 * Math.min(cw, ch) * Math.min(1, strength * 1.6);
          const cx = (column + 0.5) * cw, cy = (row + 0.5) * ch;
          const dx = half * Math.cos(angle), dy = half * Math.sin(angle);
          shapes.push({ kind: "line", layer: key, x1: cx - dx, y1: cy - dy, x2: cx + dx, y2: cy + dy, strength });
        }
        break;
      }
      case "vectors":
        for (const item of layer.items) {
          shapes.push({ kind: "line", layer: key, x1: item.from[0] * width, y1: item.from[1] * height,
            x2: item.to[0] * width, y2: item.to[1] * height, arrow: true });
        }
        break;
    }
  }
  return { width, height, shapes };
}

/** The reason Vision draws for a cut: the one pointed at, else the strongest it can draw. */
export function shownReason<Reason extends { code: string; layers?: string[] }>(
  reasons: readonly Reason[], pointed: string | null,
): Reason | null {
  const drawable = reasons.filter((reason) => reason.layers?.length);
  return drawable.find((reason) => reason.code === pointed) ?? drawable[0] ?? null;
}

/** Where the layers came from, in a few words ("rf-detr-seg-small-1.11-fp16 · match-v1-550fb45554"). */
export function visionSource(vision: MomentVision): string {
  const models = vision.source.models ? Object.values(vision.source.models) : [];
  return [...models, vision.source.profile ?? "unknown profile"].join(" · ");
}
