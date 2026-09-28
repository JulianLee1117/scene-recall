"use client";

import { useEffect, useState } from "react";
import type { LibraryFilm } from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

/** One catalog per search surface; suggestions never make per-keystroke requests. */
export function useSearchFilms(enabled = true): LibraryFilm[] {
  const [films, setFilms] = useState<LibraryFilm[]>([]);

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let pending = false;
    const load = async () => {
      if (pending || controller.signal.aborted) return;
      pending = true;
      try {
        const response = await fetch(`${API_URL}/library`, { signal: controller.signal });
        if (!response.ok) return;
        const catalog: unknown = await response.json();
        if (Array.isArray(catalog) && !controller.signal.aborted) setFilms(catalog);
      } catch {
        // Keep the last catalog and ordinary search usable during an outage.
      } finally {
        pending = false;
      }
    };
    const refresh = () => {
      if (document.visibilityState === "visible") void load();
    };
    void load();
    const interval = window.setInterval(refresh, 15_000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      controller.abort();
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", refresh);
    };
  }, [enabled]);

  return films;
}
