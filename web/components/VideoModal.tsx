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
};

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
  const evidenceTime = shot.matched_frame_timestamp ?? shot.t_start;
  const seekTarget = Math.max(0, evidenceTime - 1);
  const matchedTextLabel = shot.matched_text_view
    ? (TEXT_VIEW_LABELS[shot.matched_text_view] ?? "Text")
    : null;
  const matchedFacetLabels = Array.from(
    new Set((shot.matches ?? []).map((match) => FACET_LABELS[match.facet])),
  );

  useEffect(() => {
    hasSeenCanPlay.current = false;
  }, [shot]);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

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
      .writeText(formatTime(evidenceTime))
      .then(() => {
        setTimestampCopied(true);
        window.setTimeout(() => setTimestampCopied(false), 1600);
      })
      .catch(() => setTimestampCopied(false));
  }, [evidenceTime]);

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
                aria-label={bookmarked ? "Remove scene from Saved" : "Save scene"}
                aria-pressed={bookmarked}
                title={bookmarked ? "Remove from Saved" : "Save this scene"}
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

        <video
          ref={videoRef}
          src={`${apiUrl}/video/${shot.film_id}`}
          controls
          onCanPlay={handleCanPlay}
          style={{ width: "100%", display: "block", background: "#000" }}
        />

        <div className="modal-evidence">
          {matchedTextLabel && shot.matched_text && (
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
              {timestampCopied ? "Timestamp copied" : "Copy timestamp"}
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
