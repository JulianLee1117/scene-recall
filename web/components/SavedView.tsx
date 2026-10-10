"use client";

import { useEffect, useRef, useState, type CSSProperties, type ReactNode, type RefObject } from "react";
import ShotCard from "./ShotCard";
import BookmarkIcon from "./BookmarkIcon";
import ArrangeMenu from "./ArrangeMenu";
import { rowStarts, useJustifiedRows } from "@/hooks/useJustifiedRows";
import { useBoardReorder } from "@/hooks/useBoardReorder";
import { arrangeBoard, hasUserOrder, moveItem } from "@/lib/boardOrder";
import { BOARD_SCALE, DEFAULT_BOARD, loadBoardPrefs, saveBoardPrefs, type BoardPrefs } from "@/lib/boardPrefs";
import type {
  BookmarkRecord,
  RecipeMatchFacet,
  SearchResult,
} from "@/types/api";
import { displayTitle, filmLabel, formatTime } from "@/lib/format";
import chrome from "./pageChrome.module.css";
import styles from "./savedView.module.css";

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
  /** Store the board's order, first to last. Resolves to whether it held. */
  onReorder: (bookmarkIds: string[]) => Promise<boolean>;
}

type LearnAspect = (scene: SearchResult, width: number, height: number) => void;

/** A move starts on the scene's picture, never on the controls over it. */
const isCardHandle = (target: Element) => Boolean(target.closest(".result-card-primary"));

/** One justified board: the plain board, or one film's section. */
function Board({ items, scale, canReorder, onMove, renderTile }: {
  items: BookmarkRecord[];
  scale: number;
  canReorder: boolean;
  onMove: (from: number, to: number) => void;
  renderTile: (bookmark: BookmarkRecord, index: number, learnAspect: LearnAspect) => ReactNode;
}) {
  // Unavailable bookmarks have no scene; they take a default 16:9 tile.
  const scenes = items.map((bookmark) => bookmark.scene ?? { unit_id: bookmark.bookmark_id, film_id: bookmark.film_id });
  const { ref, frame, layout, tileStyle, learnAspect } = useJustifiedRows(scenes, "medium", scale);
  const { gesture, tileProps } = useBoardReorder({ enabled: canReorder, count: items.length, onMove, isHandle: isCardHandle });
  const starts = layout ? rowStarts(layout.sizes) : [];
  const tileClass = (index: number) => [
    "result-grid-item",
    styles.tile,
    gesture?.from === index ? styles.lifted : "",
    gesture?.over === index ? (gesture.before ? styles.dropBefore : styles.dropAfter) : "",
  ].filter(Boolean).join(" ");

  if (!layout) {
    return (
      <ol ref={ref as RefObject<HTMLOListElement>} className="result-grid saved-grid" aria-label="Saved scenes">
        {items.map((bookmark, index) => (
          <li className="result-grid-item" key={bookmark.bookmark_id} style={tileStyle(scenes[index])}>
            {renderTile(bookmark, index, learnAspect)}
          </li>
        ))}
      </ol>
    );
  }
  return (
    <div
      ref={ref as RefObject<HTMLDivElement>}
      role="list"
      className={`result-grid saved-grid is-rows${gesture ? ` ${styles.reordering}` : ""}`}
      style={{ "--row-h": `${frame?.rowHeight}px` } as CSSProperties}
      aria-label="Saved scenes"
    >
      {layout.sizes.map((rowSize, row) => {
        const first = starts[row];
        const short = layout.lastIsShort && row === layout.sizes.length - 1;
        return (
          <div key={items[first].bookmark_id} role="presentation" className={`result-row${short ? " is-short" : ""}`}>
            {items.slice(first, first + rowSize).map((bookmark, offset) => {
              const index = first + offset;
              return (
                <div role="listitem" key={bookmark.bookmark_id} className={tileClass(index)} style={tileStyle(scenes[index])} {...tileProps(index)}>
                  {renderTile(bookmark, index, learnAspect)}
                </div>
              );
            })}
          </div>
        );
      })}
    </div>
  );
}

