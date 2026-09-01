interface NativeDragPreviewOptions {
  eyebrow: string;
  title: string;
  detail?: string;
  imageUrl?: string;
}

/**
 * Replace the browser's full-element drag ghost with a compact scene token.
 * The node must be rendered when setDragImage runs, but can be removed before
 * the next paint because the browser captures it synchronously.
 */
export function setNativeDragPreview(
  transfer: DataTransfer,
  { eyebrow, title, detail, imageUrl }: NativeDragPreviewOptions,
): void {
  const preview = document.createElement("div");
  preview.className = `native-drag-preview${imageUrl ? " has-image" : ""}`;
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
  document.body.append(preview);

  // Force style/layout before the browser snapshots the element.
  preview.getBoundingClientRect();
  transfer.setDragImage(preview, imageUrl ? 28 : 18, 22);
  window.setTimeout(() => preview.remove(), 0);
}
