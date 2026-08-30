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
import { useFacetSourceSearch } from "@/hooks/useFacetSourceSearch";
import { useSpeechRecognition } from "@/hooks/useSpeechRecognition";
import {
  FACET_LABELS,
  MATCH_FACETS,
  MAX_RECIPE_CLAUSES,
  buildRecipeClauses,
  matchDraftHasClause,
  recipeClauseCount,
  sourceDraftFromShot,
  type MatchDraft,
  type MatchDrafts,
  type RecipeImageInput,
  type TextMatchFacet,
} from "@/lib/searchRecipe";
import { bestResultPerFilm } from "@/lib/searchResults";
import type {
  RecipeMatchFacet,
  ResolvedSourceEvidence,
  SearchRecipeRequest,
  SearchRecipeResponse,
  SearchResult,
} from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
const MOVIE_SCOPE_SEARCH_DEBOUNCE_MS = 350;
const RECIPE_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

function isSupportedRecipeImage(file: File): boolean {
  // Some OS drag sources omit the browser MIME type. The backend still
  // verifies the decoded format before using the image.
  return !file.type || RECIPE_IMAGE_TYPES.has(file.type);
}

function containsFile(transfer: DataTransfer): boolean {
  return transfer.types.includes("Files");
}

function dragLeftElement(event: React.DragEvent<HTMLElement>): boolean {
  const bounds = event.currentTarget.getBoundingClientRect();
  return (
    event.clientX <= bounds.left ||
    event.clientX >= bounds.right ||
    event.clientY <= bounds.top ||
    event.clientY >= bounds.bottom
  );
}

function revokeImageInput(image: RecipeImageInput | null | undefined) {
  if (image) URL.revokeObjectURL(image.display.previewUrl);
}

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

function focusFacetBrowse(facet: RecipeMatchFacet) {
  window.requestAnimationFrame(() => {
    document
      .querySelector<HTMLButtonElement>(`[data-browse-facet="${facet}"]`)
      ?.focus();
  });
}

