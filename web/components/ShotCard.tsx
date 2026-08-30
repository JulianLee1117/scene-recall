"use client";

import { useState, useRef, useCallback, useId } from "react";
import UseInSearchMenu from "./UseInSearchMenu";
import FacetIcon from "./FacetIcon";
import { FACET_LABELS, writeSceneSourceDrag } from "@/lib/searchRecipe";
import type {
  RecipeMatchFacet,
  SearchMatch,
  SearchResult,
} from "@/types/api";
import { formatTime, filmLabel } from "@/lib/format";

interface ShotCardProps {
  shot: SearchResult;
  position: number;
  debug: boolean;
  showRank?: boolean;
  onClick: (shot: SearchResult) => void;
  onUseInSearch?: (shot: SearchResult, facet: RecipeMatchFacet) => void;
  disabledUseFacets?: ReadonlySet<RecipeMatchFacet>;
  sourceReferenceFacet?: RecipeMatchFacet;
  bookmarked?: boolean;
  bookmarkDisabled?: boolean;
  onToggleBookmark?: (shot: SearchResult) => void;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
const CHANNEL_LABELS = {
  img: "img",
  txt: "txt",
  lex: "lex",
  spatial: "pos",
} as const;
const TEXT_VIEW_LABELS: Record<string, string> = {
  caption: "Visual description",
  dialogue: "Dialogue",
  ocr: "On-screen text",
  facets: "Scene detail",
  mood: "Mood",
};

function unitSuffix(unitId: string): string {
  const separator = unitId.lastIndexOf("_");
  if (separator >= 0 && separator < unitId.length - 1) {
    return unitId.slice(separator + 1);
  }
  return unitId.slice(-8);
}

function formatScore(score: number | undefined): string {
  return typeof score === "number" && Number.isFinite(score)
    ? score.toFixed(4)
    : "—";
}

function matchEvidenceText(match: SearchMatch): string {
  const label = `${FACET_LABELS[match.facet]} #${match.rank}`;
  if (match.evidence?.type === "text") {
    const viewLabel =
      TEXT_VIEW_LABELS[match.evidence.view] ?? match.evidence.view;
    return `${label} · ${viewLabel}: ${match.evidence.text}`;
  }
  if (match.evidence?.type === "frame") {
    const timestamp =
      typeof match.evidence.timestamp === "number"
        ? ` at ${formatTime(match.evidence.timestamp)}`
        : "";
    return `${label} · frame ${match.evidence.frame_index + 1}${timestamp}`;
  }
  return label;
}

export default function ShotCard({
  shot,
  position,
  debug,
  showRank = true,
  onClick,
  onUseInSearch,
  disabledUseFacets,
  sourceReferenceFacet,
  bookmarked = false,
  bookmarkDisabled = false,
  onToggleBookmark,
}: ShotCardProps) {
  const [hovered, setHovered] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const debugDescriptionId = useId();
  const displayedRank = shot.rank ?? position;
  const evidenceTime = shot.matched_frame_timestamp ?? shot.t_start;
  const matchedTextLabel = shot.matched_text_view
    ? (TEXT_VIEW_LABELS[shot.matched_text_view] ?? "Text")
    : null;
  const matchedFacetLabels = Array.from(
    new Set((shot.matches ?? []).map((match) => FACET_LABELS[match.facet])),
  );
  const sourceAvailable = Number.isInteger(shot.keyframe_index);

  const handleMouseEnter = useCallback(() => {
    setHovered(true);
    videoRef.current?.play().catch(() => {});
  }, []);

  const handleMouseLeave = useCallback(() => {
    setHovered(false);
    if (videoRef.current) {
      videoRef.current.pause();
      videoRef.current.currentTime = 0;
    }
  }, []);

  const handleClick = useCallback(() => {
    handleMouseLeave();
    onClick(shot);
  }, [handleMouseLeave, onClick, shot]);

  const handleToggleBookmark = useCallback(() => {
    onToggleBookmark?.(shot);
  }, [onToggleBookmark, shot]);

  return (
    <article
      className={`result-card${debug ? " result-card-debug" : ""}${sourceReferenceFacet ? " is-source-reference-result" : ""}`}
      draggable={Boolean(
        !sourceReferenceFacet && onUseInSearch && sourceAvailable,
      )}
      onDragStart={(event) => {
        if (sourceReferenceFacet) {
          event.preventDefault();
          return;
        }
        if (!writeSceneSourceDrag(event.dataTransfer, shot)) {
          event.preventDefault();
        }
      }}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <button
        type="button"
        className="result-card-primary"
        onClick={handleClick}
        onFocus={handleMouseEnter}
        onBlur={handleMouseLeave}
        title={shot.caption}
        aria-label={`Result ${displayedRank}: ${shot.caption}, ${formatTime(evidenceTime)}`}
        aria-describedby={debug ? debugDescriptionId : undefined}
      >
        <span className="result-card-media">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={`${API_URL}${shot.keyframe_url}`}
            alt=""
            loading="lazy"
            style={{
              opacity: hovered ? 0 : 1,
            }}
          />

          <video
            ref={videoRef}
            src={`${API_URL}${shot.preview_url}`}
            muted
            loop
            playsInline
            preload="none"
            aria-hidden="true"
            style={{
              opacity: hovered ? 1 : 0,
            }}
          />

          {showRank && (
            <span className="rank-badge" aria-hidden="true">
              {displayedRank}
            </span>
          )}

          <span
            className="result-card-overlay"
            style={{
              opacity: hovered ? 1 : 0,
            }}
          >
            {matchedTextLabel && shot.matched_text && (
              <span className="result-match-evidence">
                <span>{matchedTextLabel} match</span>
                <span>{shot.matched_text}</span>
              </span>
            )}
            {matchedFacetLabels.length > 0 && (
              <span
                className="result-match-facets"
                aria-label={`Matched by ${matchedFacetLabels.join(", ")}`}
              >
                {matchedFacetLabels.map((label) => (
                  <span key={label}>{label}</span>
                ))}
              </span>
            )}
            <span className="result-film">
              {shot.film_title ?? filmLabel(shot.film_id)}
            </span>
            <span className="result-time">{formatTime(evidenceTime)}</span>
          </span>
        </span>

        {debug && (
          <span className="result-debug" id={debugDescriptionId}>
            <span className="result-debug-caption">
              {shot.caption || "No caption"}
            </span>
            <span className="result-debug-line">
              <span>
                {formatTime(shot.t_start)} · …{unitSuffix(shot.unit_id)}
              </span>
              <span>score {formatScore(shot.debug?.final_score)}</span>
            </span>
            {typeof shot.matched_frame_index === "number" && (
              <span className="result-debug-line">
                <span>matched frame {shot.matched_frame_index + 1}</span>
                <span>
                  {typeof shot.matched_frame_timestamp === "number"
                    ? formatTime(shot.matched_frame_timestamp)
                    : "time unavailable"}
                </span>
              </span>
            )}
            {shot.debug?.query_ranks && (
              <span className="result-debug-line">
                <span>
                  composition #{shot.debug.query_ranks.reference ?? "—"}
                </span>
                <span>text #{shot.debug.query_ranks.text ?? "—"}</span>
              </span>
            )}
            {(shot.matches?.length ?? 0) > 0 && (
              <span className="result-debug-matches">
                {shot.matches?.map((match) => (
                  <span key={match.clause_id} title={matchEvidenceText(match)}>
                    {matchEvidenceText(match)}
                  </span>
                ))}
              </span>
            )}
            <span className="result-debug-channels">
              {(Object.keys(CHANNEL_LABELS) as Array<
                keyof typeof CHANNEL_LABELS
              >).map((channel) => (
                <span key={channel}>
                  {CHANNEL_LABELS[channel]}{" "}
                  {shot.debug?.channels?.[channel]
                    ? `#${shot.debug?.channels?.[channel]?.rank}`
                    : "—"}
                </span>
              ))}
            </span>
          </span>
        )}
      </button>

      {(onToggleBookmark || onUseInSearch) && (
        <div className="result-card-actions">
          {onToggleBookmark && (
            <button
              type="button"
              className={`result-card-action bookmark-button${bookmarked ? " is-active" : ""}`}
              disabled={bookmarkDisabled}
              onClick={handleToggleBookmark}
              onFocus={handleMouseEnter}
              onBlur={handleMouseLeave}
              onDragStart={(event) => event.preventDefault()}
              aria-label={
                bookmarked
                  ? `Remove result ${displayedRank} from Saved`
                  : `Save result ${displayedRank}`
              }
              aria-pressed={bookmarked}
              title={bookmarked ? "Remove from Saved" : "Save scene"}
            >
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill={bookmarked ? "currentColor" : "none"}
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M6 4.75A1.75 1.75 0 0 1 7.75 3h8.5A1.75 1.75 0 0 1 18 4.75V21l-6-3.75L6 21V4.75Z" />
              </svg>
            </button>
          )}
          {onUseInSearch && sourceReferenceFacet ? (
            <button
              type="button"
              className="result-card-action source-picker-use-action"
              disabled={!sourceAvailable}
              onClick={() => onUseInSearch(shot, sourceReferenceFacet)}
              onFocus={handleMouseEnter}
              onBlur={handleMouseLeave}
              onDragStart={(event) => event.preventDefault()}
              aria-label={`Use result ${displayedRank} for ${FACET_LABELS[sourceReferenceFacet]}`}
              title={`Use for ${FACET_LABELS[sourceReferenceFacet]}`}
            >
              <FacetIcon facet={sourceReferenceFacet} size={14} />
              <span>Use for {FACET_LABELS[sourceReferenceFacet]}</span>
            </button>
          ) : onUseInSearch ? (
            <UseInSearchMenu
              shot={shot}
              onUse={onUseInSearch}
              disabled={!sourceAvailable}
              disabledFacets={disabledUseFacets}
            />
          ) : null}
        </div>
      )}
    </article>
  );
}
