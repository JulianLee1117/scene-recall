"use client";

import ShotCard from "./ShotCard";
import type {
  BookmarkRecord,
  RecipeMatchFacet,
  SearchResult,
} from "@/types/api";
import { filmLabel, formatTime } from "@/lib/format";

interface SavedViewProps {
  bookmarks: BookmarkRecord[];
  loading: boolean;
  error: string | null;
  pendingUnitIds: ReadonlySet<string>;
  onShotClick: (shot: SearchResult) => void;
  onFindSimilar: (shot: SearchResult) => void;
  onUseInSearch: (shot: SearchResult, facet: RecipeMatchFacet) => void;
  onToggleBookmark: (shot: SearchResult) => void;
  onRemoveBookmark: (bookmark: BookmarkRecord) => void;
}

export default function SavedView({
  bookmarks,
  loading,
  error,
  pendingUnitIds,
  onShotClick,
  onFindSimilar,
  onUseInSearch,
  onToggleBookmark,
  onRemoveBookmark,
}: SavedViewProps) {
  return (
    <section className="saved-view" aria-labelledby="saved-heading">
      <header className="saved-heading">
        <div>
          <p>Collection</p>
          <h1 id="saved-heading">Saved scenes</h1>
        </div>
        {!loading && (
          <span>
            {bookmarks.length} {bookmarks.length === 1 ? "scene" : "scenes"}
          </span>
        )}
      </header>

      {error && (
        <p className="saved-error" role="status">
          {error}
        </p>
      )}

      {loading ? (
        <p className="saved-empty" role="status">
          Loading saved scenes…
        </p>
      ) : bookmarks.length === 0 ? (
        <div className="saved-empty">
          <svg
            width="25"
            height="25"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M6 4.75A1.75 1.75 0 0 1 7.75 3h8.5A1.75 1.75 0 0 1 18 4.75V21l-6-3.75L6 21V4.75Z" />
          </svg>
          <p>Scenes you bookmark will appear here.</p>
        </div>
      ) : (
        <ol className="result-grid saved-grid" aria-label="Saved scenes">
          {bookmarks.map((bookmark, index) =>
            bookmark.scene ? (
              <li className="result-grid-item" key={bookmark.bookmark_id}>
                <ShotCard
                  shot={bookmark.scene}
                  position={index + 1}
                  showRank={false}
                  debug={false}
                  onClick={onShotClick}
                  onFindSimilar={onFindSimilar}
                  onUseInSearch={onUseInSearch}
                  onToggleBookmark={onToggleBookmark}
                  bookmarked
                  bookmarkDisabled={
                    pendingUnitIds.has(bookmark.source_unit_id) ||
                    pendingUnitIds.has(bookmark.scene.unit_id)
                  }
                />
              </li>
            ) : (
              <li className="result-grid-item" key={bookmark.bookmark_id}>
                <article className="saved-unavailable">
                  <div>
                    <span>Scene unavailable</span>
                    <strong>
                      {bookmark.film_title || filmLabel(bookmark.film_id)}
                    </strong>
                    <span>{formatTime(bookmark.evidence_timestamp)}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => onRemoveBookmark(bookmark)}
                    disabled={pendingUnitIds.has(bookmark.source_unit_id)}
                  >
                    Remove
                  </button>
                </article>
              </li>
            ),
          )}
        </ol>
      )}
    </section>
  );
}
