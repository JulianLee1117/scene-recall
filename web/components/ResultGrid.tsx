"use client";

import { useLayoutEffect, useRef, useState } from "react";
import ShotCard from "./ShotCard";
import type { RecipeMatchFacet, SearchResult } from "@/types/api";

const INITIAL_VISIBLE_ROWS = 3;
const ROWS_PER_REVEAL = 2;
const EMPTY_UNIT_IDS: ReadonlySet<string> = new Set();

export type ResultGrouping = "all" | "best-per-movie";

interface ResultGridProps {
  results: SearchResult[];
  grouping: ResultGrouping;
  onGroupingChange: (grouping: ResultGrouping) => void;
  revealDisabled?: boolean;
  onShotClick: (shot: SearchResult) => void;
  onFindSimilar?: (shot: SearchResult) => void;
  onUseInSearch?: (shot: SearchResult, facet: RecipeMatchFacet) => void;
  sourcePickerFacet?: RecipeMatchFacet;
  onToggleBookmark?: (shot: SearchResult) => void;
  bookmarkedUnitIds?: ReadonlySet<string>;
  pendingBookmarkUnitIds?: ReadonlySet<string>;
  bookmarkDisabled?: boolean;
  debug: boolean;
  similarDisabled?: boolean;
}

function resolvedColumnCount(grid: HTMLOListElement): number {
  const template = window.getComputedStyle(grid).gridTemplateColumns.trim();
  if (!template || template === "none") return 1;
  return Math.max(1, template.split(/\s+/).length);
}

export default function ResultGrid({
  results,
  grouping,
  onGroupingChange,
  revealDisabled = false,
  onShotClick,
  onFindSimilar,
  onUseInSearch,
  sourcePickerFacet,
  onToggleBookmark,
  bookmarkedUnitIds = EMPTY_UNIT_IDS,
  pendingBookmarkUnitIds = EMPTY_UNIT_IDS,
  bookmarkDisabled = false,
  debug,
  similarDisabled = false,
}: ResultGridProps) {
  const gridRef = useRef<HTMLOListElement>(null);
  const [columnCount, setColumnCount] = useState(1);
  const [visibleRows, setVisibleRows] = useState(INITIAL_VISIBLE_ROWS);
  const hasResults = results.length > 0;

  useLayoutEffect(() => {
    setVisibleRows(INITIAL_VISIBLE_ROWS);
  }, [results]);

  useLayoutEffect(() => {
    const grid = gridRef.current;
    if (!grid || !hasResults) return;

    const updateColumnCount = () => {
      const nextCount = resolvedColumnCount(grid);
      setColumnCount((currentCount) =>
        currentCount === nextCount ? currentCount : nextCount,
      );
    };

    updateColumnCount();
    const observer = new ResizeObserver(updateColumnCount);
    observer.observe(grid);
    return () => observer.disconnect();
  }, [hasResults]);

  if (!hasResults) return null;

  const visibleCount = visibleRows * columnCount;
  const visibleResults = results.slice(0, visibleCount);
  const remainingCount = results.length - visibleResults.length;
  const nextVisibleCount = Math.min(
    results.length,
    (visibleRows + ROWS_PER_REVEAL) * columnCount,
  );
  const nextBatchCount = nextVisibleCount - visibleResults.length;
  const movieCount = new Set(results.map((result) => result.film_id)).size;
  const sceneLabel = results.length === 1 ? "scene" : "scenes";
  const movieLabel = movieCount === 1 ? "movie" : "movies";

  return (
    <section className="search-results" aria-label="Ranked search results">
      <header className="result-toolbar">
        <p className="result-count" role="status" aria-live="polite">
          {results.length} {sceneLabel} <span aria-hidden="true">&middot;</span>{" "}
          {movieCount} {movieLabel}
        </p>
        <div className="result-view-toggle" role="group" aria-label="Result view">
          <button
            type="button"
            aria-pressed={grouping === "all"}
            onClick={() => onGroupingChange("all")}
          >
            All scenes
          </button>
          <button
            type="button"
            aria-pressed={grouping === "best-per-movie"}
            aria-label="Show one scene per represented movie"
            title="Show the highest-ranked returned scene from each movie"
            onClick={() => onGroupingChange("best-per-movie")}
          >
            One per movie
          </button>
        </div>
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
              onFindSimilar={onFindSimilar}
              onUseInSearch={onUseInSearch}
              sourcePickerFacet={sourcePickerFacet}
              onToggleBookmark={onToggleBookmark}
              bookmarked={bookmarkedUnitIds.has(shot.unit_id)}
              bookmarkDisabled={
                bookmarkDisabled || pendingBookmarkUnitIds.has(shot.unit_id)
              }
              similarDisabled={similarDisabled}
            />
          </li>
        ))}
      </ol>
      {remainingCount > 0 && (
        <div className="result-more">
          <button
            type="button"
            className="result-more-button"
            disabled={revealDisabled}
            onClick={() => setVisibleRows((rows) => rows + ROWS_PER_REVEAL)}
            aria-label={`Show ${nextBatchCount} more ranked ${nextBatchCount === 1 ? "result" : "results"}`}
          >
            Show more
          </button>
          <span>{remainingCount} remaining</span>
        </div>
      )}
    </section>
  );
}
