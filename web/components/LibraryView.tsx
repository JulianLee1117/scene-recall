"use client";

import { useState, useEffect, useCallback, CSSProperties } from "react";
import type { LibraryFilm, IngestJob } from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatElapsed(startedAt: number, now: number): string {
  const secs = Math.max(0, Math.floor(now / 1000 - startedAt));
  const m = Math.floor(secs / 60);
  const s = secs % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function pillStyle(
  isIndexed: boolean,
  isIngesting: boolean,
  isError: boolean,
): CSSProperties {
  if (isIndexed)
    return {
      background: "rgba(34,197,94,0.12)",
      color: "#22c55e",
      border: "1px solid rgba(34,197,94,0.2)",
    };
  if (isIngesting)
    return {
      background: "rgba(217,119,6,0.12)",
      color: "#f59e0b",
      border: "1px solid rgba(217,119,6,0.2)",
    };
  if (isError)
    return {
      background: "rgba(239,68,68,0.12)",
      color: "#ef4444",
      border: "1px solid rgba(239,68,68,0.2)",
    };
  return {
    background: "#1a1a1a",
    color: "#555",
    border: "1px solid #2a2a2a",
  };
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function LibraryView() {
  const [films, setFilms] = useState<LibraryFilm[]>([]);
  const [jobs, setJobs] = useState<IngestJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [now, setNow] = useState(Date.now());

  const fetchLibrary = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/library`);
      if (res.ok) {
        const data = (await res.json()) as LibraryFilm[];
        setFilms(data);
      }
    } catch {
      // network error — keep stale data
    }
  }, []);

  const fetchJobs = useCallback(async () => {
    try {
      const res = await fetch(`${API_URL}/ingest/jobs`);
      if (res.ok) {
        const data = (await res.json()) as IngestJob[];
        setJobs(data);
      }
    } catch {
      // network error — keep stale data
    }
  }, []);

  useEffect(() => {
    void Promise.all([fetchLibrary(), fetchJobs()]).then(() =>
      setLoading(false),
    );

    const pollInterval = setInterval(() => {
      void Promise.all([fetchJobs(), fetchLibrary()]);
    }, 3000);

    const clockInterval = setInterval(() => setNow(Date.now()), 1000);

    return () => {
      clearInterval(pollInterval);
      clearInterval(clockInterval);
    };
  }, [fetchLibrary, fetchJobs]);

  const handleIngest = useCallback(
    async (film: LibraryFilm) => {
      // Optimistic update — show as ingesting immediately
      const optimistic: IngestJob = {
        job_id: "__optimistic__",
        path: film.path,
        filename: film.filename,
        status: "running",
        started_at: Date.now() / 1000,
        finished_at: null,
        error: null,
      };
      setJobs((prev) => [
        ...prev.filter((j) => j.path !== film.path),
        optimistic,
      ]);

      try {
        const res = await fetch(`${API_URL}/ingest`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: film.path }),
        });
        if (!res.ok) {
          // Remove optimistic entry — next poll will reflect real state
          setJobs((prev) =>
            prev.filter((j) => j.job_id !== "__optimistic__"),
          );
        }
        // On success, the next poll (within 3 s) replaces the optimistic entry
        // with the real job from the server.
      } catch {
        setJobs((prev) =>
          prev.filter((j) => j.job_id !== "__optimistic__"),
        );
      }
    },
    [],
  );

  // Build a lookup: path → latest job
  const jobsByPath = new Map<string, IngestJob>(
    jobs.map((j) => [j.path, j]),
  );

  const indexedCount = films.filter((f) => {
    const job = jobsByPath.get(f.path);
    return f.status === "indexed" && job?.status !== "running";
  }).length;

  // ---------------------------------------------------------------------------
  // Render states
  // ---------------------------------------------------------------------------

  if (loading) {
    return (
      <div
        style={{
          padding: "48px 24px",
          color: "#444",
          fontSize: "0.85rem",
        }}
      >
        Loading library…
      </div>
    );
  }

  if (films.length === 0) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "50vh",
          color: "#444",
          fontSize: "0.9rem",
          textAlign: "center",
          padding: "24px",
          lineHeight: 1.6,
        }}
      >
        No films found in your films directory.
        <br />
        Add .mkv or .mp4 files to get started.
      </div>
    );
  }

  return (
    <div
      style={{
        padding: "40px 24px",
        maxWidth: "880px",
        margin: "0 auto",
      }}
    >
      {/* Header */}
      <div style={{ marginBottom: "24px" }}>
        <h2
          style={{
            color: "#ededed",
            fontSize: "1.1rem",
            fontWeight: 600,
            margin: 0,
            letterSpacing: "0.01em",
          }}
        >
          Library
        </h2>
        <p
          style={{
            color: "#444",
            fontSize: "0.78rem",
            margin: "4px 0 0 0",
          }}
        >
          {films.length} film{films.length !== 1 ? "s" : ""} ·{" "}
          {indexedCount} indexed
        </p>
      </div>

      {/* Film list */}
      <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
        {films.map((film) => {
          const job = jobsByPath.get(film.path);
          const isIngesting = job?.status === "running";
          const isError = job?.status === "error";
          const isIndexed =
            !isIngesting && !isError && film.status === "indexed";
          const showIngest = !isIndexed && !isIngesting && !isError;

          return (
            <div
              key={film.path}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "12px",
                padding: "11px 16px",
                background: "#111",
                borderRadius: "6px",
                borderLeft: isIngesting
                  ? "2px solid #d97706"
                  : isError
                    ? "2px solid #ef4444"
                    : "2px solid transparent",
              }}
            >
              {/* Filename + meta */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontFamily: "monospace",
                    fontSize: "0.8rem",
                    color: "#ededed",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {film.filename}
                </div>
                <div
                  style={{
                    color: "#444",
                    fontSize: "0.7rem",
                    marginTop: "2px",
                  }}
                >
                  {film.size_gb} GB
                  {isIngesting && job != null && (
                    <span style={{ color: "#6b5a3a", marginLeft: "8px" }}>
                      {formatElapsed(job.started_at, now)}
                    </span>
                  )}
                  {isError && job?.error != null && (
                    <span
                      style={{
                        color: "#7f1d1d",
                        marginLeft: "8px",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        display: "inline-block",
                        maxWidth: "300px",
                        verticalAlign: "bottom",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {job.error.slice(0, 80)}
                    </span>
                  )}
                </div>
              </div>

              {/* Ingesting progress bar */}
              {isIngesting && (
                <div
                  style={{
                    width: "64px",
                    height: "3px",
                    background: "#1e1e1e",
                    borderRadius: "2px",
                    overflow: "hidden",
                    flexShrink: 0,
                  }}
                >
                  <div
                    style={{
                      height: "100%",
                      width: "35%",
                      background: "#d97706",
                      borderRadius: "2px",
                      animation: "sr-slide 1.6s ease-in-out infinite",
                    }}
                  />
                </div>
              )}

              {/* Status pill */}
              <div
                style={{
                  flexShrink: 0,
                  padding: "3px 10px",
                  borderRadius: "20px",
                  fontSize: "0.68rem",
                  fontWeight: 500,
                  letterSpacing: "0.02em",
                  whiteSpace: "nowrap",
                  ...pillStyle(isIndexed, isIngesting, isError),
                }}
              >
                {isIndexed
                  ? "Indexed"
                  : isIngesting
                    ? "Ingesting…"
                    : isError
                      ? "Error"
                      : "Not indexed"}
              </div>

              {/* Ingest button */}
              {showIngest && (
                <button
                  onClick={() => void handleIngest(film)}
                  style={{
                    flexShrink: 0,
                    padding: "4px 14px",
                    background: "transparent",
                    border: "1px solid #2e2e2e",
                    borderRadius: "4px",
                    color: "#666",
                    fontSize: "0.73rem",
                    cursor: "pointer",
                    transition: "border-color 0.12s, color 0.12s",
                  }}
                  onMouseEnter={(e) => {
                    e.currentTarget.style.borderColor = "#555";
                    e.currentTarget.style.color = "#bbb";
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.borderColor = "#2e2e2e";
                    e.currentTarget.style.color = "#666";
                  }}
                >
                  Ingest
                </button>
              )}
            </div>
          );
        })}
      </div>

      {/* Keyframe animation for progress bar */}
      <style>{`
        @keyframes sr-slide {
          0%   { transform: translateX(-100%); }
          100% { transform: translateX(380%); }
        }
      `}</style>
    </div>
  );
}
