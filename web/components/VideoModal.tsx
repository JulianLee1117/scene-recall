"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import UseInSearchMenu from "./UseInSearchMenu";
import FacetIcon from "./FacetIcon";
import BookmarkIcon from "./BookmarkIcon";
import { displayTitle, filmLabel, formatTime } from "@/lib/format";
import { FACET_LABELS } from "@/lib/searchRecipe";
import { matchBreakdown } from "@/lib/matchReasons";
import MatchBreakdown from "./MatchBreakdown";
import type { RecipeMatchFacet, SearchResult } from "@/types/api";

interface VideoModalProps {
  shot: SearchResult;
  onClose: () => void;
  onUseInSearch?: (shot: SearchResult, facet: RecipeMatchFacet) => void;
  disabledUseFacets?: ReadonlySet<RecipeMatchFacet>;
  sourceReferenceFacet?: RecipeMatchFacet;
  bookmarked?: boolean;
  bookmarkDisabled?: boolean;
  onToggleBookmark?: (shot: SearchResult) => void;
}

const BADGE_LABELS = { iconic: "Iconic", gem: "Hidden gem" } as const;

export default function VideoModal({
  shot,
  onClose,
  onUseInSearch,
  disabledUseFacets,
  sourceReferenceFacet,
  bookmarked = false,
  bookmarkDisabled = false,
  onToggleBookmark,
}: VideoModalProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  const hasSeenCanPlay = useRef(false);
  const [timestampCopied, setTimestampCopied] = useState(false);
  const titleId = useId();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
  const [playbackAttempt, setPlaybackAttempt] = useState(0);
  const [playback, setPlayback] = useState<{
    filmId: string; attempt: number; url?: string; error?: string;
  } | null>(null);
  const currentPlayback = playback?.filmId === shot.film_id && playback.attempt === playbackAttempt ? playback : null;
  const evidenceTime = shot.matched_frame_timestamp ?? shot.focus_start ?? shot.t_start;
  const [playheadTime, setPlayheadTime] = useState(evidenceTime);
  // A matched subtitle line is the most precise moment to start from.
  const seekTarget = Math.max(0, (shot.matched_line?.t_start ?? evidenceTime) - 1);
  const filmTitle = displayTitle(shot.film_title ?? filmLabel(shot.film_id));
  const hasBreakdown = matchBreakdown(shot).rows.length > 0;
  // Match cuts start from what is on screen while it is still this shot.
  const matchTime = playheadTime >= shot.t_start && playheadTime <= shot.t_end ? playheadTime : evidenceTime;
  const matchCutsHref = `/match?unit_id=${encodeURIComponent(shot.unit_id)}&time=${matchTime.toFixed(3)}`;

  useEffect(() => {
    hasSeenCanPlay.current = false;
    setPlayheadTime(evidenceTime);
    setTimestampCopied(false);
  }, [shot, evidenceTime]);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const controller = new AbortController();
    const filmId = shot.film_id;
    hasSeenCanPlay.current = false;
    void (async () => {
      let errorMessage = "Playback could not be loaded. Check that the API and film are available, then retry.";
      try {
        const response = await fetch(`${apiUrl}/video/${encodeURIComponent(filmId)}/playback`, {
          signal: controller.signal, cache: "no-store",
        });
        if (!response.ok) {
          const problem = await response.json().catch(() => null);
          if (typeof problem?.detail === "string" && problem.detail.trim()) errorMessage = problem.detail;
          throw new Error(errorMessage);
        }
        const result = await response.json();
        if (typeof result?.url !== "string" || !result.url.startsWith("/video/")) throw new Error(errorMessage);
        if (!controller.signal.aborted) setPlayback({ filmId, attempt: playbackAttempt, url: result.url });
      } catch {
        if (!controller.signal.aborted) setPlayback({ filmId, attempt: playbackAttempt, error: errorMessage });
      }
    })();
    return () => controller.abort();
  }, [apiUrl, shot.film_id, playbackAttempt]);

  const handleCanPlay = useCallback(() => {
    const video = videoRef.current;
    if (!video || hasSeenCanPlay.current) return;
    hasSeenCanPlay.current = true;
    video.currentTime = seekTarget;
    video.play().catch(() => {
      // Autoplay may be blocked; native controls remain available.
    });
  }, [seekTarget]);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const focusFrame = window.requestAnimationFrame(() => {
      closeButtonRef.current?.focus();
    });

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;

      const dialog = dialogRef.current;
      if (!dialog) return;
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button:not([disabled]), video[controls], [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      ).filter((element) => !element.hasAttribute("hidden"));

      if (focusable.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      window.removeEventListener("keydown", handleKeyDown);
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
  }, []);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  const copyTimestamp = useCallback(() => {
    void navigator.clipboard
      .writeText(`${filmTitle} ${formatTime(videoRef.current?.currentTime ?? playheadTime)} (${(videoRef.current?.currentTime ?? playheadTime).toFixed(3)}s)`)
      .then(() => {
        setTimestampCopied(true);
        window.setTimeout(() => setTimestampCopied(false), 1600);
      })
      .catch(() => setTimestampCopied(false));
  }, [filmTitle, playheadTime]);

  return (
    <div
      className="modal-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="modal-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="modal-header">
          <span id={titleId} className="modal-title">{filmTitle}</span>
          <span className="modal-time">
            {typeof shot.matched_frame_timestamp === "number"
              ? `${formatTime(evidenceTime)} match`
              : `${formatTime(shot.t_start)} – ${formatTime(shot.t_end)}`}
          </span>
          <div className="modal-header-actions">
            <button ref={closeButtonRef} type="button" onClick={onClose} aria-label="Close">×</button>
          </div>
        </div>

        {currentPlayback?.error ? <div className="modal-evidence">
          <p role="alert">{currentPlayback.error}</p>
          <div className="modal-actions"><button type="button" onClick={() => setPlaybackAttempt((attempt) => attempt + 1)}>Retry playback</button></div>
        </div> : currentPlayback?.url ? <video
          key={`${shot.unit_id}:${evidenceTime}:${currentPlayback.url}`}
          ref={videoRef}
          src={`${apiUrl}${currentPlayback.url}`}
          controls
          playsInline
          onCanPlay={handleCanPlay}
          onError={() => setPlayback({ filmId: shot.film_id, attempt: playbackAttempt,
            error: "This video could not play. Retry playback, or check that the film is available." })}
          onTimeUpdate={(event) => setPlayheadTime(event.currentTarget.currentTime)}
          onSeeked={(event) => setPlayheadTime(event.currentTarget.currentTime)}
          style={{ width: "100%", display: "block", background: "#000" }}
        /> : <p className="modal-evidence" role="status">Loading player…</p>}

        <div className="modal-toolbar">
          <div className="modal-anchor-context">
            <span>Playing <strong>{formatTime(playheadTime)}</strong></span>
            <button type="button" onClick={() => {
              if (videoRef.current) videoRef.current.currentTime = evidenceTime;
            }}>Return to retrieved moment · {formatTime(evidenceTime)}</button>
          </div>
          <div className="modal-actions">
            {onToggleBookmark && (
              <button
                type="button"
                className={bookmarked ? "is-active" : undefined}
                disabled={bookmarkDisabled}
                onClick={() => onToggleBookmark(shot)}
                aria-label={bookmarked ? "Remove retrieved scene from Saved" : "Save retrieved scene"}
                aria-pressed={bookmarked}
                title={bookmarked ? "Remove from Saved" : `Save this scene at ${formatTime(evidenceTime)}`}
              >
                <BookmarkIcon filled={bookmarked} size={14} />
                {bookmarked ? "Saved" : "Save"}
              </button>
            )}
            {onUseInSearch && sourceReferenceFacet ? (
              <button
                type="button"
                className="modal-source-picker-use"
                disabled={!Number.isInteger(shot.keyframe_index)}
                onClick={() => onUseInSearch(shot, sourceReferenceFacet)}
              >
                <FacetIcon facet={sourceReferenceFacet} size={15} />
                Use for {FACET_LABELS[sourceReferenceFacet]}
              </button>
            ) : onUseInSearch ? (
              <UseInSearchMenu
                shot={shot}
                onUse={onUseInSearch}
                variant="modal"
                disabled={!Number.isInteger(shot.keyframe_index)}
                disabledFacets={disabledUseFacets}
              />
            ) : null}
            <a
              className="modal-action-link"
              href={matchCutsHref}
              target="_blank"
              rel="noopener"
              title={`Find shots that cut well from ${formatTime(matchTime)} (opens Match Cuts in a new tab)`}
            >
              Match cuts
            </a>
            <button type="button" onClick={copyTimestamp} title="Copy the film title and current time">
              {timestampCopied ? "Copied" : "Copy time"}
            </button>
          </div>
        </div>

        <div className="modal-evidence">
          {(shot.scene?.title || (shot.badges?.length ?? 0) > 0) && (
            <div className="modal-story-header">
              {shot.scene?.title && <strong>{shot.scene.title}</strong>}
              {shot.badges?.map((badge) => (
                <span key={badge} className={`result-badge result-badge-${badge}`}>{BADGE_LABELS[badge]}</span>
              ))}
            </div>
          )}
          {shot.scene?.summary && <p className="modal-scene-summary">{shot.scene.summary}</p>}
          {shot.action && (
            <p className="modal-story-action">
              {shot.action}
              {(shot.characters?.length ?? 0) > 0 && <span> — {shot.characters?.join(", ")}</span>}
            </p>
          )}
          {shot.famous_line && <p className="modal-famous-line">“{shot.famous_line}”</p>}
          {shot.caption && <p className="modal-caption">{shot.caption}</p>}
          {hasBreakdown && (
            <section className="modal-reasons" aria-label="Why this scene ranked here">
              <h3>Why it&apos;s here</h3>
              <MatchBreakdown shot={shot} />
            </section>
          )}
          {(shot.scene_alternatives?.length ?? 0) > 0 && (
            <div className="modal-scene-alternatives" aria-label="More matching shots from this scene">
              <span>More shots from this scene</span>
              <div>
                {shot.scene_alternatives?.map((alternative) => (
                  <button
                    key={alternative.unit_id}
                    type="button"
                    title={`Play from ${formatTime(alternative.t_start)}`}
                    onClick={() => {
                      if (videoRef.current) videoRef.current.currentTime = alternative.t_start;
                    }}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={`${apiUrl}${alternative.thumbnail_url ?? alternative.keyframe_url}`} alt="" loading="lazy" />
                    <span>{formatTime(alternative.t_start)}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
