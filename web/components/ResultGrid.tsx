"use client";

import ShotCard from "./ShotCard";
import type { SearchResult } from "@/types/api";

interface ResultGridProps {
  results: SearchResult[];
  onShotClick: (shot: SearchResult) => void;
  onFindSimilar: (shot: SearchResult) => void;
  debug: boolean;
  similarDisabled?: boolean;
}

export default function ResultGrid({
  results,
  onShotClick,
  onFindSimilar,
  debug,
  similarDisabled = false,
}: ResultGridProps) {
  if (results.length === 0) return null;

  return (
    <ol className="result-grid" aria-label="Search results">
      {results.map((shot, index) => (
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
  );
}
