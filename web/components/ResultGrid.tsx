"use client";

import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type RefObject } from "react";
import ShotCard from "./ShotCard";
import { useFrameAspects } from "@/hooks/useFrameAspects";
import { cssAspect, layoutRows, rowHeightFor, visibleTileCount, type RowSize } from "@/lib/justifiedRows";
import type { RecipeMatchFacet, SearchResult } from "@/types/api";

const MIN_VISIBLE_ROWS = 3;
const ROWS_PER_REVEAL = 2;
const ROW_GAP = 2;
const VIEWPORT_BOTTOM_GUTTER = 24;
const UNMEASURED_COUNT = 12;
const EMPTY_UNIT_IDS: ReadonlySet<string> = new Set();

interface ResultGridProps {
  results: SearchResult[];
  order?: "ranked" | "chronological";
  streamKey: number | string;
  revealDisabled?: boolean;
  hasMore?: boolean;
  onRequestMore?: () => void;
  onShotClick: (shot: SearchResult) => void;
  onUseInSearch?: (shot: SearchResult, facet: RecipeMatchFacet) => void;
  disabledUseFacets?: ReadonlySet<RecipeMatchFacet>;
  sourceReferenceFacet?: RecipeMatchFacet;
  onToggleBookmark?: (shot: SearchResult) => void;
  bookmarkedUnitIds?: ReadonlySet<string>;
  pendingBookmarkUnitIds?: ReadonlySet<string>;
  bookmarkDisabled?: boolean;
  showDetails: boolean;
  /** "small" for the compact reference lookup. */
  size?: RowSize;
}

interface GridFrame {
  width: number;
  rowHeight: number;
  /** Space below the grid's top edge in the viewport, for the first reveal. */
  room: number;
}

