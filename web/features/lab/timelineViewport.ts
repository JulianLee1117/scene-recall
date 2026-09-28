/** Page only when playback leaves the viewport; keep most of the next phrase ahead. */
export function playbackScrollLeft(playheadX: number, scrollLeft: number, width: number, contentWidth: number): number {
  if (width <= 0 || contentWidth <= width || !Number.isFinite(playheadX)) return scrollLeft;
  if (playheadX >= scrollLeft && playheadX < scrollLeft + width) return scrollLeft;
  const inset = playheadX < scrollLeft ? .85 : .15;
  return Math.max(0, Math.min(contentWidth - width, playheadX - width * inset));
}
