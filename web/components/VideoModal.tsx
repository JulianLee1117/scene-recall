"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import UseInSearchMenu from "./UseInSearchMenu";
import FacetIcon from "./FacetIcon";
import { filmLabel, formatTime } from "@/lib/format";
import { FACET_LABELS } from "@/lib/searchRecipe";
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

const TEXT_VIEW_LABELS: Record<string, string> = {
  caption: "Visual description",
  dialogue: "Dialogue",
  ocr: "On-screen text",
  facets: "Scene detail",
  mood: "Mood",
  story: "Story",
  scene: "Scene",
};
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
  const matchedTextLabel = shot.matched_text_view
    ? (TEXT_VIEW_LABELS[shot.matched_text_view] ?? "Text")
    : null;
  const matchedFacetLabels = Array.from(
    new Set((shot.matches ?? []).map((match) => FACET_LABELS[match.facet])),
  );

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
      .writeText(`${formatTime(videoRef.current?.currentTime ?? playheadTime)} (${(videoRef.current?.currentTime ?? playheadTime).toFixed(3)}s)`)
      .then(() => {
        setTimestampCopied(true);
        window.setTimeout(() => setTimestampCopied(false), 1600);
      })
      .catch(() => setTimestampCopied(false));
  }, [playheadTime]);

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
          <span id={titleId} className="modal-title">
            {shot.film_title ?? filmLabel(shot.film_id)}
          </span>
          <span className="modal-time">
            {typeof shot.matched_frame_timestamp === "number"
              ? `${formatTime(evidenceTime)} match`
              : `${formatTime(shot.t_start)} – ${formatTime(shot.t_end)}`}
          </span>
          <div className="modal-header-actions">
            {onToggleBookmark && (
              <button
                type="button"
                className={bookmarked ? "is-active" : undefined}
                disabled={bookmarkDisabled}
                onClick={() => onToggleBookmark(shot)}
                aria-label={bookmarked ? "Remove retrieved scene from Saved" : "Save retrieved scene"}
                aria-pressed={bookmarked}
                title={bookmarked ? "Remove from Saved" : `Save retrieved evidence at ${formatTime(evidenceTime)}`}
              >
                <svg
                  width="17"
                  height="17"
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
            <button
              ref={closeButtonRef}
              type="button"
              onClick={onClose}
              aria-label="Close"
            >
              ×
            </button>
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
          onCanPlay={handleCanPlay}
          onError={() => setPlayback({ filmId: shot.film_id, attempt: playbackAttempt,
            error: "This video could not play. Retry playback, or check that the film is available." })}
          onTimeUpdate={(event) => setPlayheadTime(event.currentTarget.currentTime)}
          onSeeked={(event) => setPlayheadTime(event.currentTarget.currentTime)}
          style={{ width: "100%", display: "block", background: "#000" }}
        /> : <p className="modal-evidence" role="status">Loading player…</p>}

        <div className="modal-evidence">
          <div className="modal-anchor-context">
            <span>Playing <strong>{formatTime(playheadTime)}</strong></span>
            <button type="button" onClick={() => {
              if (videoRef.current) videoRef.current.currentTime = evidenceTime;
            }}>Return to retrieved moment · {formatTime(evidenceTime)}</button>
          </div>
          {(onUseInSearch || onToggleBookmark) && (
            <p className="modal-anchor-note">Save and Find related use the retrieved scene and reference frame at {formatTime(evidenceTime)}. Scrubbing changes playback.</p>
          )}
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
          {shot.matched_line && (
            <div className="modal-match-evidence">
              <span>Line at {formatTime(shot.matched_line.t_start)}</span>
              <span>{shot.matched_line.text}</span>
            </div>
          )}
          {(shot.scene_alternatives?.length ?? 0) > 0 && (
            <div className="modal-scene-alternatives" aria-label="More matching shots in this scene">
              <span>More in this scene</span>
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
          {matchedTextLabel && shot.matched_text && !shot.matched_line && (
            <div className="modal-match-evidence">
              <span>{matchedTextLabel} match</span>
              <span>{shot.matched_text}</span>
            </div>
          )}
          {matchedFacetLabels.length > 0 && (
            <div
              className="modal-match-facets"
              aria-label={`Matched by ${matchedFacetLabels.join(", ")}`}
            >
              {matchedFacetLabels.map((label) => (
                <span key={label}>{label}</span>
              ))}
            </div>
          )}
          {shot.caption &&
            !(
              shot.matched_text_view === "caption" &&
              shot.matched_text === shot.caption
            ) && <p>{shot.caption}</p>}
          <div className="modal-actions">
            <button type="button" onClick={copyTimestamp}>
              {timestampCopied ? "Timestamp copied" : "Copy current timestamp"}
            </button>
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
          </div>
        </div>
      </div>
    </div>
  );
}
