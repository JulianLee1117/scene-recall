"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { loadLooks, measureLook, saveLooks, type SceneLook } from "@/lib/sceneLook";
import type { BookmarkRecord } from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
const AT_ONCE = 6;

/**
 * The looks of the board's scenes. Remembered looks arrive on mount; the
 * missing ones are measured from the scenes' pictures while a view wants
 * them, in a few at a time, and remembered for next time. `lookOf` changes
 * identity whenever looks arrive, so a layout keyed on it recomputes.
 */
export function useSceneLooks(bookmarks: BookmarkRecord[], wanted: boolean) {
  const [looks, setLooks] = useState<ReadonlyMap<string, SceneLook>>(() => new Map());
  const underway = useRef(new Set<string>());
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    setLooks(loadLooks());
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!wanted) return;
    const queue = bookmarks.filter(
      (bookmark) => bookmark.scene && !looks.has(bookmark.scene.unit_id) && !underway.current.has(bookmark.scene.unit_id),
    );
    if (!queue.length) return;
    const learned = new Map<string, SceneLook>();
    const measure = async () => {
      for (let bookmark = queue.shift(); bookmark; bookmark = queue.shift()) {
        const scene = bookmark.scene!;
        underway.current.add(scene.unit_id);
        try {
          learned.set(scene.unit_id, await measureLook(`${API_URL}${scene.thumbnail_url ?? scene.keyframe_url}`));
        } catch {
          // An unreadable picture stays unmeasured and keeps its place after the gradient.
        }
        underway.current.delete(scene.unit_id);
      }
    };
    void Promise.all(Array.from({ length: AT_ONCE }, measure)).then(() => {
      if (!learned.size) return;
      saveLooks(learned);
      if (mounted.current) setLooks((current) => new Map([...current, ...learned]));
    });
  }, [bookmarks, wanted, looks]);

  const lookOf = useCallback((unitId: string) => looks.get(unitId), [looks]);
  return { lookOf };
}
