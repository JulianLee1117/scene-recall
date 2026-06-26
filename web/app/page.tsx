"use client";

import { useState, useCallback, useRef } from "react";
import ResultGrid from "@/components/ResultGrid";
import VideoModal from "@/components/VideoModal";
import type { SearchResult, SearchResponse } from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

export default function Home() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeShot, setActiveShot] = useState<SearchResult | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const runSearch = useCallback(async (q: string) => {
    const trimmed = q.trim();
    if (!trimmed) return;

    setLoading(true);
    setError(null);

    try {
      const res = await fetch(
        `${API_URL}/search?q=${encodeURIComponent(trimmed)}`
      );
      if (!res.ok) throw new Error(`API error ${res.status}`);
      const data: SearchResponse = await res.json();
      setResults(data.results);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
      setResults([]);
    } finally {
      setLoading(false);
    }
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    runSearch(query);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      e.preventDefault();
      runSearch(query);
    }
  };

  const isEmpty = results.length === 0 && !loading && !error;

  return (
    <main
      style={{
        minHeight: "100vh",
        background: "#0a0a0a",
        color: "#ededed",
      }}
    >
      {/* Hero search area */}
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: isEmpty ? "center" : "flex-start",
          minHeight: isEmpty ? "100vh" : "auto",
          paddingTop: isEmpty ? 0 : "40px",
          paddingBottom: "32px",
          transition: "min-height 0.3s ease",
        }}
      >
        {/* wordmark */}
        <div
          style={{
            marginBottom: "28px",
            letterSpacing: "0.2em",
            fontSize: isEmpty ? "1.1rem" : "0.85rem",
            color: "#d4a96a",
            fontWeight: 500,
            textTransform: "uppercase",
            transition: "font-size 0.3s ease",
          }}
        >
          scene-recall
        </div>

        {/* search bar */}
        <form
          onSubmit={handleSubmit}
          style={{
            width: "100%",
            maxWidth: isEmpty ? "640px" : "520px",
            padding: "0 16px",
            transition: "max-width 0.3s ease",
          }}
        >
          <div
            style={{
              position: "relative",
              display: "flex",
              alignItems: "center",
            }}
          >
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="describe a scene…"
              autoFocus
              style={{
                width: "100%",
                background: "#141414",
                border: "1px solid #2a2a2a",
                borderRadius: "6px",
                color: "#ededed",
                fontSize: isEmpty ? "1.25rem" : "1rem",
                padding: isEmpty ? "18px 52px 18px 20px" : "13px 44px 13px 16px",
                outline: "none",
                transition: "font-size 0.3s ease, padding 0.3s ease, border-color 0.15s ease",
                caretColor: "#d4a96a",
              }}
              onFocus={(e) => {
                e.currentTarget.style.borderColor = "#3a3a3a";
              }}
              onBlur={(e) => {
                e.currentTarget.style.borderColor = "#2a2a2a";
              }}
            />
            {/* search button */}
            <button
              type="submit"
              disabled={loading}
              aria-label="Search"
              style={{
                position: "absolute",
                right: isEmpty ? "14px" : "10px",
                background: "none",
                border: "none",
                cursor: loading ? "default" : "pointer",
                color: loading ? "#444" : "#d4a96a",
                padding: "4px",
                display: "flex",
                alignItems: "center",
                transition: "color 0.15s ease",
              }}
            >
              {loading ? (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <circle cx="12" cy="12" r="10" strokeOpacity="0.3" />
                  <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round">
                    <animateTransform attributeName="transform" type="rotate" from="0 12 12" to="360 12 12" dur="0.8s" repeatCount="indefinite" />
                  </path>
                </svg>
              ) : (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="11" cy="11" r="8" />
                  <line x1="21" y1="21" x2="16.65" y2="16.65" />
                </svg>
              )}
            </button>
          </div>
        </form>

        {/* error */}
        {error && (
          <p
            style={{
              marginTop: "16px",
              color: "#c0392b",
              fontSize: "0.85rem",
            }}
          >
            {error}
          </p>
        )}

        {/* no results */}
        {!loading && !error && results.length === 0 && query && (
          <p
            style={{
              marginTop: "24px",
              color: "#555",
              fontSize: "0.9rem",
            }}
          >
            No results found.
          </p>
        )}
      </div>

      {/* results grid */}
      <ResultGrid results={results} onShotClick={setActiveShot} />

      {/* video modal */}
      {activeShot && (
        <VideoModal
          shot={activeShot}
          onClose={() => setActiveShot(null)}
        />
      )}
    </main>
  );
}
