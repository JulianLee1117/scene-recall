"use client";

import { useState, useRef, useCallback, useId } from "react";
import UseInSearchMenu from "./UseInSearchMenu";
import FacetIcon from "./FacetIcon";
import BookmarkIcon from "./BookmarkIcon";
import { FACET_LABELS, sourceDraftFromShot, writeSceneSourceDrag } from "@/lib/searchRecipe";
import { useScenePointerDrag } from "@/hooks/useScenePointerDrag";
import { setNativeDragPreview } from "@/lib/nativeDragPreview";
import { hoverEvidence, matchedWordsEvidence, type MatchColumn } from "@/lib/matchReasons";
import MatchBreakdown from "./MatchBreakdown";
import type { RecipeMatchFacet, SearchResult } from "@/types/api";
import { displayTitle, formatTime, filmLabel } from "@/lib/format";
import { displayMoment, hoverPreviewUrl, previewOffset } from "@/lib/resultMoment";

interface ShotCardProps {
  shot: SearchResult;
  position: number;
  showDetails: boolean;
  /** The finders this search reports, so every card's Details lists the same rows. */
  matchColumns?: MatchColumn[];
  showRank?: boolean;
  allowSourceDrag?: boolean;
  /** Reports the keyframe's natural size so the grid can keep the film's frame shape. */
  onFrameLoad?: (scene: SearchResult, width: number, height: number) => void;
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
  matchColumns,
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
  const [playingPreview, setPlayingPreview] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const suppressClickRef = useRef(false);
  const detailsId = useId();
  const displayedRank = shot.rank ?? position;
  const evidenceTime =
    matchedWordsEvidence(shot)?.t_start ?? displayMoment(shot);
  const filmTitle = displayTitle(shot.film_title ?? filmLabel(shot.film_id));
  const sceneMore = shot.scene_alternatives?.length ?? 0;
  const evidence = hoverEvidence(shot);
  const previewUrl = hoverPreviewUrl(shot);
  const showPreview = hovered && previewUrl !== null && playingPreview === previewUrl;
  const sourceAvailable = Number.isInteger(shot.keyframe_index);
  // Scenes are modular: drag one onto a search category, or use its Related menu.
  const canDragSource = Boolean(
    allowSourceDrag && !sourceReferenceFacet && onUseInSearch && sourceAvailable,
  );
  const pointerDrag = useScenePointerDrag(canDragSource ? sourceDraftFromShot("scene", shot) : null, { onDragging: setDragging });

  // The preview starts on the picture the card shows, not at the clip's start.
  const previewStart = previewOffset(shot);
  const handleMouseEnter = useCallback(() => {
    setHovered(true);
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = previewStart;
    video.play().catch(() => {
      setPlayingPreview((current) => current === previewUrl ? null : current);
    });
  }, [previewUrl, previewStart]);

  const handleMouseLeave = useCallback(() => {
    setHovered(false);
    setPlayingPreview(null);
    if (videoRef.current) {
      videoRef.current.pause();
      videoRef.current.currentTime = previewStart;
    }
  }, [previewStart]);

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
        aria-label={`Result ${displayedRank}: ${filmTitle} at ${formatTime(evidenceTime)}. ${evidence?.text ?? shot.caption}`}
        aria-describedby={showDetails ? detailsId : undefined}
      >
        <span className="result-card-media">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={`${API_URL}${shot.thumbnail_url ?? shot.keyframe_url}`}
            alt=""
            loading="lazy"
            draggable={false}
            onLoad={(event) => onFrameLoad?.(shot, event.currentTarget.naturalWidth, event.currentTarget.naturalHeight)}
            style={{ opacity: showPreview ? 0 : 1 }}
          />

          {previewUrl && <video
            key={previewUrl}
            ref={videoRef}
            src={`${API_URL}${previewUrl}`}
            muted
            loop
            playsInline
            preload="none"
            aria-hidden="true"
            draggable={false}
            onPlaying={() => setPlayingPreview(previewUrl)}
            onLoadStart={() => setPlayingPreview(null)}
            onPause={() => setPlayingPreview(null)}
            onWaiting={() => setPlayingPreview(null)}
            onError={() => setPlayingPreview(null)}
            style={{ opacity: showPreview ? 1 : 0 }}
          />}

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
              title={`${sceneMore} more matching shot${sceneMore === 1 ? "" : "s"}`}
            >
              +{sceneMore}
            </span>
          )}

          <span className="result-card-overlay" style={{ opacity: hovered ? 1 : 0 }}>
            {/* Film and time first, then shot context or useful matched words. */}
            <span className="result-overlay-title">
              <span className="result-film">{filmTitle}</span>
              <span className="result-time">{formatTime(evidenceTime)}</span>
            </span>
            {evidence && (
              <span className="result-overlay-evidence">
                {evidence.kind && <><span className="result-overlay-kind">{evidence.kind}</span>{" "}</>}
                {evidence.text}
              </span>
            )}
          </span>
        </span>

        {showDetails && <ResultDetails id={detailsId} shot={shot} columns={matchColumns} />}
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
function ResultDetails({ id, shot, columns }: { id: string; shot: SearchResult; columns?: MatchColumn[] }) {
  return (
    <span className="result-details" id={id}>
      <span className="result-details-caption">{shot.caption || "No description yet"}</span>
      <MatchBreakdown
        shot={shot}
        columns={columns}
        compact
        aside={`${formatTime(shot.t_start)} – ${formatTime(shot.t_end)}`}
      />
    </span>
  );
}
