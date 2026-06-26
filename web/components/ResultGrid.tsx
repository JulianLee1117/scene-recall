"use client";

import ShotCard from "./ShotCard";
import type { SearchResult } from "@/types/api";

interface ResultGridProps {
  results: SearchResult[];
  onShotClick: (shot: SearchResult) => void;
}

export default function ResultGrid({ results, onShotClick }: ResultGridProps) {
  if (results.length === 0) return null;

  return (
    <div className="masonry-grid" style={{ padding: "16px 12px" }}>
      {results.map((shot) => (
        <ShotCard key={shot.unit_id} shot={shot} onClick={onShotClick} />
      ))}
    </div>
  );
}
