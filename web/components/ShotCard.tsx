"use client";

import { useState, useRef, useCallback } from "react";
import type { SearchResult } from "@/types/api";
import { formatTime, filmLabel } from "@/lib/format";

interface ShotCardProps {
  shot: SearchResult;
  onClick: (shot: SearchResult) => void;
}

export default function ShotCard({ shot, onClick }: ShotCardProps) {
  const [hovered, setHovered] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

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

  return (
    <div
      className="masonry-item"
      onClick={() => onClick(shot)}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      title={shot.caption}
    >
      {/* keyframe — always in flow to hold card height */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={shot.keyframe_url}
        alt={shot.caption}
        loading="lazy"
        style={{
          display: "block",
          width: "100%",
          height: "auto",
          opacity: hovered ? 0 : 1,
          transition: "opacity 0.15s ease",
        }}
      />

      {/* preview video — always absolutely positioned over the img */}
      <video
        ref={videoRef}
        src={shot.preview_url}
        muted
        loop
        playsInline
        preload="none"
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          width: "100%",
          height: "100%",
          opacity: hovered ? 1 : 0,
          transition: "opacity 0.15s ease",
          objectFit: "cover",
        }}
      />

      {/* overlay: film title + timestamp */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background:
            "linear-gradient(to top, rgba(0,0,0,0.75) 0%, transparent 50%)",
          opacity: hovered ? 1 : 0,
          transition: "opacity 0.15s ease",
          display: "flex",
          flexDirection: "column",
          justifyContent: "flex-end",
          padding: "8px 10px",
          pointerEvents: "none",
        }}
      >
        <span
          style={{
            color: "#d4a96a",
            fontSize: "0.7rem",
            fontWeight: 600,
            letterSpacing: "0.04em",
            textTransform: "uppercase",
            lineHeight: 1.3,
          }}
        >
          {filmLabel(shot.film_id)}
        </span>
        <span
          style={{
            color: "#bbb",
            fontSize: "0.65rem",
            marginTop: 2,
          }}
        >
          {formatTime(shot.t_start)}
        </span>
      </div>
    </div>
  );
}
