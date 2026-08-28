"use client";

import { useState, useCallback, useEffect, useId, useRef } from "react";
import ResultGrid from "@/components/ResultGrid";
import VideoModal from "@/components/VideoModal";
import LibraryView from "@/components/LibraryView";
import MovieScopeFilter from "@/components/MovieScopeFilter";
import { useSpeechRecognition } from "@/hooks/useSpeechRecognition";
import type { SearchResult, SearchResponse } from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
const MOVIE_SCOPE_SEARCH_DEBOUNCE_MS = 350;
const DEFAULT_RESULT_BATCH_SIZE = 12;

function displayBatchSize(response: SearchResponse): number {
  const size = response.display_batch_size;
  return typeof size === "number" && Number.isSafeInteger(size) && size > 0
    ? size
    : DEFAULT_RESULT_BATCH_SIZE;
}

type Tab = "search" | "library";

export default function Home() {
  const [activeTab, setActiveTab] = useState<Tab>("search");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [resultBatchSize, setResultBatchSize] = useState(
    DEFAULT_RESULT_BATCH_SIZE,
  );
  const [visibleResultCount, setVisibleResultCount] =
    useState(DEFAULT_RESULT_BATCH_SIZE);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [completedQuery, setCompletedQuery] = useState<string | null>(null);
  const [referenceLabel, setReferenceLabel] = useState<string | null>(null);
  const [referencePreviewUrl, setReferencePreviewUrl] = useState<string | null>(
    null,
  );
  const [activeShot, setActiveShot] = useState<SearchResult | null>(null);
  const [debug, setDebug] = useState(false);
  const [selectedFilmIds, setSelectedFilmIds] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const searchAbortRef = useRef<AbortController | null>(null);
  const scopeSearchTimerRef = useRef<number | null>(null);
  const referenceBlobRef = useRef<Blob | null>(null);
  const referenceExcludeRef = useRef<string | null>(null);
  const referenceLabelRef = useRef<string | null>(null);
  const referencePreviewUrlRef = useRef<string | null>(null);
  const voiceStatusId = useId();

  const cancelPendingScopeSearch = useCallback(() => {
    if (scopeSearchTimerRef.current === null) return;
    window.clearTimeout(scopeSearchTimerRef.current);
    scopeSearchTimerRef.current = null;
  }, []);

  const clearReference = useCallback(() => {
    cancelPendingScopeSearch();
    searchAbortRef.current?.abort();
    if (referencePreviewUrlRef.current) {
      URL.revokeObjectURL(referencePreviewUrlRef.current);
    }
    referenceBlobRef.current = null;
    referenceExcludeRef.current = null;
    referenceLabelRef.current = null;
    referencePreviewUrlRef.current = null;
    setReferenceLabel(null);
    setReferencePreviewUrl(null);
  }, [cancelPendingScopeSearch]);

  const activateReference = useCallback(
    (image: Blob, label: string, excludeUnitId: string | null = null) => {
      if (referencePreviewUrlRef.current) {
        URL.revokeObjectURL(referencePreviewUrlRef.current);
      }
      const previewUrl = URL.createObjectURL(image);
      referenceBlobRef.current = image;
      referenceExcludeRef.current = excludeUnitId;
      referenceLabelRef.current = label;
      referencePreviewUrlRef.current = previewUrl;
      setReferenceLabel(label);
      setReferencePreviewUrl(previewUrl);
      setCompletedQuery(null);
      setActiveShot(null);
    },
    [],
  );

  const runSearch = useCallback(
    async (q: string, scope: readonly string[] = selectedFilmIds) => {
      const trimmed = q.trim();
      if (!trimmed) return;

      cancelPendingScopeSearch();
      searchAbortRef.current?.abort();
      const controller = new AbortController();
      searchAbortRef.current = controller;
      setLoading(true);
      setError(null);
      setCompletedQuery(null);
      setVisibleResultCount(resultBatchSize);

      const params = new URLSearchParams({ q: trimmed });
      scope.forEach((filmId) => params.append("film_id", filmId));

      try {
        const res = await fetch(`${API_URL}/search?${params.toString()}`, {
          signal: controller.signal,
        });
        if (!res.ok) {
          let message = `API error ${res.status}`;
          try {
            const body = (await res.json()) as { detail?: string };
            if (typeof body.detail === "string" && body.detail) {
              message = body.detail;
            }
          } catch {
            // Keep the status-based fallback for non-JSON errors.
          }
          throw new Error(message);
        }
        const data: SearchResponse = await res.json();
        if (searchAbortRef.current !== controller) return;
        const nextBatchSize = displayBatchSize(data);
        setResultBatchSize(nextBatchSize);
        setVisibleResultCount(nextBatchSize);
        setResults(data.results);
        setCompletedQuery(trimmed);
      } catch (err) {
        if (controller.signal.aborted) return;
        setError(err instanceof Error ? err.message : "Search failed");
        setResults([]);
      } finally {
        if (searchAbortRef.current === controller) {
          searchAbortRef.current = null;
          setLoading(false);
        }
      }
    },
    [cancelPendingScopeSearch, resultBatchSize, selectedFilmIds],
  );

  const runImageSearch = useCallback(
    async (
      image: Blob,
      label: string,
      excludeUnitId: string | null = null,
      scope: readonly string[] = selectedFilmIds,
      textQuery: string = "",
    ) => {
      cancelPendingScopeSearch();
      searchAbortRef.current?.abort();
      const controller = new AbortController();
      searchAbortRef.current = controller;
      setLoading(true);
      setError(null);
      setCompletedQuery(null);
      setVisibleResultCount(resultBatchSize);

      const params = new URLSearchParams();
      scope.forEach((filmId) => params.append("film_id", filmId));
      if (excludeUnitId) {
        params.set("exclude_unit_id", excludeUnitId);
      }
      const trimmedTextQuery = textQuery.trim();
      if (trimmedTextQuery) {
        params.set("q", trimmedTextQuery);
      }
      const suffix = params.size ? `?${params.toString()}` : "";

      try {
        const res = await fetch(`${API_URL}/search/image${suffix}`, {
          method: "POST",
          body: image,
          headers: {
            "Content-Type": image.type || "application/octet-stream",
          },
          signal: controller.signal,
        });
        if (!res.ok) {
          let message = `API error ${res.status}`;
          try {
            const detail = (await res.json()) as { detail?: string };
            if (detail.detail) message = detail.detail;
          } catch {
            // Keep the status-based fallback for non-JSON errors.
          }
          throw new Error(message);
        }
        const data: SearchResponse = await res.json();
        if (searchAbortRef.current !== controller) return;
        const nextBatchSize = displayBatchSize(data);
        setResultBatchSize(nextBatchSize);
        setVisibleResultCount(nextBatchSize);
        setResults(data.results);
        setReferenceLabel(label);
        setCompletedQuery(trimmedTextQuery || null);
      } catch (err) {
        if (controller.signal.aborted) return;
        setError(
          err instanceof Error ? err.message : "Reference search failed",
        );
        setResults([]);
      } finally {
        if (searchAbortRef.current === controller) {
          searchAbortRef.current = null;
          setLoading(false);
        }
      }
    },
    [cancelPendingScopeSearch, resultBatchSize, selectedFilmIds],
  );

  const handleMovieScopeChange = useCallback(
    (filmIds: string[]) => {
      cancelPendingScopeSearch();
      searchAbortRef.current?.abort();
      searchAbortRef.current = null;
      setLoading(false);
      setSelectedFilmIds(filmIds);
      setActiveShot(null);

      const hasReference = Boolean(
        referenceBlobRef.current && referenceLabelRef.current,
      );
      const pendingQuery = query.trim();
      if (!hasReference && !pendingQuery) return;
      setVisibleResultCount(resultBatchSize);

      scopeSearchTimerRef.current = window.setTimeout(() => {
        scopeSearchTimerRef.current = null;
        if (referenceBlobRef.current && referenceLabelRef.current) {
          void runImageSearch(
            referenceBlobRef.current,
            referenceLabelRef.current,
            referenceExcludeRef.current,
            filmIds,
            pendingQuery,
          );
        } else if (pendingQuery) {
          void runSearch(pendingQuery, filmIds);
        }
      }, MOVIE_SCOPE_SEARCH_DEBOUNCE_MS);
    },
    [
      cancelPendingScopeSearch,
      query,
      resultBatchSize,
      runImageSearch,
      runSearch,
    ],
  );

  const handleVoiceTranscript = useCallback(
    (transcript: string) => {
      cancelPendingScopeSearch();
      searchAbortRef.current?.abort();
      searchAbortRef.current = null;
      setLoading(false);
      setQuery(transcript);
    },
    [cancelPendingScopeSearch],
  );

  const handleVoiceComplete = useCallback(
    (transcript: string) => {
      setQuery(transcript);
      inputRef.current?.focus();
      if (referenceBlobRef.current && referenceLabelRef.current) {
        void runImageSearch(
          referenceBlobRef.current,
          referenceLabelRef.current,
          referenceExcludeRef.current,
          selectedFilmIds,
          transcript,
        );
      } else {
        void runSearch(transcript);
      }
    },
    [runImageSearch, runSearch, selectedFilmIds],
  );

  const speech = useSpeechRecognition({
    onTranscript: handleVoiceTranscript,
    onComplete: handleVoiceComplete,
  });

  const handleReferenceFile = useCallback(
    (file: File) => {
      speech.cancel();
      activateReference(file, file.name || "Uploaded frame");
      void runImageSearch(
        file,
        file.name || "Uploaded frame",
        null,
        selectedFilmIds,
        query,
      );
    },
    [activateReference, query, runImageSearch, selectedFilmIds, speech],
  );

  const handleFindSimilar = useCallback(
    async (shot: SearchResult) => {
      if (loading) return;
      cancelPendingScopeSearch();
      speech.cancel();
      searchAbortRef.current?.abort();
      const controller = new AbortController();
      searchAbortRef.current = controller;
      setLoading(true);
      setError(null);
      setActiveShot(null);
      setVisibleResultCount(resultBatchSize);
      const referenceUrl =
        shot.matched_frame_url ?? shot.keyframe_url;
      try {
        // The same URL was already loaded by <img> in no-CORS mode. Avoid
        // reusing that opaque browser cache entry for this CORS fetch.
        const response = await fetch(`${API_URL}${referenceUrl}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`Could not load reference frame (${response.status})`);
        }
        const image = await response.blob();
        if (searchAbortRef.current !== controller) return;
        const label = `Result ${shot.rank ?? ""} frame`.trim();
        activateReference(image, label, shot.unit_id);
        await runImageSearch(
          image,
          label,
          shot.unit_id,
          selectedFilmIds,
          query,
        );
      } catch (err) {
        if (controller.signal.aborted) return;
        setError(
          err instanceof Error ? err.message : "Reference search failed",
        );
      } finally {
        if (searchAbortRef.current === controller) {
          searchAbortRef.current = null;
          setLoading(false);
        }
      }
    },
    [
      activateReference,
      cancelPendingScopeSearch,
      loading,
      query,
      resultBatchSize,
      runImageSearch,
      selectedFilmIds,
      speech,
    ],
  );

  const handleClearReference = useCallback(() => {
    searchAbortRef.current?.abort();
    clearReference();
    setActiveShot(null);
    const remainingQuery = query.trim();
    if (remainingQuery) {
      void runSearch(remainingQuery);
    } else {
      setResults([]);
      setResultBatchSize(DEFAULT_RESULT_BATCH_SIZE);
      setVisibleResultCount(DEFAULT_RESULT_BATCH_SIZE);
      setError(null);
      setCompletedQuery(null);
      setLoading(false);
    }
    inputRef.current?.focus();
  }, [clearReference, query, runSearch]);

  useEffect(
    () => () => {
      cancelPendingScopeSearch();
      searchAbortRef.current?.abort();
      if (referencePreviewUrlRef.current) {
        URL.revokeObjectURL(referencePreviewUrlRef.current);
      }
    },
    [cancelPendingScopeSearch],
  );

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const submittedQuery = query.trim();
    speech.cancel();
    if (referenceBlobRef.current && referenceLabelRef.current) {
      void runImageSearch(
        referenceBlobRef.current,
        referenceLabelRef.current,
        referenceExcludeRef.current,
        selectedFilmIds,
        submittedQuery,
      );
    } else if (submittedQuery) {
      void runSearch(submittedQuery);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Escape" && speech.status !== "idle") {
      e.preventDefault();
      speech.cancel();
      inputRef.current?.focus();
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      const submittedQuery = query.trim();
      speech.cancel();
      if (referenceBlobRef.current && referenceLabelRef.current) {
        void runImageSearch(
          referenceBlobRef.current,
          referenceLabelRef.current,
          referenceExcludeRef.current,
          selectedFilmIds,
          submittedQuery,
        );
      } else if (submittedQuery) {
        void runSearch(submittedQuery);
      }
    }
  };

  const isEmpty = results.length === 0 && !loading && !error;
  const voiceActive = speech.status !== "idle";
  const voiceStatus =
    speech.status === "requesting"
      ? "Starting microphone…"
      : speech.status === "listening"
        ? "Listening… say what scene you remember"
        : speech.status === "processing"
          ? "Finishing transcription…"
          : speech.error;

  return (
    <main
      style={{
        minHeight: "100vh",
        background: "#0a0a0a",
        color: "#ededed",
      }}
    >
      {/* Tab bar */}
      <div
        style={{
          position: "sticky",
          top: 0,
          zIndex: 10,
          background: "#0a0a0a",
          borderBottom: "1px solid #1a1a1a",
          display: "flex",
          alignItems: "stretch",
          padding: "0 20px",
        }}
      >
        {(["search", "library"] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => {
              if (tab !== "search") speech.cancel();
              setActiveTab(tab);
            }}
            style={{
              background: "none",
              border: "none",
              borderBottom:
                activeTab === tab
                  ? "2px solid #d4a96a"
                  : "2px solid transparent",
              color: activeTab === tab ? "#ededed" : "#555",
              cursor: "pointer",
              fontSize: "0.82rem",
              fontWeight: activeTab === tab ? 500 : 400,
              letterSpacing: "0.04em",
              marginBottom: "-1px",
              padding: "13px 14px",
              textTransform: "capitalize",
              transition: "color 0.15s",
            }}
            onMouseEnter={(e) => {
              if (activeTab !== tab)
                e.currentTarget.style.color = "#888";
            }}
            onMouseLeave={(e) => {
              if (activeTab !== tab)
                e.currentTarget.style.color = "#555";
            }}
          >
            {tab === "library" ? "Films" : "Search"}
          </button>
        ))}
      </div>

      {/* Library view */}
      {activeTab === "library" && <LibraryView />}

      {/* Search view */}
      {activeTab === "search" && (
        <>
          {/* Hero search area */}
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: isEmpty ? "center" : "flex-start",
              minHeight: isEmpty ? "calc(100vh - 45px)" : "auto",
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
                  onChange={(e) => {
                    speech.cancel();
                    speech.clearError();
                    cancelPendingScopeSearch();
                    searchAbortRef.current?.abort();
                    searchAbortRef.current = null;
                    setLoading(false);
                    setQuery(e.target.value);
                  }}
                  onKeyDown={handleKeyDown}
                  placeholder={
                    referenceLabel
                      ? "add a text constraint…"
                      : "describe a scene…"
                  }
                  aria-label="Describe a scene"
                  aria-describedby={voiceStatus ? voiceStatusId : undefined}
                  autoFocus
                  style={{
                    width: "100%",
                    background: "#141414",
                    border: "1px solid #2a2a2a",
                    borderRadius: "6px",
                    color: "#ededed",
                    fontSize: isEmpty ? "1.25rem" : "1rem",
                    padding: speech.isSupported
                      ? isEmpty
                        ? "18px 126px 18px 20px"
                        : "13px 116px 13px 16px"
                      : isEmpty
                        ? "18px 88px 18px 20px"
                        : "13px 78px 13px 16px",
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
                <input
                  ref={imageInputRef}
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  hidden
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    event.target.value = "";
                    if (file) handleReferenceFile(file);
                  }}
                />
                <button
                  type="button"
                  className="image-search-button"
                  disabled={loading}
                  aria-label="Choose a composition reference"
                  title="Choose a composition reference"
                  onClick={() => {
                    speech.cancel();
                    imageInputRef.current?.click();
                  }}
                  style={{
                    right: speech.isSupported
                      ? isEmpty
                        ? "78px"
                        : "70px"
                      : isEmpty
                        ? "46px"
                        : "40px",
                  }}
                >
                  <svg
                    width="18"
                    height="18"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.7"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <rect x="3" y="4" width="18" height="16" rx="2" />
                    <circle cx="8.5" cy="9" r="1.5" />
                    <path d="m4 17 4.5-4.5 3 3 2-2 6.5 6.5" />
                  </svg>
                </button>
                {speech.isSupported && (
                  <button
                    type="button"
                    className="voice-search-button"
                    data-state={speech.status}
                    disabled={speech.status === "processing"}
                    aria-label={
                      speech.status === "requesting"
                        ? "Cancel voice search"
                        : speech.status === "listening"
                          ? "Stop listening and search"
                          : speech.status === "processing"
                            ? "Finishing voice search"
                            : "Start voice search"
                    }
                    aria-pressed={voiceActive}
                    title={
                      speech.status === "requesting"
                        ? "Cancel voice search"
                        : speech.status === "listening"
                          ? "Stop listening"
                          : speech.status === "processing"
                            ? "Finishing voice search"
                            : "Search by voice"
                    }
                    onClick={() => speech.toggle(query)}
                    style={{
                      right: isEmpty ? "46px" : "40px",
                    }}
                  >
                    <svg
                      width="18"
                      height="18"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.8"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      aria-hidden="true"
                      focusable="false"
                    >
                      <rect x="9" y="2" width="6" height="12" rx="3" />
                      <path d="M5 10a7 7 0 0 0 14 0" />
                      <path d="M12 17v4" />
                      <path d="M8 21h8" />
                    </svg>
                  </button>
                )}
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

              <div className="search-meta">
                <span
                  id={voiceStatusId}
                  className={`voice-search-status${
                    speech.error ? " voice-search-status-error" : ""
                  }`}
                  role="status"
                  aria-live="polite"
                  aria-atomic="true"
                >
                  {speech.status === "listening" && (
                    <span className="voice-search-status-dot" aria-hidden="true" />
                  )}
                  {voiceStatus}
                </span>
                <div className="search-controls">
                  <MovieScopeFilter
                    selectedFilmIds={selectedFilmIds}
                    onChange={handleMovieScopeChange}
                  />
                  <button
                    type="button"
                    className="debug-toggle"
                    aria-pressed={debug}
                    onClick={() => setDebug((enabled) => !enabled)}
                  >
                    <span className="debug-toggle-dot" aria-hidden="true" />
                    Debug
                  </button>
                </div>
              </div>

              {referencePreviewUrl && referenceLabel && (
                <div className="reference-query-chip" role="status">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={referencePreviewUrl} alt="" />
                  <span className="reference-query-copy">
                    <span>{referenceLabel}</span>
                    <span>
                      {query.trim()
                        ? "Composition reference + text constraint"
                        : "Composition reference"}
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={handleClearReference}
                    aria-label="Clear reference image"
                    title="Clear reference"
                  >
                    ×
                  </button>
                </div>
              )}

              {isEmpty && !query.trim() && !referenceLabel && (
                <div className="search-examples" aria-label="Example searches">
                  <span>Try</span>
                  {["a lonely figure under red neon", '"I remember everything"'].map(
                    (example) => (
                      <button
                        key={example}
                        type="button"
                        onClick={() => {
                          setQuery(example);
                          void runSearch(example);
                        }}
                      >
                        {example}
                      </button>
                    ),
                  )}
                  <button
                    type="button"
                    onClick={() => imageInputRef.current?.click()}
                  >
                    upload a frame for composition
                  </button>
                </div>
              )}
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
            {!loading &&
              !error &&
              speech.status === "idle" &&
              results.length === 0 &&
              (completedQuery === query.trim() || referenceLabel !== null) && (
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
          <ResultGrid
            results={results}
            visibleCount={visibleResultCount}
            batchSize={resultBatchSize}
            revealDisabled={loading}
            onShowMore={() =>
              setVisibleResultCount((count) => count + resultBatchSize)
            }
            onShotClick={setActiveShot}
            onFindSimilar={handleFindSimilar}
            debug={debug}
            similarDisabled={loading}
          />

          {/* video modal */}
          {activeShot && (
            <VideoModal
              shot={activeShot}
              onClose={() => setActiveShot(null)}
              onMatchComposition={handleFindSimilar}
              matchCompositionDisabled={loading}
            />
          )}
        </>
      )}
    </main>
  );
}
