"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  BookmarkRecord,
  BookmarkResponse,
  SearchResult,
} from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

async function apiError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string };
    if (typeof body.detail === "string" && body.detail) return body.detail;
  } catch {
    // Keep the status-based fallback for non-JSON errors.
  }
  return `Bookmark request failed (${response.status})`;
}

function unitIdsFor(bookmark: BookmarkRecord): string[] {
  const ids = [bookmark.source_unit_id];
  if (bookmark.scene?.unit_id) ids.push(bookmark.scene.unit_id);
  return ids;
}

export function useBookmarks() {
  const [bookmarks, setBookmarks] = useState<BookmarkRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingUnitIds, setPendingUnitIds] = useState<Set<string>>(new Set());
  const bookmarksRef = useRef(bookmarks);

  const commitBookmarks = useCallback((next: BookmarkRecord[]) => {
    bookmarksRef.current = next;
    setBookmarks(next);
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    void fetch(`${API_URL}/bookmarks`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error(await apiError(response));
        return (await response.json()) as BookmarkResponse;
      })
      .then((response) => {
        commitBookmarks(response.bookmarks);
        setError(null);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "Could not load Saved");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [commitBookmarks]);

  const bookmarkByUnit = useMemo(() => {
    const byUnit = new Map<string, BookmarkRecord>();
    bookmarks.forEach((bookmark) => {
      unitIdsFor(bookmark).forEach((unitId) => byUnit.set(unitId, bookmark));
    });
    return byUnit;
  }, [bookmarks]);

  const markPending = useCallback((unitId: string, pending: boolean) => {
    setPendingUnitIds((current) => {
      const next = new Set(current);
      if (pending) next.add(unitId);
      else next.delete(unitId);
      return next;
    });
  }, []);

  const removeBookmark = useCallback(
    async (bookmark: BookmarkRecord) => {
      const before = bookmarksRef.current;
      const affectedIds = unitIdsFor(bookmark);
      affectedIds.forEach((unitId) => markPending(unitId, true));
      commitBookmarks(before.filter((item) => item.bookmark_id !== bookmark.bookmark_id));
      setError(null);

      try {
        const response = await fetch(
          `${API_URL}/bookmarks/${encodeURIComponent(bookmark.bookmark_id)}`,
          { method: "DELETE" },
        );
        if (!response.ok) throw new Error(await apiError(response));
      } catch (reason) {
        commitBookmarks(before);
        setError(reason instanceof Error ? reason.message : "Could not remove bookmark");
      } finally {
        affectedIds.forEach((unitId) => markPending(unitId, false));
      }
    },
    [commitBookmarks, markPending],
  );

  const toggleBookmark = useCallback(
    async (shot: SearchResult) => {
      const existing = bookmarksRef.current.find((bookmark) =>
        unitIdsFor(bookmark).includes(shot.unit_id),
      );
      if (existing) {
        await removeBookmark(existing);
        return;
      }

      const evidenceTimestamp = shot.matched_frame_timestamp ?? shot.t_start;
      const temporaryId = `pending:${shot.unit_id}`;
      const temporary: BookmarkRecord = {
        bookmark_id: temporaryId,
        film_id: shot.film_id,
        film_title: shot.film_title ?? shot.film_id,
        source_unit_id: shot.unit_id,
        evidence_timestamp: evidenceTimestamp,
        frame_index: shot.matched_frame_index ?? null,
        created_at: new Date().toISOString(),
        availability: "indexed",
        scene: shot,
      };
      const before = bookmarksRef.current;
      markPending(shot.unit_id, true);
      commitBookmarks([temporary, ...before]);
      setError(null);

      try {
        const response = await fetch(
          `${API_URL}/bookmarks/${encodeURIComponent(shot.unit_id)}`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              evidence_timestamp: evidenceTimestamp,
              frame_index: shot.matched_frame_index ?? null,
            }),
          },
        );
        if (!response.ok) throw new Error(await apiError(response));
        const saved = (await response.json()) as BookmarkRecord;
        commitBookmarks(
          bookmarksRef.current.map((bookmark) =>
            bookmark.bookmark_id === temporaryId ? saved : bookmark,
          ),
        );
      } catch (reason) {
        commitBookmarks(before);
        setError(reason instanceof Error ? reason.message : "Could not save bookmark");
      } finally {
        markPending(shot.unit_id, false);
      }
    },
    [commitBookmarks, markPending, removeBookmark],
  );

  return {
    bookmarks,
    bookmarkByUnit,
    pendingUnitIds,
    loading,
    error,
    toggleBookmark,
    removeBookmark,
  };
}
