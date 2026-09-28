export interface NativeDragPreviewOptions {
  eyebrow: string;
  title: string;
  detail?: string;
  imageUrl?: string;
}

/** Keep the cursor inside the picked-up thumbnail for either drag transport. */
export const SCENE_DRAG_HOTSPOT = { x: 28, y: 24 } as const;

/** The same recognizable scene card follows native and captured-pointer drags. */
export function createSceneDragPreview(
  { eyebrow, title, detail, imageUrl }: NativeDragPreviewOptions,
): HTMLDivElement {
  const preview = document.createElement("div");
  preview.className = `scene-drag-preview${imageUrl ? " has-image" : ""}`;
  preview.setAttribute("aria-hidden", "true");

  if (imageUrl) {
    const image = document.createElement("img");
    image.src = imageUrl;
    image.alt = "";
    image.draggable = false;
    preview.append(image);
  }

  const copy = document.createElement("span");
  copy.className = "native-drag-preview-copy";

  const eyebrowNode = document.createElement("small");
  eyebrowNode.textContent = eyebrow;
  copy.append(eyebrowNode);

  const titleNode = document.createElement("strong");
  titleNode.textContent = title;
  copy.append(titleNode);

  if (detail) {
    const detailNode = document.createElement("span");
    detailNode.textContent = detail;
    copy.append(detailNode);
  }

  preview.append(copy);
  return preview;
}

/** Render before setDragImage; the browser captures the card synchronously. */
export function setNativeDragPreview(
  transfer: DataTransfer,
  options: NativeDragPreviewOptions,
): void {
  const preview = createSceneDragPreview(options);
  preview.classList.add("native-drag-preview");
  document.body.append(preview);

  // Force style/layout before the browser snapshots the element.
  preview.getBoundingClientRect();
  transfer.setDragImage(preview, SCENE_DRAG_HOTSPOT.x, SCENE_DRAG_HOTSPOT.y);
  window.setTimeout(() => preview.remove(), 0);
}
