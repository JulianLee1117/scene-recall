"use client";

import { useEffect, useState } from "react";
import type { ShotFacet } from "@/lib/shotFilters";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

/** The server's shot filters (fixed per API version), fetched once per page. */
export function useShotFacets(): ShotFacet[] {
  const [facets, setFacets] = useState<ShotFacet[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    fetch(`${API_URL}/search/shot-facets`, { signal: controller.signal })
      .then((response) => (response.ok ? response.json() : []))
      .then((value: unknown) => {
        if (Array.isArray(value) && !controller.signal.aborted) setFacets(value as ShotFacet[]);
      })
      .catch(() => {
        // Without the vocabulary the menu simply offers film filters.
      });
    return () => controller.abort();
  }, []);
  return facets;
}
