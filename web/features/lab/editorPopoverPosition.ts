type Rect = { left: number; right: number; top: number; bottom: number };

/** Fit an anchored editor panel without changing document layout. */
export function editorPopoverPosition(anchor: Rect, contentHeight: number, viewport: { width: number; height: number }, requestedWidth: number, align: "start" | "end") {
  const margin = 12, gap = 7;
  const width = Math.max(1, Math.min(requestedWidth, viewport.width - margin * 2));
  const below = Math.max(0, viewport.height - margin - anchor.bottom - gap);
  const above = Math.max(0, anchor.top - gap - margin);
  const useBelow = below >= Math.min(contentHeight, 280) || below >= above;
  const maxHeight = Math.max(1, Math.min(viewport.height - margin * 2, useBelow ? below : above));
  const height = Math.min(contentHeight, maxHeight);
  const left = Math.max(margin, Math.min(viewport.width - margin - width, align === "end" ? anchor.right - width : anchor.left));
  const top = Math.max(margin, Math.min(viewport.height - margin - height, useBelow ? anchor.bottom + gap : anchor.top - gap - height));
  return { left, top, width, maxHeight };
}