export default function ResultGrid({
  results,
  order = "ranked",
  streamKey,
  revealDisabled = false,
  hasMore = false,
  onRequestMore,
  onShotClick,
  onUseInSearch,
  disabledUseFacets,
  sourceReferenceFacet,
  onToggleBookmark,
  bookmarkedUnitIds = EMPTY_UNIT_IDS,
  pendingBookmarkUnitIds = EMPTY_UNIT_IDS,
  bookmarkDisabled = false,
  showDetails,
  size = "large",
}: ResultGridProps) {
  const gridRef = useRef<HTMLElement>(null);
  const { tileStyle, learnAspect, aspectOf } = useFrameAspects();
  const [frame, setFrame] = useState<GridFrame | null>(null);
  // How many scenes the user has seen; a resize never hides them again.
  const [visibleItemFloor, setVisibleItemFloor] = useState(0);
  const hasResults = results.length > 0;

  useLayoutEffect(() => {
    setVisibleItemFloor(0);
  }, [streamKey]);

  useLayoutEffect(() => {
    const grid = gridRef.current;
    if (!grid || !hasResults) return;

    const measure = () => {
      const bounds = grid.getBoundingClientRect();
      // A temporarily hidden recipe grid keeps its reveal state while a facet
      // reference search is visible. Do not lay rows out at zero width.
      if (bounds.width === 0) return;
      const style = window.getComputedStyle(grid);
      const next: GridFrame = {
        width: bounds.width - (Number.parseFloat(style.paddingLeft) || 0) - (Number.parseFloat(style.paddingRight) || 0),
        rowHeight: rowHeightFor(window.innerWidth, size),
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
  }, [hasResults, size, frame !== null]);

  const layout = frame
    ? layoutRows(results.map((shot) => cssAspect(aspectOf(shot))), frame.width, frame.rowHeight, ROW_GAP)
    : null;
  let minRows = MIN_VISIBLE_ROWS;
  if (layout && frame) {
    let filled = 0;
    let rows = 0;
    while (rows < layout.heights.length && filled < frame.room) filled += layout.heights[rows++] + ROW_GAP;
    minRows = Math.max(MIN_VISIBLE_ROWS, rows);
  }
  const shown = layout
    ? visibleTileCount(layout, { floor: visibleItemFloor, minRows, exhausted: !hasMore })
    : { count: Math.min(results.length, UNMEASURED_COUNT), rows: 0, atEnd: true };

  useEffect(() => {
    setVisibleItemFloor((current) => Math.max(current, shown.count));
  }, [shown.count]);

  if (!hasResults) return null;

  const visibleResults = results.slice(0, shown.count);
  const remainingRows = layout ? layout.sizes.length - shown.rows : 0;
  // Rows still waiting locally that are whole (a short last row waits for more).
  const wholeRowsLeft = hasMore ? Math.max(0, remainingRows - 1) : remainingRows;
  const movieCount = new Set(visibleResults.map((result) => result.film_id)).size;
  const sceneLabel = visibleResults.length === 1 ? "scene" : "scenes";
  const movieLabel = movieCount === 1 ? "movie" : "movies";
  const listLabel = `${visibleResults.length} of ${results.length} ${order === "chronological" ? "scenes in source order" : "ranked search results"} shown`;
  const card = (shot: SearchResult, index: number) => (
    <ShotCard
      shot={shot}
      position={index + 1}
      showDetails={showDetails}
      onFrameLoad={learnAspect}
      onClick={onShotClick}
      onUseInSearch={onUseInSearch}
      disabledUseFacets={disabledUseFacets}
      sourceReferenceFacet={sourceReferenceFacet}
      onToggleBookmark={onToggleBookmark}
      bookmarked={bookmarkedUnitIds.has(shot.unit_id)}
      bookmarkDisabled={bookmarkDisabled || pendingBookmarkUnitIds.has(shot.unit_id)}
    />
  );
  const showMore = () => {
    const nextRows = layout ? layout.sizes.slice(shown.rows, shown.rows + Math.min(ROWS_PER_REVEAL, wholeRowsLeft)) : [];
    const reveal = nextRows.reduce((sum, rowSize) => sum + rowSize, 0);
    // Rows that need the next results are sized like the ones already shown.
    const typicalRow = Math.max(1, Math.round(shown.count / Math.max(1, shown.rows)));
    const wanted = reveal + typicalRow * (ROWS_PER_REVEAL - nextRows.length);
    setVisibleItemFloor((current) => Math.max(current, shown.count + wanted));
    if (wholeRowsLeft < ROWS_PER_REVEAL && hasMore) onRequestMore?.();
  };

  return (
    <section className="search-results" aria-label={order === "chronological" ? "Scenes in source order" : "Ranked search results"}>
      <header className="result-toolbar">
        <p className="result-count" role="status" aria-live="polite">
          Showing {visibleResults.length} {sceneLabel}{" "}
          <span aria-hidden="true">&middot;</span> {movieCount} {movieLabel}
        </p>
      </header>
      {layout ? (
        <div
          ref={gridRef as RefObject<HTMLDivElement>}
          role="list"
          className="result-grid is-rows"
          style={{ "--row-h": `${frame?.rowHeight}px` } as CSSProperties}
          aria-label={listLabel}
        >
          {layout.sizes.slice(0, shown.rows).map((rowSize, row) => {
            const first = layout.sizes.slice(0, row).reduce((sum, size) => sum + size, 0);
            const short = shown.atEnd && layout.lastIsShort && row === layout.sizes.length - 1;
            return (
              <div key={visibleResults[first]?.unit_id ?? row} role="presentation" className={`result-row${short ? " is-short" : ""}`}>
                {visibleResults.slice(first, first + rowSize).map((shot, offset) => (
                  <div role="listitem" className="result-grid-item" key={shot.unit_id} style={tileStyle(shot)}>
                    {card(shot, first + offset)}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      ) : (
        <ol ref={gridRef as RefObject<HTMLOListElement>} className="result-grid is-end" aria-label={listLabel}>
          {visibleResults.map((shot, index) => (
            <li className="result-grid-item" key={shot.unit_id} style={tileStyle(shot)}>
              {card(shot, index)}
            </li>
          ))}
        </ol>
      )}
      {(shown.count < results.length || hasMore) && (
        <div className="result-more">
          <button
            type="button"
            className="result-more-button"
            disabled={revealDisabled}
            onClick={showMore}
            aria-label={order === "chronological" ? "Show more scenes" : "Show more ranked results"}
          >
            {revealDisabled && wholeRowsLeft === 0 && hasMore
              ? "Finding more…"
              : "Show more"}
          </button>
        </div>
      )}
    </section>
  );
}