/** The slider's ends: a small frame and a larger one. */
function SizeGlyph({ large }: { large: boolean }) {
  const size = large ? 14 : 9;
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <rect x={(16 - size) / 2} y={(16 - size) / 2} width={size} height={size} rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
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
  onReorder,
}: SavedViewProps) {
  const [prefs, setPrefs] = useState<BoardPrefs>(DEFAULT_BOARD);
  useEffect(() => {
    setPrefs(loadBoardPrefs());
  }, []);
  const changePrefs = (change: Partial<BoardPrefs>) =>
    setPrefs((current) => {
      const next = { ...current, ...change };
      saveBoardPrefs(next);
      return next;
    });

  // Heard, not seen: the move is visible on the board.
  const [spoken, setSpoken] = useState("");
  const spokenTimer = useRef<number | null>(null);
  useEffect(() => () => {
    if (spokenTimer.current !== null) window.clearTimeout(spokenTimer.current);
  }, []);

  const groups = arrangeBoard(bookmarks, prefs.arrangement);
  const move = (from: number, to: number) => {
    const next = moveItem(bookmarks, from, to);
    if (next === bookmarks) return;
    const moved = bookmarks[from].bookmark_id;
    void onReorder(next.map((bookmark) => bookmark.bookmark_id)).then((held) => {
      if (!held) return;
      setSpoken(`Moved to ${next.findIndex((bookmark) => bookmark.bookmark_id === moved) + 1} of ${next.length}`);
      if (spokenTimer.current !== null) window.clearTimeout(spokenTimer.current);
      spokenTimer.current = window.setTimeout(() => setSpoken(""), 3000);
    });
  };

  const tile = (bookmark: BookmarkRecord, index: number, learnAspect: LearnAspect) =>
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

  return (
    <section className={`${chrome.page} ${chrome.workspace} ${styles.page}`} aria-labelledby="saved-heading">
      <header className={`${chrome.header} ${chrome.headerRow} ${styles.header}`}>
        <div className={styles.heading}>
          <h1 id="saved-heading" className={chrome.title}>Saved scenes</h1>
          {!loading && (
            <span className={chrome.count}>
              {bookmarks.length} {bookmarks.length === 1 ? "scene" : "scenes"}
            </span>
          )}
        </div>
        {!loading && bookmarks.length > 0 && (
          <div className={styles.tools}>
            <ArrangeMenu
              value={prefs.arrangement}
              placed={hasUserOrder(bookmarks)}
              onChange={(arrangement) => changePrefs({ arrangement })}
            />
            <div className={styles.size}>
              <SizeGlyph large={false} />
              <input
                type="range"
                min={BOARD_SCALE.min}
                max={BOARD_SCALE.max}
                step={BOARD_SCALE.step}
                value={prefs.scale}
                aria-label="Scene size"
                onChange={(event) => changePrefs({ scale: Number(event.target.value) })}
              />
              <SizeGlyph large />
            </div>
          </div>
        )}
      </header>

      {error && (
        <p className={chrome.error} role="status">
          {error}
        </p>
      )}
      <p className={styles.spoken} role="status" aria-live="polite">{spoken}</p>

      {loading ? (
        <p className={chrome.empty} role="status">
          Loading saved scenes…
        </p>
      ) : bookmarks.length === 0 ? (
        <div className={chrome.empty}>
          <BookmarkIcon filled={false} size={25} />
          <p>Save a scene from Search using the bookmark button. It will appear here.</p>
        </div>
      ) : (
        groups.map((group) => (
          <section key={group.key} className={styles.group} aria-label={group.title ?? "Saved scenes"}>
            {group.title && (
              <h2 className={styles.groupTitle}>
                {group.title}
                <span>{group.items.length}</span>
              </h2>
            )}
            <Board
              items={group.items}
              scale={prefs.scale}
              canReorder={prefs.arrangement === "yours" && group.items.length > 1}
              onMove={move}
              renderTile={tile}
            />
          </section>
        ))
      )}
    </section>
  );
}
