"use client";

import ShotCard from "./ShotCard";
import type { SearchResult } from "@/types/api";

interface ResultGridProps {
  results: SearchResult[];
  visibleCount: number;
  batchSize: number;
  revealDisabled?: boolean;
  onShowMore: () => void;
  onShotClick: (shot: SearchResult) => void;
  onFindSimilar: (shot: SearchResult) => void;
  debug: boolean;
  similarDisabled?: boolean;
}

export default function ResultGrid({
  results,
  visibleCount,
  batchSize,
  revealDisabled = false,
  onShowMore,
  onShotClick,
  onFindSimilar,
  debug,
  similarDisabled = false,
}: ResultGridProps) {
  if (results.length === 0) return null;

  const visibleResults = results.slice(0, visibleCount);
  const remainingCount = results.length - visibleResults.length;
  const nextBatchCount = Math.min(batchSize, remainingCount);

  return (
    <section className="search-results" aria-label="Ranked search results">
      <p className="result-count" role="status" aria-live="polite">
        {visibleResults.length} of {results.length} ranked results
      </p>
      <ol
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
            onClick={onShowMore}
            aria-label={`Show ${nextBatchCount} more ranked results`}
          >
            Show {nextBatchCount} more
          </button>
          <span>{remainingCount} remaining</span>
        </div>
      )}
    </section>
  );
}
