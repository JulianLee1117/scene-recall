"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { LAB_API, seconds } from "@/lib/lab";
import type { SearchRecipeResponse, SearchResult } from "@/types/api";
import styles from "./lab.module.css";

type Props = {
  filmIds: string[];
  onSelect: (result: SearchResult) => void;
  onClose: () => void;
  replacing: boolean;
  minimumDuration?: number;
  context?: "match";
};

export default function SourceBrowser({
  filmIds,
  onSelect,
  onClose,
  replacing,
  minimumDuration = 0,
  context,
}: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [nextLimit, setNextLimit] = useState<number | null>(null);
  const controller = useRef<AbortController | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const target = dialog.current;
    target?.showModal();
    return () => {
      controller.current?.abort();
      target?.close();
    };
  }, []);

  function changeQuery(value: string) {
    controller.current?.abort();
    setLoading(false);
    setQuery(value);
    setResults([]);
    setError("");
    setHasSearched(false);
    setNextLimit(null);
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void search();
  }

  async function search(limit?: number) {
    if (!query.trim()) return;
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setLoading(true);
    setError("");
    try {
      const response = await fetch(`${LAB_API}/search/recipe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: request.signal,
        body: JSON.stringify({
          clauses: [
            { id: "lab-source", kind: "text", facet: "all", text: query.trim() },
          ],
          ...(filmIds.length ? { film_ids: filmIds } : {}),
          ...(limit ? { limit } : {}),
        }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(
          typeof body?.detail === "string"
            ? body.detail
            : `Search failed (${response.status}).`,
        );
      }
      const body: SearchRecipeResponse = await response.json();
      if (request.signal.aborted) return;
      setResults(body.results);
      setNextLimit(body.next_limit);
      setHasSearched(true);
    } catch (reason) {
      if (!request.signal.aborted) {
        setError(reason instanceof Error ? reason.message : "Search failed.");
      }
    } finally {
      if (!request.signal.aborted) setLoading(false);
    }
  }

  return (
    <dialog
      className={styles.sourceDialog}
      ref={dialog}
      onCancel={onClose}
      aria-labelledby="source-browser-title"
    >
      <div className={styles.dialogHeader}>
        <div>
          {context !== "match" && <span className={styles.eyebrow}>Your film library</span>}
          <h2 id="source-browser-title">
            {context === "match" ? "Choose a reference scene" : replacing ? "Find a replacement" : "Find a moment"}
          </h2>
        </div>
        <button
          className={styles.iconButton}
          onClick={onClose}
          aria-label="Close source search"
        >
          ✕
        </button>
      </div>
      <form className={styles.searchForm} onSubmit={submitSearch}>
        <input
          autoFocus
          aria-label="Describe a scene"
          placeholder={context === "match" ? "Search scenes…" : "A face in warm light, a train disappearing…"}
          value={query}
          onChange={(event) => changeQuery(event.target.value)}
        />
        <button className={styles.primary} disabled={loading || !query.trim()}>
          {loading ? "Searching…" : "Search"}
        </button>
      </form>
      {error && (
        <p className={styles.error} role="alert">{error}</p>
      )}
      {minimumDuration > 0 && (
        <p className={styles.muted}>
          Choose a scene at least {Number(minimumDuration.toFixed(2))} s long to fill
          this section. To use a shorter scene, close search and move a cut on the timeline.
        </p>
      )}
      {!hasSearched && !loading && (
        <p className={styles.muted}>
          {context === "match" ? "Select a scene to start matching." : "Search by image, action, atmosphere, or a remembered moment. Choose a result, then audition and trim it in the editor."}
        </p>
      )}
      {hasSearched && !results.length && (
        <p className={styles.muted}>
          No scenes found. Try a broader description or change the film selection.
        </p>
      )}
      <div className={styles.sourceResults}>
        {results.map((result) => {
          const duration = result.t_end - result.t_start;
          const tooShort = duration < minimumDuration - 1e-6;

          return (
            <button
              key={result.unit_id}
              className={styles.sourceResult}
              disabled={tooShort}
              onClick={() => onSelect(result)}
            >
              <img
                src={`${LAB_API}${result.keyframe_url}`}
                alt={result.caption}
                loading="lazy"
              />
              <span>
                <strong>{result.film_title || result.film_id}</strong>
                <small>{seconds(result.t_start)} – {seconds(result.t_end)}</small>
                <p>{result.caption}</p>
                {tooShort && (
                  <small className={styles.sourceDurationWarning}>
                    Too short · {Number(duration.toFixed(2))} s; this section needs {Number(minimumDuration.toFixed(2))} s.
                  </small>
                )}
              </span>
              <b>{tooShort ? "Too short" : replacing ? "Replace" : "+ Add"}</b>
            </button>
          );
        })}
      </div>
      {nextLimit && (
        <button
          className={styles.secondary}
          disabled={loading}
          onClick={() => void search(nextLimit)}
        >
          Find more scenes
        </button>
      )}
    </dialog>
  );
}
