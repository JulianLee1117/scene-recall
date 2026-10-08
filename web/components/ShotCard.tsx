"use client";

import { useState, useRef, useCallback, useId } from "react";
import UseInSearchMenu from "./UseInSearchMenu";
import FacetIcon from "./FacetIcon";
import BookmarkIcon from "./BookmarkIcon";
import { FACET_LABELS, sourceDraftFromShot, writeSceneSourceDrag } from "@/lib/searchRecipe";
import { useScenePointerDrag } from "@/hooks/useScenePointerDrag";
import { setNativeDragPreview } from "@/lib/nativeDragPreview";
import { foundBy, readableEvidence, TEXT_VIEW_LABELS } from "@/lib/matchReasons";
import MatchBreakdown from "./MatchBreakdown";
import type { RecipeMatchFacet, SearchResult } from "@/types/api";
import { formatTime, filmLabel } from "@/lib/format";

interface ShotCardProps {
  shot: SearchResult;
  position: number;
  showDetails: boolean;
  showRank?: boolean;
  allowSourceDrag?: boolean;
  /** Reports the keyframe's natural size so the grid can keep the film's frame shape. */
  onFrameLoad?: (filmId: string, width: number, height: number) => void;
  onClick: (shot: SearchResult) => void;
  onUseInSearch?: (shot: SearchResult, facet: RecipeMatchFacet) => void;
  disabledUseFacets?: ReadonlySet<RecipeMatchFacet>;
  sourceReferenceFacet?: RecipeMatchFacet;
  bookmarked?: boolean;
  bookmarkDisabled?: boolean;
  onToggleBookmark?: (shot: SearchResult) => void;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
const BADGE_LABELS = { iconic: "Iconic", gem: "Hidden gem" } as const;

export default function ShotCard({
  shot,
  position,
  showDetails,
  showRank = true,
  allowSourceDrag = true,
  onFrameLoad,
  onClick,
  onUseInSearch,
  disabledUseFacets,
  sourceReferenceFacet,
  bookmarked = false,
  bookmarkDisabled = false,
  onToggleBookmark,
}: ShotCardProps) {
  const [hovered, setHovered] = useState(false);
  const [dragging, setDragging] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const suppressClickRef = useRef(false);
  const detailsId = useId();
  const displayedRank = shot.rank ?? position;
  const evidenceTime =
    shot.matched_line?.t_start ?? shot.matched_frame_timestamp ?? shot.focus_start ?? shot.t_start;
  const filmTitle = shot.film_title ?? filmLabel(shot.film_id);
  const sceneMore = shot.scene_alternatives?.length ?? 0;
  const matchedTextLabel = shot.matched_text_view
    ? (TEXT_VIEW_LABELS[shot.matched_text_view] ?? "Text")
    : null;
  const finders = foundBy(shot);
  const sourceAvailable = Number.isInteger(shot.keyframe_index);
  // Scenes are modular: drag one onto a search category, or use its Related menu.
  const canDragSource = Boolean(
    allowSourceDrag && !sourceReferenceFacet && onUseInSearch && sourceAvailable,
  );
  const pointerDrag = useScenePointerDrag(canDragSource ? sourceDraftFromShot("scene", shot) : null, { onDragging: setDragging });

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
    if (suppressClickRef.current) return;
    handleMouseLeave();
    onClick(shot);
  }, [handleMouseLeave, onClick, shot]);

  const handleToggleBookmark = useCallback(() => {
    onToggleBookmark?.(shot);
  }, [onToggleBookmark, shot]);

  return (
    <article
      className={`result-card${showDetails ? " has-details" : ""}${sourceReferenceFacet ? " is-source-reference-result" : ""}${dragging ? " is-dragging" : ""}`}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <button
        type="button"
        className="result-card-primary"
        draggable={canDragSource}
        {...pointerDrag}
        onDragStart={(event) => {
          pointerDrag.onDragStart(event);
          if (event.defaultPrevented) return;
          if (!canDragSource || !writeSceneSourceDrag(event.dataTransfer, shot)) {
            event.preventDefault();
            return;
          }
          suppressClickRef.current = true;
          handleMouseLeave();
          setNativeDragPreview(event.dataTransfer, {
            eyebrow: "Scene", title: filmTitle, detail: formatTime(evidenceTime),
            imageUrl: `${API_URL}${shot.keyframe_url}`,
          });
          setDragging(true);
        }}
        onDragEnd={() => {
          setDragging(false);
          window.setTimeout(() => { suppressClickRef.current = false; }, 150);
        }}
        onClick={handleClick}
        onFocus={handleMouseEnter}
        onBlur={handleMouseLeave}
        title={shot.caption}
        aria-label={`Result ${displayedRank}: ${filmTitle} at ${formatTime(evidenceTime)}. ${shot.caption}`}
        aria-describedby={showDetails ? detailsId : undefined}
      >
        <span className="result-card-media">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={`${API_URL}${shot.thumbnail_url ?? shot.keyframe_url}`}
            alt=""
            loading="lazy"
            draggable={false}
            onLoad={(event) => onFrameLoad?.(shot.film_id, event.currentTarget.naturalWidth, event.currentTarget.naturalHeight)}
            style={{ opacity: hovered ? 0 : 1 }}
          />

          <video
            ref={videoRef}
            src={`${API_URL}${shot.preview_url}`}
            muted
            loop
            playsInline
            preload="none"
            aria-hidden="true"
            draggable={false}
            style={{ opacity: hovered ? 1 : 0 }}
          />

          {(showRank || (shot.badges?.length ?? 0) > 0) && (
            <span className="result-card-marks">
              {showRank && (
                <span className="rank-badge" aria-hidden="true">
                  {displayedRank}
                </span>
              )}
              {shot.badges?.map((badge) => (
                <span key={badge} className={`result-badge result-badge-${badge}`} title={BADGE_LABELS[badge]}>
                  {BADGE_LABELS[badge]}
                </span>
              ))}
            </span>
          )}

          {sceneMore > 0 && (
            <span
              className="scene-more"
              title={`${sceneMore} more matching shot${sceneMore === 1 ? "" : "s"} from this scene`}
            >
              +{sceneMore}
            </span>
          )}

          <span className="result-card-overlay" style={{ opacity: hovered ? 1 : 0 }}>
            {shot.matched_line ? (
              <span className="result-match-evidence">
                <span>Line</span>
                <span>{shot.matched_line.text}</span>
              </span>
            ) : matchedTextLabel && shot.matched_text ? (
              <span className="result-match-evidence">
                <span>{matchedTextLabel}</span>
                <span>{readableEvidence(shot.matched_text_view ?? "", shot.matched_text)}</span>
              </span>
            ) : shot.action ? (
              <span className="result-match-evidence">
                <span>{shot.scene?.title || "Story"}</span>
                <span>{shot.action}</span>
              </span>
            ) : null}
            {finders.length > 0 && (
              <span className="result-match-facets" aria-label={`Found by ${finders.join(", ")}`}>
                {finders.map((label) => (
                  <span key={label}>{label}</span>
                ))}
              </span>
            )}
            <span className="result-film">{filmTitle}</span>
            <span className="result-time">{formatTime(evidenceTime)}</span>
          </span>
        </span>

        {showDetails && <ResultDetails id={detailsId} shot={shot} />}
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
              aria-label={bookmarked ? `Remove result ${displayedRank} from Saved` : `Save result ${displayedRank}`}
              aria-pressed={bookmarked}
              title={bookmarked ? "Remove from Saved" : "Save scene"}
            >
              <BookmarkIcon filled={bookmarked} />
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

/** The description and why the scene ranked here, finder by finder. */
function ResultDetails({ id, shot }: { id: string; shot: SearchResult }) {
  return (
    <span className="result-details" id={id}>
      <span className="result-details-caption">{shot.caption || "No description yet"}</span>
      <MatchBreakdown
        shot={shot}
        compact
        omitDetail={shot.caption}
        aside={`${formatTime(shot.t_start)} – ${formatTime(shot.t_end)}`}
      />
    </span>
  );
}
