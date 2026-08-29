"use client";

import { useEffect, useRef, useCallback, useId, useState } from "react";
import type { SearchResult } from "@/types/api";
import { formatTime, filmLabel } from "@/lib/format";

interface VideoModalProps {
  shot: SearchResult;
  onClose: () => void;
  onMatchComposition?: (shot: SearchResult) => void;
  matchCompositionDisabled?: boolean;
  bookmarked?: boolean;
  bookmarkDisabled?: boolean;
  onToggleBookmark?: (shot: SearchResult) => void;
}

const TEXT_VIEW_LABELS: Record<string, string> = {
  caption: "Visual description",
  dialogue: "Dialogue",
  ocr: "On-screen text",
  facets: "Scene detail",
};

export default function VideoModal({
  shot,
  onClose,
  onMatchComposition,
  matchCompositionDisabled = false,
  bookmarked = false,
  bookmarkDisabled = false,
  onToggleBookmark,
}: VideoModalProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const hasSeenCanPlay = useRef(false);
  const [timestampCopied, setTimestampCopied] = useState(false);
  const titleId = useId();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
  const evidenceTime = shot.matched_frame_timestamp ?? shot.t_start;
  const seekTarget = Math.max(0, evidenceTime - 1);
  const matchedTextLabel = shot.matched_text_view
    ? (TEXT_VIEW_LABELS[shot.matched_text_view] ?? "Text")
    : null;

  // Reset the one-shot guard whenever the shot changes
  useEffect(() => {
    hasSeenCanPlay.current = false;
  }, [shot]);

  const handleCanPlay = useCallback(() => {
    const vid = videoRef.current;
    if (!vid || hasSeenCanPlay.current) return;
    hasSeenCanPlay.current = true;
    vid.currentTime = seekTarget;
    vid.play().catch(() => {
      // autoplay blocked — user can press play
    });
  }, [seekTarget]);

  // close on Escape
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // prevent background scroll
  useEffect(() => {
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
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
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="modal-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        {/* header */}
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
            <button type="button" onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
        </div>

        {/* video */}
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
          {shot.caption &&
            !(
              shot.matched_text_view === "caption" &&
              shot.matched_text === shot.caption
            ) && <p>{shot.caption}</p>}
          <div className="modal-actions">
            <button type="button" onClick={copyTimestamp}>
              {timestampCopied ? "Timestamp copied" : "Copy timestamp"}
            </button>
            {onMatchComposition && (
              <button
                type="button"
                disabled={matchCompositionDisabled}
                onClick={() => onMatchComposition(shot)}
              >
                Match composition
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
