"use client";

import { useEffect, useRef, useState } from "react";
import type { SearchResult } from "@/types/api";
import styles from "./searchComparison.module.css";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
// Covers both the request and response body, with room for the server's 45s deadline.
const COMPARISON_TIMEOUT_MS = 50_000;
const TIMEOUT_MESSAGE = "Comparison took too long. Your ordinary results are still here; you can try again.";
type Variant = "normal" | "jev" | "fixed";
type Comparison = {
  variants: Record<Variant, { results: SearchResult[] }>;
  provider?: { status: string; reason?: string };
};

export default function SearchComparison({ query, filmIds, onResultsChange }: {
  query: string;
  filmIds: readonly string[];
  onResultsChange: (results: SearchResult[] | null) => void;
}) {
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [variant, setVariant] = useState<Variant>("normal");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  const scopeKey = JSON.stringify([...filmIds].sort());
  useEffect(() => {
    setComparison(null);
    setVariant("normal");
    setLoading(false);
    setError(null);
    return () => {
      request.current?.abort();
      request.current = null;
      // The parent keeps ordinary results while this temporary view is mounted.
      onResultsChange(null);
    };
  }, [query, scopeKey, onResultsChange]);

  function cancel() {
    request.current?.abort();
    request.current = null;
    setLoading(false);
    setError(null);
  }

  async function compare() {
    if (request.current) return;
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError(null);
    const timeout = setTimeout(() => {
      if (request.current !== controller) return;
      // Recover the UI even if a stalled transport never rejects after abort.
      request.current = null;
      controller.abort();
      setLoading(false);
      setError(TIMEOUT_MESSAGE);
    }, COMPARISON_TIMEOUT_MS);
    controller.signal.addEventListener("abort", () => clearTimeout(timeout), { once: true });
    try {
      const response = await fetch(`${API_URL}/search/intent/compare-hosted`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ q: query, film_ids: filmIds, limit: 200 }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(response.status === 504 ? TIMEOUT_MESSAGE
        : response.status === 429 ? "A comparison is still finishing. Try again shortly; your ordinary results are still here."
        : "Comparison unavailable. Your ordinary results are still here.");
      const data: Comparison = await response.json();
      if (controller.signal.aborted) return;
      if (!["normal", "jev", "fixed"].every((value) =>
        Array.isArray(data?.variants?.[value as Variant]?.results))) {
        throw new Error("Comparison unavailable. Your ordinary results are still here.");
      }
      setComparison(data);
      const usedJev = ["completed", "cached"].includes(data.provider?.status ?? "");
      const selected = usedJev ? "jev" : "normal";
      setVariant(selected);
      onResultsChange(data.variants[selected].results);
    } catch (reason) {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Comparison unavailable");
    } finally {
      clearTimeout(timeout);
      if (request.current === controller) {
        request.current = null;
        setLoading(false);
      }
    }
  }

  return (
    <div className={styles.root}>
      {comparison ? <>
        <div className={styles.switcher} role="group" aria-label="Compare search results">
          {(["normal", "jev", "fixed"] as const).map((value) => (
            <button key={value} type="button" aria-pressed={variant === value}
              onClick={() => { setVariant(value); onResultsChange(comparison.variants[value].results); }}>
              {value === "normal" ? "Ordinary" : value === "jev" ? "Jev guided" : "Fixed expansion"}
            </button>
          ))}
        </div>
        <span className={styles.note} role="status">
          {["completed", "cached"].includes(comparison.provider?.status ?? "")
            ? "Same query · same library snapshot"
            : comparison.provider?.reason === "interpretation_deadline"
              ? "Jev took too long — guided results use ordinary search"
              : "Jev unavailable — guided results use ordinary search"}
        </span>
        <button type="button" className={styles.close} aria-label="Close comparison"
          onClick={() => { setComparison(null); onResultsChange(null); }}>×</button>
      </> : <>
        <button type="button" className={styles.start} disabled={loading} onClick={() => void compare()}>
          {loading ? "Comparing…" : "Compare Jev"}
        </button>
        {loading && <button type="button" className={styles.cancel} onClick={cancel}>Cancel</button>}
        <span className={styles.note} role="status">{loading ? "Searching the library · your results stay available" : "Experimental"}</span>
      </>}
      {error && <span className={styles.error} role="status">{error}</span>}
    </div>
  );
}
