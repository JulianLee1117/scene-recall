"use client";

import { useEffect, useRef, useCallback } from "react";
import type { SearchResult } from "@/types/api";

interface VideoModalProps {
  shot: SearchResult;
  onClose: () => void;
}

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) {
    return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  }
  return `${m}:${String(s).padStart(2, "0")}`;
}

function filmLabel(filmId: string): string {
  return filmId
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export default function VideoModal({ shot, onClose }: VideoModalProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "";
  const seekTarget = Math.max(0, shot.t_start - 1);

  const handleCanPlay = useCallback(() => {
    const vid = videoRef.current;
    if (!vid) return;
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
            {filmLabel(shot.film_id)}
          </span>
          <span style={{ color: "#6b6b6b", fontSize: "0.85rem" }}>
            {formatTime(shot.t_start)} – {formatTime(shot.t_end)}
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

        {/* caption */}
        {shot.caption && (
          <p
            style={{
              margin: 0,
              padding: "8px 14px 12px",
              color: "#8a8a8a",
              fontSize: "0.8rem",
              fontStyle: "italic",
              lineHeight: 1.5,
            }}
          >
            {shot.caption}
          </p>
        )}
      </div>
    </div>
  );
}
