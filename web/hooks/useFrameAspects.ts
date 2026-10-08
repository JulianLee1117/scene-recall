"use client";

import { useCallback, useState, type CSSProperties } from "react";
import type { SearchResult } from "@/types/api";

// Result tiles keep their frame's own shape (1.33 to 2.39 and beyond), so a
// grid of scenes reads as one uncropped moodboard. Each scene's shape is
// learned from its loaded picture; until then the film's last seen shape
// stands in (films can change ratio between scenes), remembered across
// searches so tiles rarely resize once their image arrives.
const DEFAULT_ASPECT = 16 / 9;
const MIN_ASPECT = 1;
const MAX_ASPECT = 2.8;
const filmAspects = new Map<string, number>();
const sceneAspects = new Map<string, number>();

function clampAspect(aspect: number): number {
  return Math.min(MAX_ASPECT, Math.max(MIN_ASPECT, aspect));
}

type Scene = Pick<SearchResult, "unit_id" | "film_id">;

export function useFrameAspects() {
  // Bumps whenever a shape is learned, so layouts can recompute.
  const [version, setVersion] = useState(0);

  const aspectOf = useCallback(
    (scene: Scene) => sceneAspects.get(scene.unit_id) ?? filmAspects.get(scene.film_id) ?? DEFAULT_ASPECT,
    [],
  );
  const tileStyle = useCallback(
    (scene: Scene) => ({ "--ar": aspectOf(scene).toFixed(4) }) as CSSProperties,
    [aspectOf],
  );

  const learnAspect = useCallback((scene: Scene, width: number, height: number) => {
    if (!(width > 0 && height > 0)) return;
    const aspect = clampAspect(width / height);
    filmAspects.set(scene.film_id, aspect);
    const known = sceneAspects.get(scene.unit_id);
    if (known !== undefined && Math.abs(known - aspect) < 0.01) return;
    sceneAspects.set(scene.unit_id, aspect);
    setVersion((current) => current + 1);
  }, []);

  return { tileStyle, learnAspect, aspectOf, version };
}
