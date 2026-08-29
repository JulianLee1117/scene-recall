"use client";

import {
  useState,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
} from "react";
import ResultGrid, { type ResultGrouping } from "@/components/ResultGrid";
import VideoModal from "@/components/VideoModal";
import LibraryView from "@/components/LibraryView";
import SavedView from "@/components/SavedView";
import MatchByRail from "@/components/MatchByRail";
import MovieScopeFilter from "@/components/MovieScopeFilter";
import SearchOptions from "@/components/SearchOptions";
import { useBookmarks } from "@/hooks/useBookmarks";
import { useSpeechRecognition } from "@/hooks/useSpeechRecognition";
import {
  MAX_RECIPE_CLAUSES,
  buildRecipeClauses,
  matchDraftHasClause,
  recipeClauseCount,
  sourceDraftFromShot,
  type MatchDraft,
  type MatchDrafts,
  type TextMatchFacet,
} from "@/lib/searchRecipe";
import { bestResultPerFilm } from "@/lib/searchResults";
import type {
  RecipeMatchFacet,
  SearchRecipeRequest,
  SearchRecipeResponse,
  SearchResult,
  SearchResponse,
} from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
const MOVIE_SCOPE_SEARCH_DEBOUNCE_MS = 350;

async function searchError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as {
      detail?: string | Array<{ msg?: string }>;
    };
    if (typeof body.detail === "string" && body.detail) return body.detail;
    if (Array.isArray(body.detail)) {
      const message = body.detail.find((item) => item.msg)?.msg;
      if (message) return message;
    }
  } catch {
    // Keep the status-based fallback for non-JSON errors.
  }
  return `Search failed (${response.status})`;
}

type Tab = "search" | "saved" | "library";

const TABS: Array<{ id: Tab; label: string }> = [
  { id: "search", label: "Search" },
  { id: "saved", label: "Saved" },
  { id: "library", label: "Films" },
];

