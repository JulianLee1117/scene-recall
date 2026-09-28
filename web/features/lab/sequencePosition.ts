/** HTML media clocks can round a requested cut to microsecond precision. */
const MEDIA_TIME_TOLERANCE = 0.00001;

/** Use the same boundary rule for the video deck, caption and manual selection. */
export function slotAt<T extends { start: number; end: number }>(time: number, slots: readonly T[], passageEnd: number): T | undefined {
  if (!Number.isFinite(time) || !Number.isFinite(passageEnd)) return undefined;
  // The end transport control displays the final frame, rather than an empty slot.
  const displayTime = Math.min(time, passageEnd - 0.001);
  return slots.find((slot) => displayTime >= slot.start - MEDIA_TIME_TOLERANCE && displayTime < slot.end - MEDIA_TIME_TOLERANCE);
}
