export function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function filmLabel(filmId: string): string {
  return filmId
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** "Taxi Driver (1976) [Remastered]" → "Taxi Driver (1976)": edition tags stay in the library, not on screen. */
export function displayTitle(title: string): string {
  return title.replace(/(\s*\[[^\]]*\])+\s*$/, "").trim() || title;
}
