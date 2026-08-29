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

function bookmarkTimestamp(shot: SearchResult): number {
  if (typeof shot.matched_frame_timestamp === "number") {
    return shot.matched_frame_timestamp;
  }

  const matchedFrameTimestamp = shot.matches?.find(
    (match) =>
      match.evidence?.type === "frame" &&
      typeof match.evidence.timestamp === "number",
  )?.evidence;
  if (
    matchedFrameTimestamp?.type === "frame" &&
    typeof matchedFrameTimestamp.timestamp === "number"
  ) {
    return matchedFrameTimestamp.timestamp;
  }

  if (typeof shot.evidence_timestamp === "number") {
    return shot.evidence_timestamp;
  }
  return shot.t_start + (shot.t_end - shot.t_start) / 2;
}

function bookmarkFrameIndex(shot: SearchResult): number | null {
  if (Number.isInteger(shot.keyframe_index)) return shot.keyframe_index;
  if (Number.isInteger(shot.matched_frame_index)) {
    return shot.matched_frame_index ?? null;
  }
  return null;
}

export function useBookmarks() {
  const [bookmarks, setBookmarks] = useState<BookmarkRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingUnitIds, setPendingUnitIds] = useState<Set<string>>(new Set());
  const bookmarksRef = useRef(bookmarks);
  const pendingUnitIdsRef = useRef<Set<string>>(new Set());

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

  const markPending = useCallback((unitIds: readonly string[], pending: boolean) => {
    const next = new Set(pendingUnitIdsRef.current);
    unitIds.forEach((unitId) => {
      if (pending) next.add(unitId);
      else next.delete(unitId);
    });
    pendingUnitIdsRef.current = next;
    setPendingUnitIds(next);
  }, []);

  const removeBookmark = useCallback(
    async (bookmark: BookmarkRecord) => {
      const affectedIds = unitIdsFor(bookmark);
      if (affectedIds.some((unitId) => pendingUnitIdsRef.current.has(unitId))) {
        return;
      }

      const current = bookmarksRef.current;
      const removedIndex = current.findIndex(
        (item) => item.bookmark_id === bookmark.bookmark_id,
      );
      if (removedIndex < 0) return;
      const removed = current[removedIndex];

      markPending(affectedIds, true);
      commitBookmarks(
        current.filter((item) => item.bookmark_id !== bookmark.bookmark_id),
      );
      setError(null);

      try {
        const response = await fetch(
          `${API_URL}/bookmarks/${encodeURIComponent(bookmark.bookmark_id)}`,
          { method: "DELETE" },
        );
        // DELETE is idempotent from the user's perspective. A prior request
        // may have committed even if its response was lost.
        if (!response.ok && response.status !== 404) {
          throw new Error(await apiError(response));
        }
      } catch (reason) {
        const live = bookmarksRef.current;
        if (!live.some((item) => item.bookmark_id === removed.bookmark_id)) {
          const restored = [...live];
          restored.splice(Math.min(removedIndex, restored.length), 0, removed);
          commitBookmarks(restored);
        }
        setError(reason instanceof Error ? reason.message : "Could not remove bookmark");
      } finally {
        markPending(affectedIds, false);
      }
    },
    [commitBookmarks, markPending],
  );

  const toggleBookmark = useCallback(
    async (shot: SearchResult) => {
      if (pendingUnitIdsRef.current.has(shot.unit_id)) return;

      const existing = bookmarksRef.current.find((bookmark) =>
        unitIdsFor(bookmark).includes(shot.unit_id),
      );
      if (existing) {
        await removeBookmark(existing);
        return;
      }

      const evidenceTimestamp = bookmarkTimestamp(shot);
      const frameIndex = bookmarkFrameIndex(shot);
      const temporaryId = `pending:${shot.unit_id}`;
      const temporary: BookmarkRecord = {
        bookmark_id: temporaryId,
        film_id: shot.film_id,
        film_title: shot.film_title ?? shot.film_id,
        source_unit_id: shot.unit_id,
        evidence_timestamp: evidenceTimestamp,
        frame_index: frameIndex,
        created_at: new Date().toISOString(),
        availability: "indexed",
        scene: shot,
      };
      markPending([shot.unit_id], true);
      commitBookmarks([temporary, ...bookmarksRef.current]);
      setError(null);

      try {
        const response = await fetch(
          `${API_URL}/bookmarks/${encodeURIComponent(shot.unit_id)}`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              evidence_timestamp: evidenceTimestamp,
              frame_index: frameIndex,
            }),
          },
        );
        if (!response.ok) throw new Error(await apiError(response));
        const saved = (await response.json()) as BookmarkRecord;
        const live = bookmarksRef.current;
        const temporaryIndex = live.findIndex(
          (bookmark) => bookmark.bookmark_id === temporaryId,
        );
        if (temporaryIndex >= 0) {
          const next = [...live];
          next[temporaryIndex] = saved;
          commitBookmarks(next);
        } else if (!live.some((bookmark) => bookmark.bookmark_id === saved.bookmark_id)) {
          commitBookmarks([saved, ...live]);
        }
      } catch (reason) {
        commitBookmarks(
          bookmarksRef.current.filter(
            (bookmark) => bookmark.bookmark_id !== temporaryId,
          ),
        );
        setError(reason instanceof Error ? reason.message : "Could not save bookmark");
      } finally {
        markPending([shot.unit_id], false);
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
