"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import ShotCard from "./ShotCard";
import BookmarkIcon from "./BookmarkIcon";
import ArrangeMenu from "./ArrangeMenu";
import { useFrameAspects } from "@/hooks/useFrameAspects";
import { useBoardReorder } from "@/hooks/useBoardReorder";
import { layoutBoard, placeAt, type BoardBlock } from "@/lib/boardLayout";
import { arrangeBoard, hasUserOrder, moveItem, type BoardGroup } from "@/lib/boardOrder";
import { BOARD_SCALE, DEFAULT_BOARD, loadBoardPrefs, saveBoardPrefs, type BoardPrefs } from "@/lib/boardPrefs";
import { cssAspect, rowHeightFor } from "@/lib/justifiedRows";
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
const sceneOf = (bookmark: BookmarkRecord) =>
  bookmark.scene ?? { unit_id: bookmark.bookmark_id, film_id: bookmark.film_id };

/**
 * The board as one canvas. Every scene is one element placed by a transform
 * from one layout, so it glides wherever it goes next: out of the way of a
 * lifted scene, into its new place on a drop, or into its film's group when
 * the arrangement changes.
 */
function BoardCanvas({ groups, scale, canReorder, onReorder, renderTile }: {
  groups: BoardGroup[];
  scale: number;
  canReorder: boolean;
  onReorder: (bookmarkIds: string[], movedId: string) => void;
  renderTile: (bookmark: BookmarkRecord, index: number, learnAspect: LearnAspect) => ReactNode;
}) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const [frame, setFrame] = useState<{ width: number; rowHeight: number } | null>(null);
  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const measure = () => {
      const width = canvas.getBoundingClientRect().width;
      if (width === 0) return;
      const next = { width, rowHeight: Math.round(rowHeightFor(window.innerWidth, "medium") * scale) };
      setFrame((current) =>
        current && current.width === next.width && current.rowHeight === next.rowHeight ? current : next);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(canvas);
    window.addEventListener("resize", measure);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [scale]);

  const { aspectOf, learnAspect } = useFrameAspects();
  const items = useMemo(() => groups.flatMap((group) => group.items), [groups]);
  const byId = useMemo(() => new Map(items.map((bookmark) => [bookmark.bookmark_id, bookmark])), [items]);
  // While a scene is lifted, the sequence it is being carried through.
  const [tentative, setTentative] = useState<string[] | null>(null);
  const tileBlock = (bookmark: BookmarkRecord): BoardBlock =>
    ({ kind: "tile", id: bookmark.bookmark_id, aspect: cssAspect(aspectOf(sceneOf(bookmark))) });
  const blocks: BoardBlock[] = tentative
    ? tentative.map((id) => tileBlock(byId.get(id) as BookmarkRecord))
    : groups.flatMap((group) => [
      ...(group.title ? [{ kind: "heading", key: group.key } as BoardBlock] : []),
      ...group.items.map(tileBlock),
    ]);
  const layout = frame ? layoutBoard(blocks, frame.width, frame.rowHeight) : null;

  // The lifted scene follows the pointer by its own transform, set without a render.
  const grab = useRef<{ id: string; dx: number; dy: number; x: number; y: number } | null>(null);
  const floatTransform = useRef("");
  const floatRef = useRef<HTMLDivElement | null>(null);
  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  const orderRef = useRef<string[]>([]);
  orderRef.current = tentative ?? items.map((bookmark) => bookmark.bookmark_id);
  const canvasOrigin = () => {
    const rect = canvasRef.current?.getBoundingClientRect();
    return { left: rect?.left ?? 0, top: rect?.top ?? 0 };
  };
  const dragTo = (x: number, y: number) => {
    const current = grab.current;
    const now = layoutRef.current;
    if (!current || !now) return;
    current.x = x;
    current.y = y;
    const { left, top } = canvasOrigin();
    floatTransform.current = `translate(${x - left - current.dx}px, ${y - top - current.dy}px)`;
    if (floatRef.current) floatRef.current.style.transform = floatTransform.current;
    const order = orderRef.current;
    const at = placeAt(now, order, current.id, x - left, y - top);
    if (at === null) return;
    const next = moveItem(order, order.indexOf(current.id), at);
    if (next === order) return;
    orderRef.current = next;
    setTentative(next);
  };
  const dragRef = useRef(dragTo);
  dragRef.current = dragTo;

  const { lifted, tileProps } = useBoardReorder({
    enabled: canReorder,
    isHandle: isCardHandle,
    onLift(index, x, y) {
      const id = items[index].bookmark_id;
      const place = layoutRef.current?.tiles.get(id);
      const { left, top } = canvasOrigin();
      grab.current = { id, dx: x - left - (place?.x ?? 0), dy: y - top - (place?.y ?? 0), x, y };
      floatTransform.current = place ? `translate(${place.x}px, ${place.y}px)` : "";
      setTentative(items.map((bookmark) => bookmark.bookmark_id));
    },
    onDrag: dragTo,
    onDrop() {
      const current = grab.current;
      const order = orderRef.current;
      grab.current = null;
      if (current && order.some((id, index) => id !== items[index]?.bookmark_id)) onReorder(order, current.id);
      setTentative(null);
    },
    onCancel() {
      grab.current = null;
      setTentative(null);
    },
    onKeyMove(index, direction) {
      const next = moveItem(items, index, direction < 0 ? index - 1 : index + 2);
      if (next !== items) onReorder(next.map((bookmark) => bookmark.bookmark_id), items[index].bookmark_id);
    },
  });
  // The page may scroll under a lifted scene; it keeps following the pointer.
  useEffect(() => {
    if (lifted === null) return;
    const follow = () => {
      const current = grab.current;
      if (current) dragRef.current(current.x, current.y);
    };
    window.addEventListener("scroll", follow, { passive: true });
    return () => window.removeEventListener("scroll", follow);
  }, [lifted]);

  const liftedId = lifted === null ? null : items[lifted]?.bookmark_id ?? null;
  const liftedPlace = liftedId && layout ? layout.tiles.get(liftedId) : undefined;

  return (
    <div
      ref={canvasRef}
      role="list"
      aria-label="Saved scenes"
      className={`${styles.canvas}${liftedId ? ` ${styles.reordering}` : ""}`}
      style={{ height: layout?.height ?? 0 }}
    >
      {layout && !tentative && groups.map((group) => group.title && (
        <h2 key={group.key} className={styles.groupTitle} style={{ transform: `translateY(${layout.headings.get(group.key) ?? 0}px)` }}>
          {group.title}
          <span>{group.items.length}</span>
        </h2>
      ))}
      {/* Tiles stay in a stable element order and move by their transforms:
          moving a lifted tile's element would drop the pointer capture. */}
      {layout && items.map((bookmark, index) => {
        const place = layout.tiles.get(bookmark.bookmark_id);
        if (!place) return null;
        const isLifted = bookmark.bookmark_id === liftedId;
        return (
          <div
            key={bookmark.bookmark_id}
            ref={isLifted ? floatRef : undefined}
            role="listitem"
            data-id={bookmark.bookmark_id}
            className={`result-grid-item ${styles.tile}${isLifted ? ` ${styles.lifted}` : ""}`}
            style={{
              width: place.width,
              height: place.height,
              transform: isLifted ? floatTransform.current : `translate(${place.x}px, ${place.y}px)`,
            }}
            {...tileProps(index)}
          >
            {renderTile(bookmark, index, learnAspect)}
          </div>
        );
      })}
      {liftedPlace && (
        <div
          className={styles.slot}
          aria-hidden="true"
          style={{ width: liftedPlace.width, height: liftedPlace.height, transform: `translate(${liftedPlace.x}px, ${liftedPlace.y}px)` }}
        />
      )}
    </div>
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
  const reorder = (bookmarkIds: string[], movedId: string) => {
    void onReorder(bookmarkIds).then((held) => {
      if (!held) return;
      setSpoken(`Moved to ${bookmarkIds.indexOf(movedId) + 1} of ${bookmarkIds.length}`);
      if (spokenTimer.current !== null) window.clearTimeout(spokenTimer.current);
      spokenTimer.current = window.setTimeout(() => setSpoken(""), 3000);
    });
  };

  const groups = arrangeBoard(bookmarks, prefs.arrangement);

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
        <BoardCanvas
          groups={groups}
          scale={prefs.scale}
          canReorder={prefs.arrangement === "yours" && bookmarks.length > 1}
          onReorder={reorder}
          renderTile={tile}
        />
      )}
    </section>
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
