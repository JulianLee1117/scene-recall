/** Space belongs to transport only when a control is not already using it. */
export function isPlaybackSpace(event: KeyboardEvent): boolean {
  if (
    (event.code !== "Space" && event.key !== " ") ||
    event.defaultPrevented ||
    event.isComposing ||
    event.altKey ||
    event.ctrlKey ||
    event.metaKey ||
    event.shiftKey
  )
    return false;

  const target = event.target as Element | null;
  if (
    target?.closest?.(
      "input, textarea, select, [contenteditable]:not([contenteditable='false']), " +
        "[role='textbox'], [role='combobox']",
    )
  )
    return false;
  // Timeline controls opt into transport Space instead of native activation.
  // Typing always wins, including editors nested inside an opted-in region.
  if (target?.closest?.("[data-playback-space]")) return true;
  return !target?.closest?.(
    "button, summary, a[href], [role='button'], " +
      "[role='checkbox'], [role='radio'], [role='switch'], [role='menuitem']",
  );
}