export default function Home() {
  const [activeTab, setActiveTab] = useState<Tab>("search");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recipeNotice, setRecipeNotice] = useState<string | null>(null);
  const [hasCompletedSearch, setHasCompletedSearch] = useState(false);
  const [searchWorkspaceActive, setSearchWorkspaceActive] = useState(false);
  const [matchDrafts, setMatchDrafts] = useState<MatchDrafts>({});
  const [mainImage, setMainImage] = useState<RecipeImageInput | null>(null);
  const mainImageRef = useRef<RecipeImageInput | null>(null);
  const [mainImageDragOver, setMainImageDragOver] = useState(false);
  const [sourceEvidenceByFacet, setSourceEvidenceByFacet] = useState<
    Partial<Record<RecipeMatchFacet, ResolvedSourceEvidence>>
  >({});
  const [activeShot, setActiveShot] = useState<SearchResult | null>(null);
  const [debug, setDebug] = useState(false);
  const [resultGrouping, setResultGrouping] = useState<ResultGrouping>("all");
  const [selectedFilmIds, setSelectedFilmIds] = useState<string[]>([]);
  const facetSourceSearch = useFacetSourceSearch(selectedFilmIds);
  const sourceReferenceFacet = facetSourceSearch.facet;
  const inputRef = useRef<HTMLInputElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);
  const searchAbortRef = useRef<AbortController | null>(null);
  const scopeSearchTimerRef = useRef<number | null>(null);
  const sourceModeWorkspaceRef = useRef(false);
  const sourceModeRecipeDirtyRef = useRef(false);
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

  const runRecipe = useCallback(
    async (
      mainText: string,
      drafts: MatchDrafts,
      scope: readonly string[] = selectedFilmIds,
      image: RecipeImageInput | null = mainImageRef.current,
    ) => {
      const clauses = buildRecipeClauses(mainText, drafts, image);
      if (clauses.length === 0) {
        cancelPendingScopeSearch();
        searchAbortRef.current?.abort();
        searchAbortRef.current = null;
        setLoading(false);
        setError(null);
        setRecipeNotice(null);
        setSourceEvidenceByFacet({});
        return;
      }
      if (clauses.length > MAX_RECIPE_CLAUSES) {
        setRecipeNotice("Use up to three matches at once.");
        return;
      }

      cancelPendingScopeSearch();
      searchAbortRef.current?.abort();
      const controller = new AbortController();
      searchAbortRef.current = controller;
      setSearchWorkspaceActive(true);
      setLoading(true);
      setError(null);
      setRecipeNotice(null);
      setSourceEvidenceByFacet({});
      setHasCompletedSearch(false);

      const request: SearchRecipeRequest = {
        clauses,
        ...(scope.length ? { film_ids: [...scope] } : {}),
      };

      try {
        const formData = image ? new FormData() : null;
        if (formData && image) {
          formData.append("recipe", JSON.stringify(request));
          formData.append("image", image.file, image.file.name);
        }
        const response = await fetch(
          `${API_URL}${image ? "/search/recipe/image" : "/search/recipe"}`,
          {
            method: "POST",
            ...(formData
              ? { body: formData }
              : {
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify(request),
                }),
            signal: controller.signal,
          },
        );
        if (!response.ok) throw new Error(await searchError(response));
        const data: SearchRecipeResponse = await response.json();
        if (searchAbortRef.current !== controller) return;
        setResults(data.results);
        setSourceEvidenceByFacet(
          Object.fromEntries(
            (data.source_evidence ?? [])
              .filter((evidence) => evidence.facet !== "visual")
              .map((evidence) => [evidence.facet, evidence]),
          ) as Partial<Record<RecipeMatchFacet, ResolvedSourceEvidence>>,
        );
        setHasCompletedSearch(true);
      } catch (reason) {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "Search failed");
        setResults([]);
        setSourceEvidenceByFacet({});
      } finally {
        if (searchAbortRef.current === controller) {
          searchAbortRef.current = null;
          setLoading(false);
        }
      }
    },
    [cancelPendingScopeSearch, selectedFilmIds],
  );

  const handleRecipeLimit = useCallback(() => {
    setRecipeNotice("Use up to three matches at once.");
  }, []);

  const handleActivateTextFacet = useCallback(
    (facet: TextMatchFacet) => {
      if (sourceReferenceFacet) {
        const refreshRecipe = sourceModeRecipeDirtyRef.current;
        sourceModeRecipeDirtyRef.current = false;
        facetSourceSearch.close();
        setSearchWorkspaceActive(sourceModeWorkspaceRef.current);
        if (refreshRecipe) {
          void runRecipe(query, matchDrafts, selectedFilmIds);
        }
      }
      if (matchDrafts[facet]) return;
      if (
        recipeClauseCount(query, matchDrafts, mainImage) >= MAX_RECIPE_CLAUSES
      ) {
        handleRecipeLimit();
        return;
      }
      setRecipeNotice(null);
      setHasCompletedSearch(false);
      setMatchDrafts((current) => ({
        ...current,
        [facet]: { kind: "text", facet, text: "" },
      }));
    },
    [
      facetSourceSearch,
      handleRecipeLimit,
      mainImage,
      matchDrafts,
      query,
      runRecipe,
      selectedFilmIds,
      sourceReferenceFacet,
    ],
  );

  const handleFacetTextChange = useCallback(
    (facet: TextMatchFacet, text: string) => {
      const previous = matchDrafts[facet];
      const previouslyActive = matchDraftHasClause(previous);
      const nextDraft: MatchDraft = { kind: "text", facet, text };
      const nextDrafts = { ...matchDrafts, [facet]: nextDraft };
      const nextCount = recipeClauseCount(query, nextDrafts, mainImage);
      if (!previouslyActive && text.trim() && nextCount > MAX_RECIPE_CLAUSES) {
        handleRecipeLimit();
        return;
      }

      cancelPendingScopeSearch();
      searchAbortRef.current?.abort();
      searchAbortRef.current = null;
      setLoading(false);
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
      mainImage,
      matchDrafts,
      query,
      runRecipe,
    ],
  );

  const handleFacetTextSubmit = useCallback(
    (facet: TextMatchFacet, text: string) => {
      const nextDrafts: MatchDrafts = {
        ...matchDrafts,
        [facet]: { kind: "text", facet, text },
      };
      setMatchDrafts(nextDrafts);
      setRecipeNotice(null);
      void runRecipe(query, nextDrafts);
    },
    [matchDrafts, query, runRecipe],
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
    (
      facet: RecipeMatchFacet,
      draft: MatchDraft,
      originFacet?: RecipeMatchFacet,
    ) => {
      if (draft.kind !== "source") return;
      const originDraft = originFacet ? matchDrafts[originFacet] : undefined;
      const isMovingSource = Boolean(
        originFacet &&
          originFacet !== facet &&
          originDraft?.kind === "source" &&
          originDraft.source.unit_id === draft.source.unit_id &&
          originDraft.source.frame_index === draft.source.frame_index,
      );
      if (originFacet === facet) return;

      const baseDrafts = { ...matchDrafts };
      if (isMovingSource && originFacet) delete baseDrafts[originFacet];
      const replacingClause = matchDraftHasClause(baseDrafts[facet]);
      if (
        !replacingClause &&
        recipeClauseCount(query, baseDrafts, mainImage) >= MAX_RECIPE_CLAUSES
      ) {
        handleRecipeLimit();
        return;
      }

      const nextDrafts: MatchDrafts = {
        ...baseDrafts,
        [facet]: { ...draft, facet },
      };
      setMatchDrafts(nextDrafts);
      setRecipeNotice(null);
      setHasCompletedSearch(false);
      setActiveTab("search");
      setActiveShot(null);
      void runRecipe(query, nextDrafts);
    },
    [handleRecipeLimit, mainImage, matchDrafts, query, runRecipe],
  );

  const handleSourceReferenceChoose = useCallback(
    (shot: SearchResult) => {
      if (!sourceReferenceFacet) return;
      const targetFacet = sourceReferenceFacet;
      const draft = sourceDraftFromShot(sourceReferenceFacet, shot);
      if (!draft) {
        facetSourceSearch.close();
        setSearchWorkspaceActive(sourceModeWorkspaceRef.current);
        setError("This scene does not have an exact searchable frame.");
        focusFacetBrowse(targetFacet);
        return;
      }
      facetSourceSearch.close();
      sourceModeRecipeDirtyRef.current = false;
      applySourceFacet(targetFacet, draft);
      focusFacetBrowse(targetFacet);
    },
    [applySourceFacet, facetSourceSearch, sourceReferenceFacet],
  );

  const handleSourceReferenceCancel = useCallback(() => {
    const targetFacet = sourceReferenceFacet;
    const refreshRecipe = sourceModeRecipeDirtyRef.current;
    sourceModeRecipeDirtyRef.current = false;
    setActiveShot(null);
    facetSourceSearch.close();
    setSearchWorkspaceActive(sourceModeWorkspaceRef.current);
    if (refreshRecipe) {
      void runRecipe(query, matchDrafts, selectedFilmIds);
    }
    if (targetFacet) focusFacetBrowse(targetFacet);
  }, [
    facetSourceSearch,
    matchDrafts,
    query,
    runRecipe,
    selectedFilmIds,
    sourceReferenceFacet,
  ]);

  const handleMovieScopeChange = useCallback(
    (filmIds: string[]) => {
      cancelPendingScopeSearch();
      searchAbortRef.current?.abort();
      searchAbortRef.current = null;
      setLoading(false);
      setSelectedFilmIds(filmIds);
      setActiveShot(null);

      const pendingQuery = query.trim();
      const hasRecipe =
        buildRecipeClauses(pendingQuery, matchDrafts, mainImage).length > 0;
      if (sourceReferenceFacet) {
        sourceModeRecipeDirtyRef.current = hasRecipe;
        if (
          (facetSourceSearch.hasSearched || facetSourceSearch.loading) &&
          facetSourceSearch.query.trim()
        ) {
          void facetSourceSearch.search(filmIds);
        }
        return;
      }

      if (hasRecipe) {
        scopeSearchTimerRef.current = window.setTimeout(() => {
          scopeSearchTimerRef.current = null;
          void runRecipe(pendingQuery, matchDrafts, filmIds);
        }, MOVIE_SCOPE_SEARCH_DEBOUNCE_MS);
      }
    },
    [
      cancelPendingScopeSearch,
      facetSourceSearch,
      mainImage,
      matchDrafts,
      query,
      runRecipe,
      sourceReferenceFacet,
    ],
  );

  const handleVoiceTranscript = useCallback(
    (transcript: string) => {
      if (sourceReferenceFacet) {
        facetSourceSearch.setQuery(transcript);
        return;
      }
      cancelPendingScopeSearch();
      searchAbortRef.current?.abort();
      searchAbortRef.current = null;
      setLoading(false);
      setQuery(transcript);
      setHasCompletedSearch(false);
    },
    [cancelPendingScopeSearch, facetSourceSearch, sourceReferenceFacet],
  );

  const handleVoiceComplete = useCallback(
    (transcript: string) => {
      if (sourceReferenceFacet) {
        facetSourceSearch.setQuery(transcript);
        setSearchWorkspaceActive(true);
        void facetSourceSearch.search(selectedFilmIds, transcript);
        inputRef.current?.focus();
        return;
      }
      setQuery(transcript);
      inputRef.current?.focus();
      void runRecipe(transcript, matchDrafts);
    },
    [
      facetSourceSearch,
      matchDrafts,
      runRecipe,
      selectedFilmIds,
      sourceReferenceFacet,
    ],
  );

  const speech = useSpeechRecognition({
    onTranscript: handleVoiceTranscript,
    onComplete: handleVoiceComplete,
  });

  const handleUseInSearch = useCallback(
    (shot: SearchResult, facet: RecipeMatchFacet) => {
      speech.cancel();
      const draft = sourceDraftFromShot(facet, shot);
      if (!draft) {
        setError("This scene does not have an exact searchable frame.");
        return;
      }
      applySourceFacet(facet, draft);
    },
    [applySourceFacet, speech],
  );

  const handleBrowseFacet = useCallback(
    (facet: RecipeMatchFacet) => {
      speech.cancel();
      setActiveShot(null);
      if (!sourceReferenceFacet) {
        sourceModeWorkspaceRef.current = searchWorkspaceActive;
        sourceModeRecipeDirtyRef.current = false;
      }
      facetSourceSearch.open(facet);
      window.requestAnimationFrame(() => inputRef.current?.focus());
    },
    [facetSourceSearch, searchWorkspaceActive, sourceReferenceFacet, speech],
  );

  const resetSearchHome = useCallback(() => {
    speech.cancel();
    cancelPendingScopeSearch();
    searchAbortRef.current?.abort();
    searchAbortRef.current = null;
    facetSourceSearch.close();
    sourceModeRecipeDirtyRef.current = false;
    setActiveTab("search");
    setQuery("");
    revokeImageInput(mainImageRef.current);
    mainImageRef.current = null;
    setMatchDrafts({});
    setMainImage(null);
    setSourceEvidenceByFacet({});
    setResults([]);
    setLoading(false);
    setError(null);
    setRecipeNotice(null);
    setHasCompletedSearch(false);
    setSearchWorkspaceActive(false);
    setSelectedFilmIds([]);
    setResultGrouping("all");
    setActiveShot(null);
    window.requestAnimationFrame(() => inputRef.current?.focus());
  }, [
    cancelPendingScopeSearch,
    facetSourceSearch,
    speech,
  ]);

  const handleMainImageFile = useCallback(
    (file: File) => {
      if (sourceReferenceFacet) return;
      speech.cancel();
      if (!isSupportedRecipeImage(file)) {
        setError("Use a JPEG, PNG, or WebP reference image.");
        return;
      }

      if (
        !mainImageRef.current &&
        recipeClauseCount(query, matchDrafts) >= MAX_RECIPE_CLAUSES
      ) {
        setRecipeNotice("Remove one match before adding an image.");
        return;
      }

      const nextImage: RecipeImageInput = {
        file,
        display: {
          label: file.name || "Uploaded frame",
          previewUrl: URL.createObjectURL(file),
        },
      };
      const previousImage = mainImageRef.current;
      mainImageRef.current = nextImage;
      setMainImage(nextImage);
      revokeImageInput(previousImage);
      setSourceEvidenceByFacet({});
      setError(null);
      setRecipeNotice(null);
      setHasCompletedSearch(false);
      setActiveTab("search");
      setActiveShot(null);
      void runRecipe(query, matchDrafts, selectedFilmIds, nextImage);
    },
    [
      matchDrafts,
      query,
      runRecipe,
      selectedFilmIds,
      sourceReferenceFacet,
      speech,
    ],
  );

  const handleRemoveMainImage = useCallback(() => {
    const previousImage = mainImageRef.current;
    if (!previousImage) return;
    mainImageRef.current = null;
    setMainImage(null);
    revokeImageInput(previousImage);
    setRecipeNotice(null);
    setHasCompletedSearch(false);
    void runRecipe(query, matchDrafts, selectedFilmIds, null);
  }, [matchDrafts, query, runRecipe, selectedFilmIds]);

  useEffect(
    () => () => {
      cancelPendingScopeSearch();
      searchAbortRef.current?.abort();
      revokeImageInput(mainImageRef.current);
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
        recipeClauseCount(nextQuery, matchDrafts, mainImage) >
        MAX_RECIPE_CLAUSES
          ? "Remove one match to search."
          : null,
      );

      if (removedMainClause) {
        void runRecipe(nextQuery, matchDrafts);
      }
    },
    [
      cancelPendingScopeSearch,
      mainImage,
      matchDrafts,
      query,
      runRecipe,
      speech,
    ],
  );

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    speech.cancel();
    if (sourceReferenceFacet) {
      setSearchWorkspaceActive(true);
      void facetSourceSearch.search();
    } else {
      void runRecipe(query, matchDrafts);
    }
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape" && sourceReferenceFacet) {
      event.preventDefault();
      handleSourceReferenceCancel();
    } else if (event.key === "Escape" && speech.status !== "idle") {
      event.preventDefault();
      speech.cancel();
      inputRef.current?.focus();
    }
  };

  const isHome = !searchWorkspaceActive;
  const clauseCount = recipeClauseCount(query, matchDrafts, mainImage);
  const disabledUseFacets = useMemo(
    () =>
      clauseCount < MAX_RECIPE_CLAUSES
        ? new Set<RecipeMatchFacet>()
        : new Set(
            MATCH_FACETS.filter(
              (facet) => !matchDraftHasClause(matchDrafts[facet]),
            ),
          ),
    [clauseCount, matchDrafts],
  );
  const recipeOverLimit = clauseCount > MAX_RECIPE_CLAUSES;
  const hasFacetDrafts = Object.keys(matchDrafts).length > 0;
  const searchDisabled = sourceReferenceFacet
    ? facetSourceSearch.loading || !facetSourceSearch.query.trim()
    : loading ||
      recipeOverLimit ||
      buildRecipeClauses(query, matchDrafts, mainImage).length === 0;
  const displayedResults = useMemo(
    () =>
      resultGrouping === "best-per-movie"
        ? bestResultPerFilm(results)
        : results,
    [resultGrouping, results],
  );
  const displayedSourceResults = useMemo(
    () =>
      facetSourceSearch.grouping === "best-per-movie"
        ? bestResultPerFilm(facetSourceSearch.results)
        : facetSourceSearch.results,
    [
      facetSourceSearch.grouping,
      facetSourceSearch.results,
    ],
  );
  const bookmarkedUnitIds = useMemo(
    () => new Set(bookmarkByUnit.keys()),
    [bookmarkByUnit],
  );
  const activeShotBookmark = activeShot
    ? bookmarkByUnit.get(activeShot.unit_id)
    : undefined;
  const voiceActive = speech.status !== "idle";
  const activeLoading = sourceReferenceFacet
    ? facetSourceSearch.loading
    : loading;
  const activeGrouping = sourceReferenceFacet
    ? facetSourceSearch.grouping
    : resultGrouping;
  const activeResultCount = sourceReferenceFacet
    ? facetSourceSearch.results.length
    : results.length;
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
      onDragOver={(event) => {
        if (!containsFile(event.dataTransfer)) return;
        if (
          event.target instanceof Element &&
          event.target.closest(".search-bar-shell")
        ) {
          return;
        }
        event.preventDefault();
        event.dataTransfer.dropEffect = "none";
      }}
      onDrop={(event) => {
        if (!containsFile(event.dataTransfer)) return;
        if (
          event.target instanceof Element &&
          event.target.closest(".search-bar-shell")
        ) {
          return;
        }
        event.preventDefault();
      }}
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
              if (tab.id === "search") {
                resetSearchHome();
                return;
              }
              speech.cancel();
              facetSourceSearch.close();
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
          onUseInSearch={handleUseInSearch}
          disabledUseFacets={disabledUseFacets}
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
              justifyContent: isHome ? "center" : "flex-start",
              minHeight: isHome ? "calc(100vh - 45px)" : "auto",
              paddingTop: isHome ? 0 : "40px",
              paddingBottom: isHome ? "32px" : "2px",
              transition: "min-height 0.3s ease",
            }}
          >
            {/* wordmark */}
            <button
              type="button"
              className="search-wordmark"
              onClick={resetSearchHome}
              aria-label="Return to Scene Recall home"
              title="Home"
              style={{
                marginBottom: "28px",
                letterSpacing: "0.2em",
                fontSize: isHome ? "1.1rem" : "0.85rem",
                color: "#d4a96a",
                fontWeight: 500,
                textTransform: "uppercase",
                transition: "font-size 0.3s ease",
              }}
            >
              scene-recall
            </button>

            {/* One stable workspace for both recipes and scene references. */}
            <form
              className="search-workspace-form"
              onSubmit={handleSubmit}
              style={{
                width: "100%",
                maxWidth: "1280px",
                padding: "0 16px",
                transition: "max-width 0.3s ease",
              }}
            >
              <div
                className={`search-bar-shell${
                  mainImageDragOver ? " is-image-drag-over" : ""
                }`}
                onDragEnter={(event) => {
                  if (!containsFile(event.dataTransfer)) return;
                  event.preventDefault();
                  if (sourceReferenceFacet) return;
                  setMainImageDragOver(true);
                }}
                onDragOver={(event) => {
                  if (!containsFile(event.dataTransfer)) return;
                  event.preventDefault();
                  event.dataTransfer.dropEffect = sourceReferenceFacet
                    ? "none"
                    : "copy";
                  if (sourceReferenceFacet) return;
                  setMainImageDragOver(true);
                }}
                onDragLeave={(event) => {
                  if (dragLeftElement(event)) setMainImageDragOver(false);
                }}
                onDrop={(event) => {
                  if (!containsFile(event.dataTransfer)) return;
                  event.preventDefault();
                  setMainImageDragOver(false);
                  if (sourceReferenceFacet) return;
                  const file = event.dataTransfer.files.item(0);
                  if (file) handleMainImageFile(file);
                }}
                style={{
                  position: "relative",
                  display: "flex",
                  alignItems: "center",
                }}
              >
                {sourceReferenceFacet && (
                  <button
                    type="button"
                    className="facet-reference-chip"
                    onClick={handleSourceReferenceCancel}
                    aria-label={`Stop finding a scene for ${FACET_LABELS[sourceReferenceFacet]}`}
                    title="Return to your search"
                  >
                    <span>{FACET_LABELS[sourceReferenceFacet]} reference</span>
                    <span aria-hidden="true">{"\u00d7"}</span>
                  </button>
                )}
                {mainImage && !sourceReferenceFacet && (
                  <div
                    className="main-image-chip"
                    title={mainImage.display.label}
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={mainImage.display.previewUrl}
                      alt=""
                      draggable={false}
                    />
                    <button
                      type="button"
                      onClick={handleRemoveMainImage}
                      aria-label="Remove reference image"
                      title="Remove reference image"
                    >
                      {"\u00d7"}
                    </button>
                  </div>
                )}
                <input
                  ref={inputRef}
                  className={
                    sourceReferenceFacet
                      ? "is-source-reference-input"
                      : undefined
                  }
                  type="text"
                  value={sourceReferenceFacet ? facetSourceSearch.query : query}
                  maxLength={500}
                  onChange={(event) =>
                    sourceReferenceFacet
                      ? facetSourceSearch.setQuery(event.target.value)
                      : handleQueryChange(event.target.value)
                  }
                  onKeyDown={handleKeyDown}
                  placeholder={
                    sourceReferenceFacet
                      ? "find a scene…"
                      : "describe anything you remember…"
                  }
                  aria-label={
                    sourceReferenceFacet
                      ? `Find a scene for ${FACET_LABELS[sourceReferenceFacet]}`
                      : "Describe a scene"
                  }
                  aria-describedby={voiceStatus ? voiceStatusId : undefined}
                  autoFocus
                  style={{
                    width: "100%",
                    background: "#141414",
                    border: "1px solid #2a2a2a",
                    borderRadius: "6px",
                    color: "#ededed",
                    fontSize: isHome ? "1.25rem" : "1rem",
                    paddingTop: isHome ? "18px" : "13px",
                    paddingRight: sourceReferenceFacet
                      ? speech.isSupported
                        ? isHome
                          ? "84px"
                          : "78px"
                        : isHome
                          ? "48px"
                          : "42px"
                      : speech.isSupported
                        ? isHome
                          ? "126px"
                          : "116px"
                        : isHome
                          ? "88px"
                          : "78px",
                    paddingBottom: isHome ? "18px" : "13px",
                    paddingLeft: sourceReferenceFacet
                      ? isHome
                        ? "162px"
                        : "156px"
                      : mainImage
                        ? isHome
                          ? "82px"
                          : "74px"
                      : isHome
                        ? "20px"
                        : "16px",
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
                    if (file) handleMainImageFile(file);
                  }}
                />
                <button
                  type="button"
                  className="image-search-button"
                  disabled={Boolean(sourceReferenceFacet)}
                  aria-hidden={Boolean(sourceReferenceFacet)}
                  tabIndex={sourceReferenceFacet ? -1 : 0}
                  aria-label={
                    mainImage ? "Replace reference image" : "Add a reference image"
                  }
                  title={
                    mainImage ? "Replace reference image" : "Add a reference image"
                  }
                  onClick={() => {
                    speech.cancel();
                    imageInputRef.current?.click();
                  }}
                  style={{
                    visibility: sourceReferenceFacet ? "hidden" : "visible",
                    right: speech.isSupported
                      ? isHome
                        ? "78px"
                        : "70px"
                      : isHome
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
                    <path d="m4 17 4.5-4.5 3 3 2-2 3 3" />
                    <path d="M17 2v7M14.5 4.5 17 2l2.5 2.5" />
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
                    onClick={() =>
                      speech.toggle(
                        sourceReferenceFacet ? facetSourceSearch.query : query,
                      )
                    }
                    style={{
                      right: isHome ? "46px" : "40px",
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
                    right: isHome ? "14px" : "10px",
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
                  {activeLoading ? (
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

              <MatchByRail
                clauseCount={clauseCount}
                drafts={matchDrafts}
                sourceEvidence={sourceEvidenceByFacet}
                debug={debug}
                onActivateText={handleActivateTextFacet}
                onTextChange={handleFacetTextChange}
                onSubmitText={handleFacetTextSubmit}
                onRemove={handleRemoveFacet}
                onBrowse={handleBrowseFacet}
                onSource={applySourceFacet}
                onLimit={handleRecipeLimit}
                targetFacet={sourceReferenceFacet ?? undefined}
              />

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
                  {activeResultCount > 0 && (
                    <>
                      <div
                        className="result-view-toggle"
                        role="group"
                        aria-label="Scenes shown per movie"
                      >
                        <button
                          type="button"
                          aria-pressed={activeGrouping === "all"}
                          onClick={() =>
                            sourceReferenceFacet
                              ? facetSourceSearch.setGrouping("all")
                              : setResultGrouping("all")
                          }
                        >
                          All scenes
                        </button>
                        <button
                          type="button"
                          aria-pressed={activeGrouping === "best-per-movie"}
                          aria-label="Show the best scene from each represented movie"
                          title="Keep the highest-ranked returned scene from each movie"
                          onClick={() =>
                            sourceReferenceFacet
                              ? facetSourceSearch.setGrouping("best-per-movie")
                              : setResultGrouping("best-per-movie")
                          }
                        >
                          Best per movie
                        </button>
                      </div>
                      {!sourceReferenceFacet && (
                        <SearchOptions
                          showRankingDetails={debug}
                          onShowRankingDetailsChange={setDebug}
                        />
                      )}
                    </>
                  )}
                </div>
              </div>

              {isHome && (
                <div
                  className={`search-examples${
                    query.trim() ||
                    hasFacetDrafts ||
                    mainImage ||
                    sourceReferenceFacet
                      ? " is-hidden"
                      : ""
                  }`}
                  aria-label="Example searches"
                  aria-hidden={Boolean(
                    query.trim() ||
                      hasFacetDrafts ||
                      mainImage ||
                      sourceReferenceFacet,
                  )}
                >
                  <span>Try</span>
                  {["a lonely figure under red neon", '"I remember everything"'].map(
                    (example) => (
                      <button
                        key={example}
                        type="button"
                        tabIndex={
                          query.trim() ||
                          hasFacetDrafts ||
                          mainImage ||
                          sourceReferenceFacet
                            ? -1
                            : 0
                        }
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
                    tabIndex={
                      query.trim() ||
                      hasFacetDrafts ||
                      mainImage ||
                      sourceReferenceFacet
                        ? -1
                        : 0
                    }
                    onClick={() => imageInputRef.current?.click()}
                  >
                    add a reference image
                  </button>
                </div>
              )}
            </form>

            {/* error */}
            {(sourceReferenceFacet ? facetSourceSearch.error : error) && (
              <p
                style={{
                  marginTop: "16px",
                  color: "#c0392b",
                  fontSize: "0.85rem",
                }}
              >
                {sourceReferenceFacet ? facetSourceSearch.error : error}
              </p>
            )}

            {/* no results */}
            {!activeLoading &&
              !(sourceReferenceFacet ? facetSourceSearch.error : error) &&
              speech.status === "idle" &&
              (sourceReferenceFacet
                ? facetSourceSearch.results.length === 0 &&
                  facetSourceSearch.hasSearched
                : results.length === 0 && hasCompletedSearch) && (
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

          {/* Keep the recipe grid mounted while reference mode is active. */}
          <div hidden={Boolean(sourceReferenceFacet)}>
            <ResultGrid
              results={displayedResults}
              revealDisabled={loading}
              onShotClick={setActiveShot}
              onUseInSearch={handleUseInSearch}
              disabledUseFacets={disabledUseFacets}
              onToggleBookmark={(shot) => void toggleBookmark(shot)}
              bookmarkedUnitIds={bookmarkedUnitIds}
              pendingBookmarkUnitIds={pendingBookmarkUnitIds}
              bookmarkDisabled={bookmarksLoading}
              debug={debug}
            />
          </div>
          {sourceReferenceFacet && (
            <ResultGrid
              results={displayedSourceResults}
              revealDisabled={facetSourceSearch.loading}
              onShotClick={setActiveShot}
              onUseInSearch={handleSourceReferenceChoose}
              sourceReferenceFacet={sourceReferenceFacet}
              onToggleBookmark={(shot) => void toggleBookmark(shot)}
              bookmarkedUnitIds={bookmarkedUnitIds}
              pendingBookmarkUnitIds={pendingBookmarkUnitIds}
              bookmarkDisabled={bookmarksLoading}
              debug={false}
            />
          )}
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
          onUseInSearch={
            sourceReferenceFacet ? handleSourceReferenceChoose : handleUseInSearch
          }
          disabledUseFacets={disabledUseFacets}
          sourceReferenceFacet={sourceReferenceFacet ?? undefined}
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
