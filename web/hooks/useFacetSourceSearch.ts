"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { ResultGrouping } from "@/components/ResultGrid";
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
  const [grouping, setGrouping] = useState<ResultGrouping>("all");
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
    setGrouping("all");
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
    },
    [clearRequest],
  );

  const search = useCallback(
    async (
      scope: readonly string[] = selectedFilmIds,
      nextQuery: string = query,
    ) => {
      const text = nextQuery.trim();
      if (!text) return;

      clearRequest();
      const controller = new AbortController();
      abortRef.current = controller;
      setLoading(true);
      setError(null);
      setHasSearched(false);
      setResults([]);

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
        setGrouping("all");
        setHasSearched(true);
      } catch (reason) {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "Search failed");
        setResults([]);
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

  return {
    facet,
    query,
    results,
    loading,
    error,
    hasSearched,
    grouping,
    setGrouping,
    open,
    close,
    setQuery,
    search,
  };
}
