"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import FacetIcon from "./FacetIcon";
import MatchByRail from "./MatchByRail";
import ResultGrid, { type ResultGrouping } from "./ResultGrid";
import { FACET_LABELS, type MatchDrafts } from "@/lib/searchRecipe";
import { bestResultPerFilm } from "@/lib/searchResults";
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

interface SourcePickerProps {
  targetFacet: RecipeMatchFacet;
  mainText: string;
  drafts: MatchDrafts;
  selectedFilmIds: readonly string[];
  onCancel: () => void;
  onChoose: (shot: SearchResult) => void;
  onPreview: (shot: SearchResult) => void;
}

export default function SourcePicker({
  targetFacet,
  mainText,
  drafts,
  selectedFilmIds,
  onCancel,
  onChoose,
  onPreview,
}: SourcePickerProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasSearched, setHasSearched] = useState(false);
  const [grouping, setGrouping] = useState<ResultGrouping>("all");
  const abortRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const targetLabel = FACET_LABELS[targetFacet];
  const displayedResults = useMemo(
    () => (grouping === "best-per-movie" ? bestResultPerFilm(results) : results),
    [grouping, results],
  );

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => inputRef.current?.focus());
    return () => {
      window.cancelAnimationFrame(frame);
      abortRef.current?.abort();
    };
  }, []);

  const runSearch = useCallback(async () => {
    const text = query.trim();
    if (!text || loading) return;

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    setHasSearched(false);
    setResults([]);

    const request: SearchRecipeRequest = {
      clauses: [
        {
          id: "source-picker",
          kind: "text",
          facet: "all",
          text,
        },
      ],
      ...(selectedFilmIds.length
        ? { film_ids: [...selectedFilmIds] }
        : {}),
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
  }, [loading, query, selectedFilmIds]);

  const cancel = () => {
    abortRef.current?.abort();
    abortRef.current = null;
    onCancel();
  };

  return (
    <section
      className="source-picker"
      aria-labelledby="source-picker-title"
      onKeyDown={(event) => {
        if (event.key !== "Escape") return;
        event.preventDefault();
        cancel();
      }}
    >
      <div className="source-picker-panel">
        <header className="source-picker-header">
          <button type="button" className="source-picker-back" onClick={cancel}>
            <svg
              width="15"
              height="15"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="m15 18-6-6 6-6" />
            </svg>
            Back
          </button>
          <div className="source-picker-heading">
            <span>Independent source search</span>
            <h2 id="source-picker-title">
              <FacetIcon facet={targetFacet} size={18} />
              Choose a scene for {targetLabel}
            </h2>
          </div>
          <button
            type="button"
            className="source-picker-cancel"
            onClick={cancel}
          >
            Cancel
          </button>
        </header>

        <p className="source-picker-explanation">
          Find a reference without applying your current matches. Choosing one
          returns to your recipe and searches with it.
        </p>

        <form
          className="source-picker-form"
          onSubmit={(event) => {
            event.preventDefault();
            void runSearch();
          }}
        >
          <FacetIcon facet="scene" size={17} />
          <input
            ref={inputRef}
            type="text"
            value={query}
            maxLength={500}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={`Find a scene to use for ${targetLabel}…`}
            aria-label={`Find a scene to use for ${targetLabel}`}
          />
          <span className="source-picker-scope">
            {selectedFilmIds.length
              ? `${selectedFilmIds.length} selected ${selectedFilmIds.length === 1 ? "movie" : "movies"}`
              : "All movies"}
          </span>
          <button type="submit" disabled={loading || !query.trim()}>
            {loading ? "Searching…" : "Search"}
          </button>
        </form>

        <div className="source-picker-recipe" aria-label="Paused current recipe">
          {mainText.trim() && (
            <div className="source-picker-main-query">
              <span>Broad search</span>
              <strong>{mainText}</strong>
            </div>
          )}
          <MatchByRail
            mainText={mainText}
            drafts={drafts}
            frozen
            label="Current recipe · paused"
            targetFacet={targetFacet}
          />
        </div>
      </div>

      {!loading && !error && !hasSearched && (
        <p className="source-picker-empty">
          Search for any scene, then choose <strong>Use for {targetLabel}</strong>.
        </p>
      )}
      {loading && (
        <p className="source-picker-empty" role="status">
          Searching scenes…
        </p>
      )}
      {error && (
        <p className="source-picker-error" role="alert">
          {error}
        </p>
      )}
      {!loading && !error && hasSearched && results.length === 0 && (
        <p className="source-picker-empty">No scenes found.</p>
      )}

      <ResultGrid
        results={displayedResults}
        grouping={grouping}
        onGroupingChange={setGrouping}
        revealDisabled={loading}
        onShotClick={onPreview}
        onUseInSearch={(shot) => onChoose(shot)}
        sourcePickerFacet={targetFacet}
        debug={false}
      />
    </section>
  );
}
