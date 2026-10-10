"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { useFrameAspects } from "@/hooks/useFrameAspects";
import { cssAspect, layoutRows, rowHeightFor, type RowLayout, type RowSize } from "@/lib/justifiedRows";
import type { SearchResult } from "@/types/api";

export const ROW_GAP = 2;
const VIEWPORT_BOTTOM_GUTTER = 24;

export interface GridFrame {
  width: number;
  rowHeight: number;
  /** Space below the grid's top edge in the viewport, for the first reveal. */
  room: number;
}

type Scene = Pick<SearchResult, "unit_id" | "film_id">;

/**
 * Measures a grid and lays its scenes out in even justified rows. Returns no
 * layout until the grid has a width (hidden grids keep their last layout).
 */
export function useJustifiedRows(scenes: Scene[], size: RowSize = "medium", scale = 1) {
  const ref = useRef<HTMLElement>(null);
  const { tileStyle, learnAspect, aspectOf } = useFrameAspects();
  const [frame, setFrame] = useState<GridFrame | null>(null);
  const hasScenes = scenes.length > 0;

  useLayoutEffect(() => {
    const grid = ref.current;
    if (!grid || !hasScenes) return;

    const measure = () => {
      const bounds = grid.getBoundingClientRect();
      if (bounds.width === 0) return;
      const style = window.getComputedStyle(grid);
      const next: GridFrame = {
        width: bounds.width - (Number.parseFloat(style.paddingLeft) || 0) - (Number.parseFloat(style.paddingRight) || 0),
        rowHeight: Math.round(rowHeightFor(window.innerWidth, size) * scale),
        room: window.innerHeight - bounds.top - VIEWPORT_BOTTOM_GUTTER,
      };
      setFrame((current) =>
        current && current.width === next.width && current.rowHeight === next.rowHeight ? current : next);
    };

    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(grid);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
    // Re-attach once rows replace the unmeasured list element.
  }, [hasScenes, size, scale, frame !== null]);

  const layout: RowLayout | null = frame
    ? layoutRows(scenes.map((scene) => cssAspect(aspectOf(scene))), frame.width, frame.rowHeight, ROW_GAP)
    : null;
  return { ref, frame, layout, tileStyle, learnAspect };
}

/** Index of each row's first tile. */
export function rowStarts(sizes: number[]): number[] {
  const starts: number[] = [];
  let total = 0;
  for (const size of sizes) {
    starts.push(total);
    total += size;
  }
  return starts;
}
