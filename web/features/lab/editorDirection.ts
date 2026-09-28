import type { EditorDirection, EditorDirectionRange, LabDocument } from "@/types/lab";

export const MAX_DIRECTION_LENGTH = 24_000;
export const MAX_DIRECTION_RANGES = 32;
export const MIN_DIRECTION_RANGE = .1;

/** An explicitly cleared direction stays clear; only absent legacy fields migrate. */
export function effectiveEditorDirection(document: LabDocument): EditorDirection {
  if (document.editor_direction != null) return document.editor_direction;
  const visual = document.visual_plan?.source === "user" ? document.visual_plan : null;
  return {
    instruction: [document.brief, visual?.arc, visual?.motifs].filter((value) => value?.trim()).join("\n\n"),
    ranges: [],
  };
}

/** Direction is input for future generation, never a mutation of placed shots. */
export function changeEditorDirection(document: LabDocument, patch: Partial<EditorDirection>): LabDocument {
  const current = effectiveEditorDirection(document);
  const next = { ...current, ...patch };
  if (document.editor_direction != null && JSON.stringify(next) === JSON.stringify(current)) return document;
  return { ...document, editor_direction: next };
}

export function appendDirectionExample(document: LabDocument, example: string): LabDocument {
  const instruction = effectiveEditorDirection(document).instruction;
  return changeEditorDirection(document, {
    instruction: `${instruction}${instruction.trim() ? "\n\n" : ""}${example}`.slice(0, MAX_DIRECTION_LENGTH),
  });
}

export function updateDirectionRange(document: LabDocument, id: string, patch: Partial<EditorDirectionRange>): LabDocument {
  const direction = effectiveEditorDirection(document);
  if (!direction.ranges.some((range) => range.id === id)) return document;
  return changeEditorDirection(document, { ranges: direction.ranges.map((range) => range.id === id ? { ...range, ...patch, id } : range) });
}

export function directionRangeBounds(ranges: EditorDirectionRange[], id: string, duration: number) {
  const sorted = [...ranges].sort((a, b) => a.start - b.start);
  const index = sorted.findIndex((range) => range.id === id);
  return { start: sorted[index - 1]?.end ?? 0, end: sorted[index + 1]?.start ?? duration };
}

export function fitDirectionRange(
  range: Pick<EditorDirectionRange, "start" | "end">,
  kind: "move" | "start" | "end",
  delta: number,
  bounds: { start: number; end: number },
): { start: number; end: number } {
  const clamp = (value: number, low: number, high: number) => Math.max(low, Math.min(high, value));
  const round = (value: number) => Math.round(value * 1000) / 1000;
  if (kind === "move") {
    const shift = clamp(delta, bounds.start - range.start, bounds.end - range.end);
    const duration = range.end - range.start;
    const start = clamp(round(range.start + shift), bounds.start, bounds.end - duration);
    return { start, end: Math.min(bounds.end, start + duration) };
  }
  return kind === "start"
    ? { start: clamp(round(range.start + delta), bounds.start, range.end - MIN_DIRECTION_RANGE), end: range.end }
    : { start: range.start, end: clamp(round(range.end + delta), range.start + MIN_DIRECTION_RANGE, bounds.end) };
}

/** Find free song time without displacing another instruction. */
export function availableDirectionRange(ranges: EditorDirectionRange[], passage: { start: number; end: number }, at: number) {
  let start = passage.start;
  const gaps: { start: number; end: number }[] = [];
  for (const range of [...ranges].sort((a, b) => a.start - b.start)) {
    if (range.end <= passage.start || range.start >= passage.end) continue;
    if (range.start - start >= MIN_DIRECTION_RANGE) gaps.push({ start, end: Math.min(range.start, passage.end) });
    start = Math.max(start, range.end);
  }
  if (passage.end - start >= MIN_DIRECTION_RANGE) gaps.push({ start, end: passage.end });
  const gap = gaps.find((item) => item.start <= at && item.end - MIN_DIRECTION_RANGE >= at)
    ?? gaps.find((item) => item.start >= at) ?? gaps[0];
  if (!gap) return null;
  const from = Math.max(gap.start, Math.min(at, gap.end - MIN_DIRECTION_RANGE));
  return { start: from, end: Math.min(gap.end, from + 4) };
}
