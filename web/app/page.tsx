"use client";

import dynamic from "next/dynamic";
import {
  useState,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
} from "react";
import ResultGrid from "@/components/ResultGrid";
import VideoModal from "@/components/VideoModal";
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
import type {
  RecipeImageFacet,
  RecipeMatchFacet,
  ResolvedSourceEvidence,
  SearchRecipeRequest,
  SearchRecipeResponse,
  SearchResult,
} from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
const MOVIE_SCOPE_SEARCH_DEBOUNCE_MS = 350;
const RECIPE_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);
const EMPTY_RESULT_WINDOW = { hasMore: false, nextLimit: null } as const;
const LibraryView = dynamic(() => import("@/components/LibraryView"), {
  loading: () => (
    <div className="films-page films-loading" role="status">
      Loading films…
    </div>
  ),
});

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

const SEARCH_EXAMPLES = [
  "sunlight through trees",
  "embracing on a beach",
  "quietly unsettling",
  "centered wide shot",
] as const;

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
  const [resultStreamKey, setResultStreamKey] = useState(0);
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
  const [resultWindow, setResultWindow] = useState<{
    hasMore: boolean;
    nextLimit: number | null;
  }>(EMPTY_RESULT_WINDOW);
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
      limit?: number,
    ) => {
      const clauses = buildRecipeClauses(mainText, drafts, image);
      const isDeepening = limit !== undefined;
      if (clauses.length === 0) {
        cancelPendingScopeSearch();
        searchAbortRef.current?.abort();
        searchAbortRef.current = null;
        setLoading(false);
        setError(null);
        setRecipeNotice(null);
        setSourceEvidenceByFacet({});
        setResultWindow(EMPTY_RESULT_WINDOW);
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
      if (!isDeepening) {
        setResultStreamKey((current) => current + 1);
        setResultWindow(EMPTY_RESULT_WINDOW);
        setSourceEvidenceByFacet({});
        setHasCompletedSearch(false);
      }

      const request: SearchRecipeRequest = {
        clauses,
        ...(scope.length ? { film_ids: [...scope] } : {}),
        ...(limit !== undefined ? { limit } : {}),
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
        setResultWindow({
          hasMore: data.has_more,
          nextLimit: data.next_limit,
        });
        setSourceEvidenceByFacet(
          Object.fromEntries(
            (data.source_evidence ?? []).map((evidence) => [
              evidence.facet,
              evidence,
            ]),
          ) as Partial<Record<RecipeMatchFacet, ResolvedSourceEvidence>>,
        );
        setHasCompletedSearch(true);
      } catch (reason) {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "Search failed");
        if (!isDeepening) {
          setResults([]);
          setSourceEvidenceByFacet({});
        }
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
      setResultWindow(EMPTY_RESULT_WINDOW);
      setRecipeNotice(null);
      setHasCompletedSearch(false);
    },
    [
      cancelPendingScopeSearch,
      handleRecipeLimit,
      mainImage,
      matchDrafts,
      query,
    ],
  );

  const handleFacetTextCommit = useCallback(
    (facet: TextMatchFacet, text: string) => {
      const normalizedText = text.trim();
      const nextDrafts: MatchDrafts = { ...matchDrafts };
      if (normalizedText) {
        nextDrafts[facet] = {
          kind: "text",
          facet,
          text: normalizedText,
        };
      } else {
        delete nextDrafts[facet];
      }
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
      const replacingImage = mainImage?.facet === facet;
      const nextImage = replacingImage ? null : mainImage;
      const replacingClause = matchDraftHasClause(baseDrafts[facet]);
      if (
        !replacingClause &&
        recipeClauseCount(query, baseDrafts, nextImage) >= MAX_RECIPE_CLAUSES
      ) {
        handleRecipeLimit();
        return;
      }

      const nextDrafts: MatchDrafts = {
        ...baseDrafts,
        [facet]: { ...draft, facet },
      };
      if (replacingImage) {
        mainImageRef.current = null;
        setMainImage(null);
      }
      setMatchDrafts(nextDrafts);
      setRecipeNotice(null);
      setHasCompletedSearch(false);
      setActiveTab("search");
      setActiveShot(null);
      void runRecipe(query, nextDrafts, selectedFilmIds, nextImage);
      if (replacingImage) revokeImageInput(mainImage);
    },
    [
      handleRecipeLimit,
      mainImage,
      matchDrafts,
      query,
      runRecipe,
      selectedFilmIds,
    ],
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
      setResultWindow(EMPTY_RESULT_WINDOW);
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
      setResultWindow(EMPTY_RESULT_WINDOW);
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
    setResultWindow(EMPTY_RESULT_WINDOW);
    setActiveShot(null);
    window.requestAnimationFrame(() => inputRef.current?.focus());
  }, [
    cancelPendingScopeSearch,
    facetSourceSearch,
    speech,
  ]);

  const handleMainImageFile = useCallback(
    (file: File, facet: RecipeImageFacet = "look") => {
      if (sourceReferenceFacet) return;
      speech.cancel();
      if (!isSupportedRecipeImage(file)) {
        setError("Use a JPEG, PNG, or WebP reference image.");
        return;
      }

      const nextDrafts = { ...matchDrafts };
      delete nextDrafts[facet];
      if (recipeClauseCount(query, nextDrafts) >= MAX_RECIPE_CLAUSES) {
        setRecipeNotice("Remove one match before adding an image.");
        return;
      }

      const nextImage: RecipeImageInput = {
        file,
        facet,
        display: {
          label: file.name || "Uploaded frame",
          previewUrl: URL.createObjectURL(file),
        },
      };
      const previousImage = mainImageRef.current;
      mainImageRef.current = nextImage;
      setMainImage(nextImage);
      setMatchDrafts(nextDrafts);
      revokeImageInput(previousImage);
      setSourceEvidenceByFacet({});
      setError(null);
      setRecipeNotice(null);
      setHasCompletedSearch(false);
      setActiveTab("search");
      setActiveShot(null);
      void runRecipe(query, nextDrafts, selectedFilmIds, nextImage);
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

  const handleMoveMainImage = useCallback(
    (facet: RecipeImageFacet) => {
      const previousImage = mainImageRef.current;
      if (!previousImage || previousImage.facet === facet) return;
      const nextDrafts = { ...matchDrafts };
      delete nextDrafts[facet];
      const nextImage = { ...previousImage, facet };
      mainImageRef.current = nextImage;
      setMainImage(nextImage);
      setMatchDrafts(nextDrafts);
      setSourceEvidenceByFacet({});
      setRecipeNotice(null);
      setHasCompletedSearch(false);
      void runRecipe(query, nextDrafts, selectedFilmIds, nextImage);
    },
    [matchDrafts, query, runRecipe, selectedFilmIds],
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
      setResultWindow(EMPTY_RESULT_WINDOW);
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

  const handleLoadMoreResults = useCallback(() => {
    if (loading || resultWindow.nextLimit === null) return;
    void runRecipe(
      query,
      matchDrafts,
      selectedFilmIds,
      mainImageRef.current,
      resultWindow.nextLimit,
    );
  }, [
    loading,
    matchDrafts,
    query,
    resultWindow.nextLimit,
    runRecipe,
    selectedFilmIds,
  ]);

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
  const hasFacetDrafts = Object.keys(matchDrafts).length > 0;
  const showSearchExamples = Boolean(
    isHome &&
      !query.trim() &&
      !hasFacetDrafts &&
      !mainImage &&
      !sourceReferenceFacet &&
      speech.status === "idle" &&
      !speech.error,
  );
  const disabledUseFacets = useMemo(
    () =>
      clauseCount < MAX_RECIPE_CLAUSES
        ? new Set<RecipeMatchFacet>()
        : new Set(
            MATCH_FACETS.filter(
              (facet) =>
                !matchDraftHasClause(matchDrafts[facet]) &&
                mainImage?.facet !== facet,
            ),
          ),
    [clauseCount, mainImage, matchDrafts],
  );
  const recipeOverLimit = clauseCount > MAX_RECIPE_CLAUSES;
  const searchDisabled = sourceReferenceFacet
    ? facetSourceSearch.loading || !facetSourceSearch.query.trim()
    : loading ||
      recipeOverLimit ||
      buildRecipeClauses(query, matchDrafts, mainImage).length === 0;
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
  const activeResultCount = sourceReferenceFacet
    ? facetSourceSearch.results.length
    : results.length;
  const activeError = sourceReferenceFacet ? facetSourceSearch.error : error;
  const hasNoResults = sourceReferenceFacet
    ? facetSourceSearch.results.length === 0 && facetSourceSearch.hasSearched
    : results.length === 0 && hasCompletedSearch;
  const imageSearchLabel =
    mainImage?.facet === "look"
      ? "Replace Look reference image"
      : "Add a reference image to Look";
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
        if (
          event.target instanceof Element &&
          event.target.closest(".match-tile")
        ) {
          event.preventDefault();
          event.dataTransfer.dropEffect = "none";
          return;
        }
        event.preventDefault();
        event.dataTransfer.dropEffect = sourceReferenceFacet ? "none" : "copy";
      }}
      onDrop={(event) => {
        if (!containsFile(event.dataTransfer)) return;
        if (
          event.target instanceof Element &&
          event.target.closest(".search-bar-shell")
        ) {
          return;
        }
        if (
          event.target instanceof Element &&
          event.target.closest(".match-tile")
        ) {
          event.preventDefault();
          return;
        }
        event.preventDefault();
        setMainImageDragOver(false);
        if (sourceReferenceFacet) return;
        const file = event.dataTransfer.files.item(0);
        if (file) handleMainImageFile(file, "look");
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
          <div className={`search-hero${isHome ? " is-home" : ""}`}>
            {/* wordmark */}
            <button
              type="button"
              className={`search-wordmark${isHome ? " is-home" : ""}`}
              onClick={resetSearchHome}
              aria-label="Return to Scene Recall home"
              title="Home"
            >
              scene-recall
            </button>

            {/* One stable workspace for both recipes and scene references. */}
            <form
              className="search-workspace-form"
              onSubmit={handleSubmit}
            >
              <div
                className={[
                  "search-bar-shell",
                  isHome ? "is-home" : "",
                  speech.isSupported ? "has-voice" : "",
                  sourceReferenceFacet ? "is-reference-mode" : "",
                  mainImageDragOver ? "is-image-drag-over" : "",
                ]
                  .filter(Boolean)
                  .join(" ")}
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
                  if (file) handleMainImageFile(file, "look");
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
                <input
                  ref={inputRef}
                  className={`search-main-input${
                    sourceReferenceFacet ? " is-source-reference-input" : ""
                  }`}
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
                      : "what are you dreaming of…"
                  }
                  aria-label={
                    sourceReferenceFacet
                      ? `Find a scene for ${FACET_LABELS[sourceReferenceFacet]}`
                      : "Describe a scene"
                  }
                  aria-describedby={voiceStatus ? voiceStatusId : undefined}
                  autoFocus
                />
                <input
                  ref={imageInputRef}
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  hidden
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    event.target.value = "";
                    if (file) handleMainImageFile(file, "look");
                  }}
                />
                <button
                  type="button"
                  className="image-search-button"
                  disabled={Boolean(sourceReferenceFacet)}
                  aria-hidden={Boolean(sourceReferenceFacet)}
                  tabIndex={sourceReferenceFacet ? -1 : 0}
                  aria-label={imageSearchLabel}
                  title={imageSearchLabel}
                  onClick={() => {
                    speech.cancel();
                    imageInputRef.current?.click();
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
                  className="search-submit-button"
                  disabled={searchDisabled}
                  aria-label="Search"
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

              <div className="search-meta">
                {showSearchExamples ? (
                  <div
                    className="search-examples"
                    aria-label="Example searches"
                  >
                    <span>Try</span>
                    {SEARCH_EXAMPLES.map((example) => (
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
                    ))}
                  </div>
                ) : (
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
                      <span
                        className="voice-search-status-dot"
                        aria-hidden="true"
                      />
                    )}
                    {voiceStatus}
                  </span>
                )}
                <div className="search-controls">
                  <MovieScopeFilter
                    selectedFilmIds={selectedFilmIds}
                    onChange={handleMovieScopeChange}
                  />
                  {activeResultCount > 0 && !sourceReferenceFacet && (
                    <SearchOptions
                      showRankingDetails={debug}
                      onShowRankingDetailsChange={setDebug}
                    />
                  )}
                </div>
              </div>

              <MatchByRail
                clauseCount={clauseCount}
                drafts={matchDrafts}
                image={mainImage}
                sourceEvidence={sourceEvidenceByFacet}
                debug={debug}
                onActivateText={handleActivateTextFacet}
                onTextChange={handleFacetTextChange}
                onCommitText={handleFacetTextCommit}
                onRemove={handleRemoveFacet}
                onImageFile={handleMainImageFile}
                onMoveImage={handleMoveMainImage}
                onRemoveImage={handleRemoveMainImage}
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
            </form>

            {/* error */}
            {activeError && (
              <p
                style={{
                  marginTop: "16px",
                  color: "#c0392b",
                  fontSize: "0.85rem",
                }}
              >
                {activeError}
              </p>
            )}

            {/* no results */}
            {!activeLoading &&
              !activeError &&
              speech.status === "idle" &&
              hasNoResults && (
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
              results={results}
              streamKey={resultStreamKey}
              revealDisabled={loading}
              hasMore={resultWindow.hasMore}
              onRequestMore={handleLoadMoreResults}
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
              results={facetSourceSearch.results}
              streamKey={facetSourceSearch.streamKey}
              revealDisabled={facetSourceSearch.loading}
              hasMore={facetSourceSearch.hasMore}
              onRequestMore={facetSourceSearch.loadMore}
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
