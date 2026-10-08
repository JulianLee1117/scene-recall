"use client";

import type { CSSProperties, RefObject } from "react";
import ShotCard from "./ShotCard";
import BookmarkIcon from "./BookmarkIcon";
import { rowStarts, useJustifiedRows } from "@/hooks/useJustifiedRows";
import type {
  BookmarkRecord,
  RecipeMatchFacet,
  SearchResult,
} from "@/types/api";
import { displayTitle, filmLabel, formatTime } from "@/lib/format";

interface SavedViewProps {
  bookmarks: BookmarkRecord[];
  loading: boolean;
  error: string | null;
  pendingUnitIds: ReadonlySet<string>;
  onShotClick: (shot: SearchResult) => void;
  onUseInSearch: (shot: SearchResult, facet: RecipeMatchFacet) => void;
  disabledUseFacets?: ReadonlySet<RecipeMatchFacet>;
  onToggleBookmark: (shot: SearchResult) => void;
  onRemoveBookmark: (bookmark: BookmarkRecord) => void;
}

export default function SavedView({
  bookmarks,
  loading,
  error,
  pendingUnitIds,
  onShotClick,
  onUseInSearch,
  disabledUseFacets,
  onToggleBookmark,
  onRemoveBookmark,
}: SavedViewProps) {
  // Unavailable bookmarks have no scene; they take a default 16:9 tile.
  const scenes = bookmarks.map((bookmark) => bookmark.scene ?? { unit_id: bookmark.bookmark_id, film_id: bookmark.film_id });
  const { ref, frame, layout, tileStyle, learnAspect } = useJustifiedRows(scenes);

  const tile = (bookmark: BookmarkRecord, index: number) =>
    bookmark.scene ? (
      <ShotCard
        shot={bookmark.scene}
        onFrameLoad={learnAspect}
        position={index + 1}
        showRank={false}
        allowSourceDrag={false}
        showDetails={false}
        onClick={onShotClick}
        onUseInSearch={onUseInSearch}
        disabledUseFacets={disabledUseFacets}
        onToggleBookmark={onToggleBookmark}
        bookmarked
        bookmarkDisabled={
          pendingUnitIds.has(bookmark.source_unit_id) ||
          pendingUnitIds.has(bookmark.scene.unit_id)
        }
      />
    ) : (
      <article className="saved-unavailable">
        <div>
          <span>Scene unavailable</span>
          <strong>
            {displayTitle(bookmark.film_title || filmLabel(bookmark.film_id))}
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
    );

  const starts = layout ? rowStarts(layout.sizes) : [];

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
          <BookmarkIcon filled={false} size={25} />
          <p>Scenes you bookmark will appear here.</p>
        </div>
      ) : layout ? (
        <div
          ref={ref as RefObject<HTMLDivElement>}
          role="list"
          className="result-grid saved-grid is-rows"
          style={{ "--row-h": `${frame?.rowHeight}px` } as CSSProperties}
          aria-label="Saved scenes"
        >
          {layout.sizes.map((rowSize, row) => {
            const first = starts[row];
            const short = layout.lastIsShort && row === layout.sizes.length - 1;
            return (
              <div key={bookmarks[first].bookmark_id} role="presentation" className={`result-row${short ? " is-short" : ""}`}>
                {bookmarks.slice(first, first + rowSize).map((bookmark, offset) => (
                  <div role="listitem" className="result-grid-item" key={bookmark.bookmark_id} style={tileStyle(scenes[first + offset])}>
                    {tile(bookmark, first + offset)}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      ) : (
        <ol ref={ref as RefObject<HTMLOListElement>} className="result-grid saved-grid" aria-label="Saved scenes">
          {bookmarks.map((bookmark, index) => (
            <li className="result-grid-item" key={bookmark.bookmark_id} style={tileStyle(scenes[index])}>
              {tile(bookmark, index)}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
