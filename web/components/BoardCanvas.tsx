"use client";

import { memo, useEffect, useLayoutEffect, useMemo, useRef, useState, type Ref } from "react";
import ShotCard from "./ShotCard";
import { useFrameAspects } from "@/hooks/useFrameAspects";
import { BOARD_INDEX_ATTRIBUTE, useBoardReorder } from "@/hooks/useBoardReorder";
import { layoutBoard, placeAt, type BoardBlock } from "@/lib/boardLayout";
import { moveItem, type BoardGroup } from "@/lib/boardOrder";
import { cssAspect, rowHeightFor } from "@/lib/justifiedRows";
import { displayTitle, filmLabel, formatTime } from "@/lib/format";
import type { BookmarkRecord, RecipeMatchFacet, SearchResult } from "@/types/api";
import styles from "./boardCanvas.module.css";

/** What a tile needs to show its scene and offer its actions. */
export interface SceneActions {
  pendingUnitIds: ReadonlySet<string>;
  onShotClick: (shot: SearchResult) => void;
  onUseInSearch: (shot: SearchResult, facet: RecipeMatchFacet) => void;
  disabledUseFacets?: ReadonlySet<RecipeMatchFacet>;
  onToggleBookmark: (shot: SearchResult) => void;
  onRemoveBookmark: (bookmark: BookmarkRecord) => void;
}

type LearnAspect = (scene: SearchResult, width: number, height: number) => void;

/** A move starts on the scene's picture, never on the controls over it. */
const isCardHandle = (target: Element) => Boolean(target.closest(".result-card-primary"));
const sceneOf = (bookmark: BookmarkRecord) =>
  bookmark.scene ?? { unit_id: bookmark.bookmark_id, film_id: bookmark.film_id };

/**
 * One scene on the board, memoized on primitives: during a drag only the
 * tiles whose place changes render again.
 */
const Tile = memo(function Tile({
  ref, bookmark, index, x, y, width, height, lifted, transform, tileProps, learnAspect, actions,
}: {
  ref?: Ref<HTMLDivElement>;
  bookmark: BookmarkRecord;
  index: number;
  x: number;
  y: number;
  width: number;
  height: number;
  lifted: boolean;
  /** The lifted tile's own transform, under the pointer. */
  transform?: string;
  tileProps: ReturnType<typeof useBoardReorder>["tileProps"];
  learnAspect: LearnAspect;
  actions: SceneActions;
}) {
  return (
    <div
      ref={ref}
      role="listitem"
      data-id={bookmark.bookmark_id}
      {...{ [BOARD_INDEX_ATTRIBUTE]: index }}
      className={`result-grid-item ${styles.tile}${lifted ? ` ${styles.lifted}` : ""}`}
      style={{ width, height, transform: lifted && transform ? transform : `translate(${x}px, ${y}px)` }}
      {...tileProps}
    >
      {bookmark.scene ? (
        <ShotCard
          shot={bookmark.scene}
          onFrameLoad={learnAspect}
          position={index + 1}
          showRank={false}
          allowSourceDrag={false}
          showDetails={false}
          onClick={actions.onShotClick}
          onUseInSearch={actions.onUseInSearch}
          disabledUseFacets={actions.disabledUseFacets}
          onToggleBookmark={actions.onToggleBookmark}
          bookmarked
          bookmarkDisabled={
            actions.pendingUnitIds.has(bookmark.source_unit_id) ||
            actions.pendingUnitIds.has(bookmark.scene.unit_id)
          }
        />
      ) : (
        <article className="saved-unavailable">
          <div>
            <span>Scene unavailable</span>
            <strong>{displayTitle(bookmark.film_title || filmLabel(bookmark.film_id))}</strong>
            <span>{formatTime(bookmark.evidence_timestamp)}</span>
          </div>
          <button
            type="button"
            onClick={() => actions.onRemoveBookmark(bookmark)}
            disabled={actions.pendingUnitIds.has(bookmark.source_unit_id)}
          >
            Remove
          </button>
        </article>
      )}
    </div>
  );
});

/**
 * The board as one canvas. Every scene is one element placed by a transform
 * from one layout, so it glides wherever it goes next: out of the way of a
 * lifted scene, into its new place on a drop, or into its film's group when
 * the arrangement changes. Tiles keep a stable element order, since moving
 * a lifted tile's element would drop the pointer capture.
 */
export default function BoardCanvas({ groups, scale, canReorder, onReorder, actions }: {
  groups: BoardGroup[];
  /** Multiplies the medium row height. */
  scale: number;
  canReorder: boolean;
  /** The board's new order, first to last, and the scene that moved. */
  onReorder: (bookmarkIds: string[], movedId: string) => void;
  actions: SceneActions;
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
      {layout && items.map((bookmark, index) => {
        const place = layout.tiles.get(bookmark.bookmark_id);
        if (!place) return null;
        const isLifted = bookmark.bookmark_id === liftedId;
        return (
          <Tile
            key={bookmark.bookmark_id}
            ref={isLifted ? floatRef : undefined}
            bookmark={bookmark}
            index={index}
            x={place.x}
            y={place.y}
            width={place.width}
            height={place.height}
            lifted={isLifted}
            transform={isLifted ? floatTransform.current : undefined}
            tileProps={tileProps}
            learnAspect={learnAspect}
            actions={actions}
          />
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
