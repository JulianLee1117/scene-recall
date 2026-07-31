"use client";

import { useState, useRef, useCallback, useId } from "react";
import type { SearchResult } from "@/types/api";
import { formatTime, filmLabel } from "@/lib/format";

interface ShotCardProps {
  shot: SearchResult;
  position: number;
  debug: boolean;
  onClick: (shot: SearchResult) => void;
  onFindSimilar: (shot: SearchResult) => void;
  similarDisabled?: boolean;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
const CHANNEL_LABELS = {
  img: "img",
  txt: "txt",
  lex: "lex",
  spatial: "pos",
} as const;

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

export default function ShotCard({
  shot,
  position,
  debug,
  onClick,
  onFindSimilar,
  similarDisabled = false,
}: ShotCardProps) {
  const [hovered, setHovered] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);
  const debugDescriptionId = useId();
  const displayedRank = shot.rank ?? position;
  const evidenceTime = shot.matched_frame_timestamp ?? shot.t_start;

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

  const handleFindSimilar = useCallback(() => {
    handleMouseLeave();
    onFindSimilar(shot);
  }, [handleMouseLeave, onFindSimilar, shot]);

  return (
    <article
      className={`result-card${debug ? " result-card-debug" : ""}`}
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

          <span className="rank-badge" aria-hidden="true">
            {displayedRank}
          </span>

          <span
            className="result-card-overlay"
            style={{
              opacity: hovered ? 1 : 0,
            }}
          >
            <span className="result-film">{filmLabel(shot.film_id)}</span>
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

      <button
        type="button"
        className="find-similar-button"
        disabled={similarDisabled}
        onClick={handleFindSimilar}
        onFocus={handleMouseEnter}
        onBlur={handleMouseLeave}
        aria-label={`Find shots with framing similar to result ${displayedRank}`}
        title="Find similar framing"
      >
        <svg
          width="17"
          height="17"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <path d="M8 3H3v5" />
          <path d="M16 3h5v5" />
          <path d="M21 16v5h-5" />
          <path d="M8 21H3v-5" />
          <circle cx="12" cy="12" r="3" />
        </svg>
        <span>Similar</span>
      </button>
    </article>
  );
}