export default function Home() {
  const [activeTab, setActiveTab] = useState<Tab>("search");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recipeNotice, setRecipeNotice] = useState<string | null>(null);
  const [hasCompletedSearch, setHasCompletedSearch] = useState(false);
  const [matchDrafts, setMatchDrafts] = useState<MatchDrafts>({});
  const [referenceLabel, setReferenceLabel] = useState<string | null>(null);
  const [referencePreviewUrl, setReferencePreviewUrl] = useState<string | null>(
    null,
  );
  const [activeShot, setActiveShot] = useState<SearchResult | null>(null);
  const [debug, setDebug] = useState(false);
  const [resultGrouping, setResultGrouping] = useState<ResultGrouping>("all");
  const [selectedFilmIds, setSelectedFilmIds] = useState<string[]>([]);
  const inputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const searchAbortRef = useRef<AbortController | null>(null);
  const scopeSearchTimerRef = useRef<number | null>(null);
  const referenceBlobRef = useRef<Blob | null>(null);
  const referenceLabelRef = useRef<string | null>(null);
  const referencePreviewUrlRef = useRef<string | null>(null);
  const voiceStatusId = useId();
  const {
    bookmarks,
    bookmarkByUnit,
    pendingUnitIds: pendingBookmarkUnitIds,
    loading: bookmarksLoading,
    error: bookmarkError,
    toggleBookmark,
    removeBookmark,
  } = useBookmarks();

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
    referenceLabelRef.current = null;
    referencePreviewUrlRef.current = null;
    setReferenceLabel(null);
    setReferencePreviewUrl(null);
  }, [cancelPendingScopeSearch]);

  const activateReference = useCallback(
    (image: Blob, label: string) => {
      if (referencePreviewUrlRef.current) {
        URL.revokeObjectURL(referencePreviewUrlRef.current);
      }
      const previewUrl = URL.createObjectURL(image);
      referenceBlobRef.current = image;
      referenceLabelRef.current = label;
      referencePreviewUrlRef.current = previewUrl;
      setReferenceLabel(label);
      setReferencePreviewUrl(previewUrl);
      setHasCompletedSearch(false);
      setActiveShot(null);
    },
    [],
  );

  const runRecipe = useCallback(
    async (
      mainText: string,
      drafts: MatchDrafts,
      scope: readonly string[] = selectedFilmIds,
    ) => {
      const clauses = buildRecipeClauses(mainText, drafts);
      if (clauses.length === 0) {
        cancelPendingScopeSearch();
        searchAbortRef.current?.abort();
        searchAbortRef.current = null;
        setLoading(false);
        setResults([]);
        setError(null);
        setRecipeNotice(null);
        setHasCompletedSearch(false);
        return;
      }
      if (clauses.length > MAX_RECIPE_CLAUSES) {
        setRecipeNotice("Use up to three search parts.");
        return;
      }

      cancelPendingScopeSearch();
      searchAbortRef.current?.abort();
      const controller = new AbortController();
      searchAbortRef.current = controller;
      setLoading(true);
      setError(null);
      setRecipeNotice(null);
      setHasCompletedSearch(false);

      const request: SearchRecipeRequest = {
        clauses,
        ...(scope.length ? { film_ids: [...scope] } : {}),
      };

      try {
        const response = await fetch(`${API_URL}/search/recipe`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(request),
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(await searchError(response));
        const data: SearchRecipeResponse = await response.json();
        if (searchAbortRef.current !== controller) return;
        setResults(data.results);
        setHasCompletedSearch(true);
      } catch (reason) {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "Search failed");
        setResults([]);
      } finally {
        if (searchAbortRef.current === controller) {
          searchAbortRef.current = null;
          setLoading(false);
        }
      }
    },
    [cancelPendingScopeSearch, selectedFilmIds],
  );

  const runImageSearch = useCallback(
    async (
      image: Blob,
      label: string,
      scope: readonly string[] = selectedFilmIds,
      textQuery: string = "",
    ) => {
      cancelPendingScopeSearch();
      searchAbortRef.current?.abort();
      const controller = new AbortController();
      searchAbortRef.current = controller;
      setLoading(true);
      setError(null);
      setRecipeNotice(null);
      setHasCompletedSearch(false);

      const params = new URLSearchParams();
      scope.forEach((filmId) => params.append("film_id", filmId));
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
        if (!res.ok) throw new Error(await searchError(res));
        const data: SearchResponse = await res.json();
        if (searchAbortRef.current !== controller) return;
        setResults(data.results);
        setReferenceLabel(label);
        setHasCompletedSearch(true);
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
    [cancelPendingScopeSearch, selectedFilmIds],
  );

  const leaveUploadedReference = useCallback(() => {
    if (!referenceBlobRef.current) return;
    clearReference();
    searchAbortRef.current = null;
    setLoading(false);
  }, [clearReference]);

  const handleRecipeLimit = useCallback(() => {
    setRecipeNotice("Use up to three search parts.");
  }, []);

  const handleActivateTextFacet = useCallback(
    (facet: TextMatchFacet) => {
      if (matchDrafts[facet]) return;
      if (recipeClauseCount(query, matchDrafts) >= MAX_RECIPE_CLAUSES) {
        handleRecipeLimit();
        return;
      }
      leaveUploadedReference();
      setRecipeNotice(null);
      setHasCompletedSearch(false);
      setMatchDrafts((current) => ({
        ...current,
        [facet]: { kind: "text", facet, text: "" },
      }));
    },
    [handleRecipeLimit, leaveUploadedReference, matchDrafts, query],
  );

  const handleFacetTextChange = useCallback(
    (facet: TextMatchFacet, text: string) => {
      const previous = matchDrafts[facet];
      const previouslyActive = matchDraftHasClause(previous);
      const nextDraft: MatchDraft = { kind: "text", facet, text };
      const nextDrafts = { ...matchDrafts, [facet]: nextDraft };
      const nextCount = recipeClauseCount(query, nextDrafts);
      if (!previouslyActive && text.trim() && nextCount > MAX_RECIPE_CLAUSES) {
        handleRecipeLimit();
        return;
      }

      cancelPendingScopeSearch();
      searchAbortRef.current?.abort();
      searchAbortRef.current = null;
      setLoading(false);
      leaveUploadedReference();
      setMatchDrafts(nextDrafts);
      setRecipeNotice(null);
      setHasCompletedSearch(false);
      if (previouslyActive && !text.trim()) {
        void runRecipe(query, nextDrafts);
      }
    },
    [
      cancelPendingScopeSearch,
      handleRecipeLimit,
      leaveUploadedReference,
      matchDrafts,
      query,
      runRecipe,
    ],
  );

  const handleRemoveFacet = useCallback(
    (facet: RecipeMatchFacet) => {
      const removedClause = matchDraftHasClause(matchDrafts[facet]);
      const nextDrafts = { ...matchDrafts };
      delete nextDrafts[facet];
      setMatchDrafts(nextDrafts);
      setRecipeNotice(null);
      setHasCompletedSearch(false);
      if (removedClause) void runRecipe(query, nextDrafts);
    },
    [matchDrafts, query, runRecipe],
  );

  const applySourceFacet = useCallback(
    (facet: RecipeMatchFacet, draft: MatchDraft) => {
      if (draft.kind !== "source") return;
      const replacingClause = matchDraftHasClause(matchDrafts[facet]);
      if (
        !replacingClause &&
        recipeClauseCount(query, matchDrafts) >= MAX_RECIPE_CLAUSES
      ) {
        handleRecipeLimit();
        return;
      }

      leaveUploadedReference();
      const nextDrafts: MatchDrafts = {
        ...matchDrafts,
        [facet]: { ...draft, facet },
      };
      setMatchDrafts(nextDrafts);
      setRecipeNotice(null);
      setHasCompletedSearch(false);
      setActiveTab("search");
      setActiveShot(null);
      void runRecipe(query, nextDrafts);
    },
    [
      handleRecipeLimit,
      leaveUploadedReference,
      matchDrafts,
      query,
      runRecipe,
    ],
  );

  const handleUseInSearch = useCallback(
    (shot: SearchResult, facet: RecipeMatchFacet) => {
      const draft = sourceDraftFromShot(facet, shot);
      if (!draft) {
        setError("This scene does not have an exact searchable frame.");
        return;
      }
      applySourceFacet(facet, draft);
    },
    [applySourceFacet],
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
      const hasRecipe = buildRecipeClauses(pendingQuery, matchDrafts).length > 0;
      if (!hasReference && !hasRecipe) return;

      scopeSearchTimerRef.current = window.setTimeout(() => {
        scopeSearchTimerRef.current = null;
        if (referenceBlobRef.current && referenceLabelRef.current) {
          void runImageSearch(
            referenceBlobRef.current,
            referenceLabelRef.current,
            filmIds,
            pendingQuery,
          );
        } else if (hasRecipe) {
          void runRecipe(pendingQuery, matchDrafts, filmIds);
        }
      }, MOVIE_SCOPE_SEARCH_DEBOUNCE_MS);
    },
    [
      cancelPendingScopeSearch,
      matchDrafts,
      query,
      runImageSearch,
      runRecipe,
    ],
  );

  const handleVoiceTranscript = useCallback(
    (transcript: string) => {
      cancelPendingScopeSearch();
      searchAbortRef.current?.abort();
      searchAbortRef.current = null;
      setLoading(false);
      setQuery(transcript);
      setHasCompletedSearch(false);
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
          selectedFilmIds,
          transcript,
        );
      } else {
        void runRecipe(transcript, matchDrafts);
      }
    },
    [matchDrafts, runImageSearch, runRecipe, selectedFilmIds],
  );

  const speech = useSpeechRecognition({
    onTranscript: handleVoiceTranscript,
    onComplete: handleVoiceComplete,
  });

  const handleReferenceFile = useCallback(
    (file: File) => {
      speech.cancel();
      setMatchDrafts({});
      setRecipeNotice(null);
      activateReference(file, file.name || "Uploaded frame");
      void runImageSearch(
        file,
        file.name || "Uploaded frame",
        selectedFilmIds,
        query,
      );
    },
    [activateReference, query, runImageSearch, selectedFilmIds, speech],
  );

  const handleFindSimilar = useCallback(
    (shot: SearchResult) => {
      speech.cancel();
      handleUseInSearch(shot, "composition");
    },
    [handleUseInSearch, speech],
  );

  const handleClearReference = useCallback(() => {
    searchAbortRef.current?.abort();
    clearReference();
    setActiveShot(null);
    if (buildRecipeClauses(query, matchDrafts).length > 0) {
      void runRecipe(query, matchDrafts);
    } else {
      setResults([]);
      setError(null);
      setHasCompletedSearch(false);
      setLoading(false);
    }
    inputRef.current?.focus();
  }, [clearReference, matchDrafts, query, runRecipe]);

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

  const handleQueryChange = useCallback(
    (nextQuery: string) => {
      speech.cancel();
      speech.clearError();
      cancelPendingScopeSearch();
      searchAbortRef.current?.abort();
      searchAbortRef.current = null;
      setLoading(false);
      const removedMainClause = Boolean(query.trim()) && !nextQuery.trim();
      setQuery(nextQuery);
      setHasCompletedSearch(false);
      setRecipeNotice(
        recipeClauseCount(nextQuery, matchDrafts) > MAX_RECIPE_CLAUSES
          ? "Remove one match to search."
          : null,
      );

      if (removedMainClause && !referenceBlobRef.current) {
        void runRecipe(nextQuery, matchDrafts);
      }
    },
    [cancelPendingScopeSearch, matchDrafts, query, runRecipe, speech],
  );

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    speech.cancel();
    if (referenceBlobRef.current && referenceLabelRef.current) {
      void runImageSearch(
        referenceBlobRef.current,
        referenceLabelRef.current,
        selectedFilmIds,
        query,
      );
    } else {
      void runRecipe(query, matchDrafts);
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape" && speech.status !== "idle") {
      event.preventDefault();
      speech.cancel();
      inputRef.current?.focus();
    }
  };

  const isEmpty = results.length === 0 && !loading && !error;
  const clauseCount = recipeClauseCount(query, matchDrafts);
  const recipeOverLimit = clauseCount > MAX_RECIPE_CLAUSES;
  const hasFacetDrafts = Object.keys(matchDrafts).length > 0;
  const searchDisabled =
    loading ||
    recipeOverLimit ||
    (!referenceLabel && buildRecipeClauses(query, matchDrafts).length === 0);
  const displayedResults = useMemo(
    () =>
      resultGrouping === "best-per-movie"
        ? bestResultPerFilm(results)
        : results,
    [resultGrouping, results],
  );
  const bookmarkedUnitIds = useMemo(
    () => new Set(bookmarkByUnit.keys()),
    [bookmarkByUnit],
  );
  const activeShotBookmark = activeShot
    ? bookmarkByUnit.get(activeShot.unit_id)
    : undefined;
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
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => {
              if (tab.id !== "search") speech.cancel();
              setActiveTab(tab.id);
            }}
            style={{
              background: "none",
              border: "none",
              borderBottom:
                activeTab === tab.id
                  ? "2px solid #d4a96a"
                  : "2px solid transparent",
              color: activeTab === tab.id ? "#ededed" : "#555",
              cursor: "pointer",
              fontSize: "0.82rem",
              fontWeight: activeTab === tab.id ? 500 : 400,
              letterSpacing: "0.04em",
              marginBottom: "-1px",
              padding: "13px 14px",
              textTransform: "capitalize",
              transition: "color 0.15s",
            }}
            onMouseEnter={(e) => {
              if (activeTab !== tab.id)
                e.currentTarget.style.color = "#888";
            }}
            onMouseLeave={(e) => {
              if (activeTab !== tab.id)
                e.currentTarget.style.color = "#555";
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Library view */}
      {activeTab === "library" && <LibraryView />}

      {/* Saved view */}
      {activeTab === "saved" && (
        <SavedView
          bookmarks={bookmarks}
          loading={bookmarksLoading}
          error={bookmarkError}
          pendingUnitIds={pendingBookmarkUnitIds}
          onShotClick={setActiveShot}
          onFindSimilar={handleFindSimilar}
          onUseInSearch={handleUseInSearch}
          onToggleBookmark={(shot) => void toggleBookmark(shot)}
          onRemoveBookmark={(bookmark) => void removeBookmark(bookmark)}
        />
      )}

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
                maxWidth: isEmpty ? "760px" : "720px",
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
                  maxLength={500}
                  onChange={(event) => handleQueryChange(event.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={
                    referenceLabel
                      ? "add a broad text constraint…"
                      : "describe anything you remember…"
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
                  disabled={searchDisabled}
                  aria-label="Search"
                  style={{
                    position: "absolute",
                    right: isEmpty ? "14px" : "10px",
                    background: "none",
                    border: "none",
                    cursor: searchDisabled ? "default" : "pointer",
                    color: searchDisabled ? "#444" : "#d4a96a",
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

              {!referenceLabel && (
                <MatchByRail
                  mainText={query}
                  drafts={matchDrafts}
                  onActivateText={handleActivateTextFacet}
                  onTextChange={handleFacetTextChange}
                  onRemove={handleRemoveFacet}
                  onSource={applySourceFacet}
                  onLimit={handleRecipeLimit}
                />
              )}

              {recipeNotice && (
                <p className="match-notice" role="status">
                  {recipeNotice}
                </p>
              )}

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
                  <SearchOptions
                    showRankingDetails={debug}
                    onShowRankingDetailsChange={setDebug}
                  />
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
                        ? "Uploaded composition + broad text"
                        : "Uploaded composition reference"}
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

              {isEmpty && !query.trim() && !referenceLabel && !hasFacetDrafts && (
                <div className="search-examples" aria-label="Example searches">
                  <span>Try</span>
                  {["a lonely figure under red neon", '"I remember everything"'].map(
                    (example) => (
                      <button
                        key={example}
                        type="button"
                        onClick={() => {
                          setQuery(example);
                          void runRecipe(example, matchDrafts);
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
              hasCompletedSearch && (
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
            results={displayedResults}
            grouping={resultGrouping}
            onGroupingChange={setResultGrouping}
            revealDisabled={loading}
            onShotClick={setActiveShot}
            onFindSimilar={handleFindSimilar}
            onUseInSearch={handleUseInSearch}
            onToggleBookmark={(shot) => void toggleBookmark(shot)}
            bookmarkedUnitIds={bookmarkedUnitIds}
            pendingBookmarkUnitIds={pendingBookmarkUnitIds}
            bookmarkDisabled={bookmarksLoading}
            debug={debug}
            similarDisabled={loading}
          />
        </>
      )}

      {bookmarkError && activeTab !== "saved" && (
        <p className="bookmark-error" role="status">
          {bookmarkError}
        </p>
      )}

      {/* Shared player for Search and Saved scenes. */}
      {activeShot && (
        <VideoModal
          shot={activeShot}
          onClose={() => setActiveShot(null)}
          onMatchComposition={handleFindSimilar}
          onUseInSearch={handleUseInSearch}
          matchCompositionDisabled={loading}
          onToggleBookmark={(shot) => void toggleBookmark(shot)}
          bookmarked={Boolean(activeShotBookmark)}
          bookmarkDisabled={
            bookmarksLoading || pendingBookmarkUnitIds.has(activeShot.unit_id)
          }
        />
      )}
    </main>
  );
}
