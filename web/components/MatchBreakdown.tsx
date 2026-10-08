import type { ReactNode } from "react";
import { matchBreakdown, type MatchColumn } from "@/lib/matchReasons";
import type { SearchResult } from "@/types/api";

/**
 * How each finder ranked a scene, as an aligned table: finder, its rank (or
 * the rerank score), and what it matched. Spans only, so it can sit in a button.
 */
export default function MatchBreakdown({
  shot,
  columns,
  compact = false,
  aside,
}: {
  shot: SearchResult;
  /** The finders this search reports; every card lists the same rows in the same order. */
  columns?: MatchColumn[];
  /** Cards keep each row to one line. */
  compact?: boolean;
  /** Shown in the footer, e.g. the shot's time range. */
  aside?: ReactNode;
}) {
  const { rows, score } = matchBreakdown(shot, columns);
  if (rows.length === 0 && !aside) return null;
  return (
    <span className={`match-table${compact ? " is-compact" : ""}`}>
      {rows.map((row) => (
        <span key={row.column} className={`match-row${row.matched ? "" : " is-unmatched"}`}>
          <span className="match-label" title={row.hint}>{row.label}</span>
          <span className="match-value">{row.value}</span>
          <span className="match-detail" title={row.detail}>{row.detail}</span>
        </span>
      ))}
      {(aside || score !== undefined) && (
        <span className="match-foot">
          {aside && <span>{aside}</span>}
          {score !== undefined && <span title="Final ordering score after rerank and priors">score {score.toFixed(3)}</span>}
        </span>
      )}
    </span>
  );
}
