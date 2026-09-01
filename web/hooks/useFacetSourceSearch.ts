"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  RecipeMatchFacet,
  SearchRecipeRequest,
  SearchRecipeResponse,
  SearchResult,
} from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

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

/**
 * Owns the temporary, broad search used to find a scene for one recipe facet.
 * Its query, request identity, results, and view state never enter the recipe.
 */
export function useFacetSourceSearch(selectedFilmIds: readonly string[]) {
  const [facet, setFacet] = useState<RecipeMatchFacet | null>(null);
  const [query, setQueryState] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasSearched, setHasSearched] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [nextLimit, setNextLimit] = useState<number | null>(null);
  const [streamKey, setStreamKey] = useState(0);
  const abortRef = useRef<AbortController | null>(null);

  const clearRequest = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setLoading(false);
  }, []);

  const resetSearch = useCallback(() => {
    clearRequest();
    setQueryState("");
    setResults([]);
    setError(null);
    setHasSearched(false);
    setHasMore(false);
    setNextLimit(null);
  }, [clearRequest]);

  const open = useCallback(
    (nextFacet: RecipeMatchFacet) => {
      if (facet === null) resetSearch();
      setFacet(nextFacet);
    },
    [facet, resetSearch],
  );

  const close = useCallback(() => {
    setFacet(null);
    resetSearch();
  }, [resetSearch]);

  const setQuery = useCallback(
    (nextQuery: string) => {
      clearRequest();
      setQueryState(nextQuery);
      setResults([]);
      setError(null);
      setHasSearched(false);
      setHasMore(false);
      setNextLimit(null);
    },
    [clearRequest],
  );

  const search = useCallback(
    async (
      scope: readonly string[] = selectedFilmIds,
      nextQuery: string = query,
      limit?: number,
    ) => {
      const text = nextQuery.trim();
      if (!text) return;
      const isDeepening = limit !== undefined;

      clearRequest();
      const controller = new AbortController();
      abortRef.current = controller;
      setLoading(true);
      setError(null);
      if (!isDeepening) {
        setStreamKey((current) => current + 1);
        setHasSearched(false);
        setResults([]);
        setHasMore(false);
        setNextLimit(null);
      }

      const request: SearchRecipeRequest = {
        clauses: [
          {
            id: "facet-source",
            kind: "text",
            facet: "all",
            text,
          },
        ],
        ...(scope.length ? { film_ids: [...scope] } : {}),
        ...(limit !== undefined ? { limit } : {}),
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
        if (abortRef.current !== controller) return;
        setResults(data.results);
        setHasMore(data.has_more);
        setNextLimit(data.next_limit);
        setHasSearched(true);
      } catch (reason) {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "Search failed");
        if (!isDeepening) setResults([]);
        setHasSearched(true);
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null;
          setLoading(false);
        }
      }
    },
    [clearRequest, query, selectedFilmIds],
  );

  useEffect(() => () => abortRef.current?.abort(), []);

  const loadMore = useCallback(() => {
    if (loading || nextLimit === null) return;
    void search(selectedFilmIds, query, nextLimit);
  }, [loading, nextLimit, query, search, selectedFilmIds]);

  return {
    facet,
    query,
    results,
    loading,
    error,
    hasSearched,
    hasMore,
    streamKey,
    open,
    close,
    setQuery,
    search,
    loadMore,
  };
}
