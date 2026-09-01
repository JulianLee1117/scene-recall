"use client";

import { useLayoutEffect, useRef, useState } from "react";
import ShotCard from "./ShotCard";
import type { RecipeMatchFacet, SearchResult } from "@/types/api";

const MIN_VISIBLE_ROWS = 3;
const ROWS_PER_REVEAL = 2;
const VIEWPORT_BOTTOM_GUTTER = 24;
const EMPTY_UNIT_IDS: ReadonlySet<string> = new Set();

interface ResultGridProps {
  results: SearchResult[];
  streamKey: number;
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
  debug: boolean;
}

function resolvedColumnCount(grid: HTMLOListElement): number | null {
  // A temporarily hidden recipe grid keeps its reveal state while a facet
  // reference search is visible. Do not reinterpret display:none as one column.
  if (grid.getBoundingClientRect().width === 0) return null;
  const template = window.getComputedStyle(grid).gridTemplateColumns.trim();
  if (!template || template === "none") return 1;
  return Math.max(1, template.split(/\s+/).length);
}

export default function ResultGrid({
  results,
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
  debug,
}: ResultGridProps) {
  const gridRef = useRef<HTMLOListElement>(null);
  const [columnCount, setColumnCount] = useState(1);
  const [visibleItemFloor, setVisibleItemFloor] = useState(0);
  const hasResults = results.length > 0;

  useLayoutEffect(() => {
    setVisibleItemFloor(0);
  }, [streamKey]);

  useLayoutEffect(() => {
    const grid = gridRef.current;
    if (!grid || !hasResults) return;

    const updateLayout = () => {
      const nextCount = resolvedColumnCount(grid);
      if (nextCount === null) return;
      setColumnCount((currentCount) =>
        currentCount === nextCount ? currentCount : nextCount,
      );

      const firstItem = grid.querySelector<HTMLElement>(".result-grid-item");
      if (!firstItem) return;

      const firstItemRect = firstItem.getBoundingClientRect();
      const rowGap =
        Number.parseFloat(window.getComputedStyle(grid).rowGap) || 0;
      const availableHeight = Math.max(
        0,
        window.innerHeight - firstItemRect.top - VIEWPORT_BOTTOM_GUTTER,
      );
      const viewportRows = Math.max(
        MIN_VISIBLE_ROWS,
        Math.ceil((availableHeight + rowGap) / (firstItemRect.height + rowGap)),
      );
      // Preserve the number of results the user has seen, not merely the
      // number of rows. A narrower grid has fewer columns, so row-based state
      // could otherwise hide already revealed scenes after a resize.
      setVisibleItemFloor((currentCount) =>
        Math.max(currentCount, viewportRows * nextCount),
      );
    };

    updateLayout();
    const observer = new ResizeObserver(updateLayout);
    observer.observe(grid);
    window.addEventListener("resize", updateLayout);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", updateLayout);
    };
  }, [hasResults, results]);

  if (!hasResults) return null;

  const visibleCount = Math.min(
    results.length,
    Math.ceil(
      Math.max(visibleItemFloor, MIN_VISIBLE_ROWS * columnCount) / columnCount,
    ) * columnCount,
  );
  const visibleResults = results.slice(0, visibleCount);
  const remainingCount = results.length - visibleResults.length;
  const movieCount = new Set(visibleResults.map((result) => result.film_id)).size;
  const sceneLabel = visibleResults.length === 1 ? "scene" : "scenes";
  const movieLabel = movieCount === 1 ? "movie" : "movies";

  return (
    <section className="search-results" aria-label="Ranked search results">
      <header className="result-toolbar">
        <p className="result-count" role="status" aria-live="polite">
          Showing {visibleResults.length} {sceneLabel}{" "}
          <span aria-hidden="true">&middot;</span> {movieCount} {movieLabel}
        </p>
      </header>
      <ol
        ref={gridRef}
        className="result-grid"
        aria-label={`${visibleResults.length} of ${results.length} ranked search results shown`}
      >
        {visibleResults.map((shot, index) => (
          <li className="result-grid-item" key={shot.unit_id}>
            <ShotCard
              shot={shot}
              position={index + 1}
              debug={debug}
              onClick={onShotClick}
              onUseInSearch={onUseInSearch}
              disabledUseFacets={disabledUseFacets}
              sourceReferenceFacet={sourceReferenceFacet}
              onToggleBookmark={onToggleBookmark}
              bookmarked={bookmarkedUnitIds.has(shot.unit_id)}
              bookmarkDisabled={
                bookmarkDisabled || pendingBookmarkUnitIds.has(shot.unit_id)
              }
            />
          </li>
        ))}
      </ol>
      {(remainingCount > 0 || hasMore) && (
        <div className="result-more">
          <button
            type="button"
            className="result-more-button"
            disabled={revealDisabled}
            onClick={() => {
              setVisibleItemFloor(
                (currentCount) =>
                  Math.max(
                    currentCount,
                    visibleResults.length + ROWS_PER_REVEAL * columnCount,
                  ),
              );
              if (remainingCount === 0 && hasMore) onRequestMore?.();
            }}
            aria-label="Show more ranked results"
          >
            {revealDisabled && remainingCount === 0 && hasMore
              ? "Finding more…"
              : "Show more"}
          </button>
        </div>
      )}
    </section>
  );
}
