"use client";

import { useCallback, useState, type CSSProperties } from "react";

// Result tiles keep each film's own frame shape (1.33 to 2.39 and beyond), so
// a grid of scenes reads as one uncropped moodboard. Shapes are learned from
// loaded keyframes and remembered per film, across searches, so later tiles
// are sized correctly before their image arrives.
const DEFAULT_ASPECT = 16 / 9;
const MIN_ASPECT = 1;
const MAX_ASPECT = 2.8;
const filmAspects = new Map<string, number>();

function clampAspect(aspect: number): number {
  return Math.min(MAX_ASPECT, Math.max(MIN_ASPECT, aspect));
}

export function useFrameAspects() {
  const [, setVersion] = useState(0);

  const tileStyle = useCallback(
    (filmId: string) => ({ "--ar": (filmAspects.get(filmId) ?? DEFAULT_ASPECT).toFixed(4) }) as CSSProperties,
    [],
  );

  const learnAspect = useCallback((filmId: string, width: number, height: number) => {
    if (!(width > 0 && height > 0)) return;
    const aspect = clampAspect(width / height);
    const known = filmAspects.get(filmId);
    if (known !== undefined && Math.abs(known - aspect) < 0.01) return;
    filmAspects.set(filmId, aspect);
    setVersion((version) => version + 1);
  }, []);

  return { tileStyle, learnAspect };
}
