import type { ReactNode } from "react";
import { matchBreakdown, type Strength } from "@/lib/matchReasons";
import type { SearchResult } from "@/types/api";

export function MatchMeter({ strength, label }: { strength: Strength; label: string }) {
  return (
    <span className="match-meter" role="img" aria-label={label} title={label}>
      {[1, 2, 3, 4, 5].map((step) => (
        <span key={step} className={step <= strength ? "is-on" : undefined} />
      ))}
    </span>
  );
}

/**
 * Why a scene ranked where it did: its overall fit, then each finder with a
 * five-step meter and what it matched. Spans only, so it can sit in a button.
 */
export default function MatchBreakdown({
  shot,
  compact = false,
  aside,
  omitDetail,
}: {
  shot: SearchResult;
  /** Cards show the three strongest finders on one line each. */
  compact?: boolean;
  /** Shown at the end of the fit row, e.g. the shot's time range. */
  aside?: ReactNode;
  /** A detail already shown nearby (the description) is not repeated. */
  omitDetail?: string;
}) {
  const { fit, rows } = matchBreakdown(shot);
  const shown = compact ? rows.slice(0, 3) : rows;
  if (!fit && shown.length === 0 && !aside) return null;
  return (
    <span className={`match-breakdown${compact ? " is-compact" : ""}`}>
      {(fit || aside) && (
        <span className="match-fit">
          {fit && (
            <>
              <span className="match-fit-word">{fit.word}</span>
              <MatchMeter strength={fit.strength} label={`${fit.word} for your description`} />
            </>
          )}
          {aside && <span className="match-aside">{aside}</span>}
        </span>
      )}
      {shown.map((row) => (
        <span key={`${row.label}:${row.note}`} className="match-row" title={row.note}>
          <span className="match-label">{row.label}</span>
          <MatchMeter strength={row.strength} label={row.note} />
          {row.detail && (
            <span className="match-detail">{row.detail === omitDetail ? "Matches the description above" : row.detail}</span>
          )}
        </span>
      ))}
    </span>
  );
}
