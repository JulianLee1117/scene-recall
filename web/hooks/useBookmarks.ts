"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  BookmarkRecord,
  BookmarkResponse,
  SearchResult,
} from "@/types/api";
import { APP_CLIENT_HEADERS } from "@/lib/appClient";
import { bookmarkAnchor } from "@/lib/resultMoment";

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
          { method: "DELETE", headers: APP_CLIENT_HEADERS },
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

      const { evidence_timestamp: evidenceTimestamp, frame_index: frameIndex } = bookmarkAnchor(shot);
      const temporaryId = `pending:${shot.unit_id}`;
      const temporary: BookmarkRecord = {
        bookmark_id: temporaryId,
        film_id: shot.film_id,
        film_title: shot.film_title ?? shot.film_id,
        source_unit_id: shot.unit_id,
        evidence_timestamp: evidenceTimestamp,
        frame_index: frameIndex,
        created_at: new Date().toISOString(),
        position: null,
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
            headers: { ...APP_CLIENT_HEADERS, "Content-Type": "application/json" },
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

  /**
   * Store the board's order: the listed bookmarks in this sequence, with
   * anything unlisted kept ahead of them as the server keeps it. An empty
   * list clears the order, so the board is newest first again. The board
   * shows the new order at once and goes back if the request fails.
   */
  const reorderBookmarks = useCallback(
    async (bookmarkIds: string[]): Promise<boolean> => {
      const previous = bookmarksRef.current;
      const byId = new Map(previous.map((bookmark) => [bookmark.bookmark_id, bookmark]));
      const listed = bookmarkIds
        .map((id) => byId.get(id))
        .filter((bookmark): bookmark is BookmarkRecord => Boolean(bookmark));
      const unlisted = previous.filter((bookmark) => !bookmarkIds.includes(bookmark.bookmark_id));
      commitBookmarks(
        bookmarkIds.length
          ? [...unlisted, ...listed]
          : [...previous].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)),
      );
      setError(null);
      try {
        const response = await fetch(`${API_URL}/bookmarks/order`, {
          method: "POST",
          headers: { ...APP_CLIENT_HEADERS, "Content-Type": "application/json" },
          body: JSON.stringify({ bookmark_ids: bookmarkIds }),
        });
        if (!response.ok) throw new Error(await apiError(response));
        const result = (await response.json()) as BookmarkResponse;
        // A save still in flight stays on top, where the server will list it.
        const pending = bookmarksRef.current.filter((bookmark) => bookmark.bookmark_id.startsWith("pending:"));
        commitBookmarks([...pending, ...result.bookmarks]);
        return true;
      } catch (reason) {
        commitBookmarks(previous);
        setError(reason instanceof Error ? reason.message : "Could not reorder");
        return false;
      }
    },
    [commitBookmarks],
  );

  return {
    bookmarks,
    bookmarkByUnit,
    pendingUnitIds,
    loading,
    error,
    toggleBookmark,
    removeBookmark,
    reorderBookmarks,
  };
}
