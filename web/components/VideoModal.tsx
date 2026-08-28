"use client";

import { useEffect, useRef, useCallback, useState } from "react";
import type { SearchResult } from "@/types/api";
import { formatTime, filmLabel } from "@/lib/format";

interface VideoModalProps {
  shot: SearchResult;
  onClose: () => void;
  onMatchComposition?: (shot: SearchResult) => void;
  matchCompositionDisabled?: boolean;
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
}: VideoModalProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const hasSeenCanPlay = useRef(false);
  const [timestampCopied, setTimestampCopied] = useState(false);
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
        style={{
          position: "relative",
          width: "min(90vw, 1200px)",
          background: "#0a0a0a",
        }}
      >
        {/* header */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            padding: "10px 14px",
            borderBottom: "1px solid #222",
          }}
        >
          <span style={{ color: "#d4a96a", fontWeight: 600, fontSize: "0.9rem" }}>
            {shot.film_title ?? filmLabel(shot.film_id)}
          </span>
          <span style={{ color: "#6b6b6b", fontSize: "0.85rem" }}>
            {typeof shot.matched_frame_timestamp === "number"
              ? `${formatTime(evidenceTime)} match`
              : `${formatTime(shot.t_start)} – ${formatTime(shot.t_end)}`}
          </span>
          <button
            onClick={onClose}
            aria-label="Close"
            style={{
              background: "none",
              border: "none",
              color: "#6b6b6b",
              cursor: "pointer",
              fontSize: "1.3rem",
              lineHeight: 1,
              padding: "2px 6px",
            }}
          >
            ✕
          </button>
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
