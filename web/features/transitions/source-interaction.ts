import type { SourceFraming, TransitionSource } from "./transitions";

const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));

/** Keep the opposite trim edge fixed, including at the source and clip-length limits. */
export function trimLimits(source: TransitionSource, edge: "source_start" | "source_end", filmDuration = Infinity) {
  return edge === "source_start"
    ? { min: Math.max(0, source.source_end - 12), max: source.source_end - .2 }
    : { min: source.source_start + .2, max: Math.min(filmDuration, source.source_start + 12) };
}

export function trimAtPointer(source: TransitionSource, edge: "source_start" | "source_end", time: number, filmDuration = Infinity) {
  const { min, max } = trimLimits(source, edge, filmDuration);
  if (!Number.isFinite(time) || !Number.isFinite(min) || max < min) return null;
  // Preserve a legal bound even when a native source endpoint has sub-ms precision.
  return clamp(Math.round(clamp(time, min, max) * 1000) / 1000, min, max);
}

/** Match the contain/cover + anchored zoom used by the preview and render crop. */
export function framingDrag(framing: SourceFraming, picture: { width: number; height: number }, viewport: { width: number; height: number }, dx: number, dy: number): SourceFraming {
  if (![picture.width, picture.height, viewport.width, viewport.height].every((size) => Number.isFinite(size) && size > 0)) return framing;
  const scale = (framing.fit === "fill" ? Math.max : Math.min)(viewport.width / picture.width, viewport.height / picture.height);
  const availableX = viewport.width - picture.width * scale * framing.zoom;
  const availableY = viewport.height - picture.height * scale * framing.zoom;
  return { ...framing,
    anchor_x: Math.abs(availableX) < 1 ? framing.anchor_x : clamp(framing.anchor_x + dx / availableX, 0, 1),
    anchor_y: Math.abs(availableY) < 1 ? framing.anchor_y : clamp(framing.anchor_y + dy / availableY, 0, 1),
  };
}
