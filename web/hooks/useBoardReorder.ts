"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";

/** A lifted tile and the place it would take if dropped now. */
export interface ReorderGesture {
  from: number;
  /** The tile under the pointer, and whether the drop goes before it. */
  over: number | null;
  before: boolean;
}

const MOUSE_SLOP = 6;
const TOUCH_SLOP = 10;
const TOUCH_HOLD_MS = 280;
const EDGE = 72;
const EDGE_STEP = 14;
const CLICK_SUPPRESS_MS = 300;

type Place = { over: number; before: boolean };

/**
 * Reordering a list of tiles by pointer. A mouse or pen lifts a tile after a
 * short drag; a finger holds it for a moment first, so a swipe still scrolls.
 * The lifted tile follows the pointer and the drop place is reported for the
 * view to mark. For keyboards, Alt with the left or right arrow moves a tile
 * one place. `onMove` receives the item's index and the insertion index in
 * the current list, as `moveItem` takes them.
 */
export function useBoardReorder({ enabled, count, onMove, isHandle }: {
  enabled: boolean;
  count: number;
  onMove: (from: number, to: number) => void;
  /** Whether a press on this element may start a move; controls inside a tile should not. */
  isHandle?: (target: Element) => boolean;
}) {
  const tiles = useRef<(HTMLElement | null)[]>([]);
  const gesture = useRef<{
    pointer: number;
    type: string;
    index: number;
    x: number;
    y: number;
    active: boolean;
    hold: number | null;
    element: HTMLElement;
    place: Place | null;
  } | null>(null);
  const suppressUntil = useRef(0);
  const [state, setState] = useState<ReorderGesture | null>(null);

  useEffect(() => {
    tiles.current.length = count;
  }, [count]);

  const preventScroll = useCallback((event: TouchEvent) => {
    if (gesture.current?.active) event.preventDefault();
  }, []);

  const clear = useCallback(() => {
    const current = gesture.current;
    if (!current) return;
    if (current.hold !== null) window.clearTimeout(current.hold);
    current.element.style.transform = "";
    if (current.element.hasPointerCapture(current.pointer)) current.element.releasePointerCapture(current.pointer);
    document.removeEventListener("touchmove", preventScroll);
    gesture.current = null;
    setState(null);
  }, [preventScroll]);

  useEffect(
    () => () => {
      const current = gesture.current;
      if (!current) return;
      if (current.hold !== null) window.clearTimeout(current.hold);
      document.removeEventListener("touchmove", preventScroll);
      gesture.current = null;
    },
    [preventScroll],
  );

  useEffect(() => {
    const cancel = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !gesture.current?.active) return;
      event.preventDefault();
      suppressUntil.current = performance.now() + CLICK_SUPPRESS_MS;
      clear();
    };
    document.addEventListener("keydown", cancel);
    return () => document.removeEventListener("keydown", cancel);
  }, [clear]);

  const lift = useCallback(() => {
    const current = gesture.current;
    if (!current || current.active) return;
    current.active = true;
    current.hold = null;
    if (current.type === "touch") document.addEventListener("touchmove", preventScroll, { passive: false });
    setState({ from: current.index, over: null, before: false });
  }, [preventScroll]);

  /**
   * The tile under the pointer, or the nearest one, and which side of it the
   * pointer is on. The lifted tile travels with the pointer, so it is never
   * the one under it.
   */
  const locate = (x: number, y: number, lifted: number): Place | null => {
    const entries = tiles.current
      .map((tile, index) => (tile && index !== lifted ? { index, rect: tile.getBoundingClientRect() } : null))
      .filter((entry): entry is { index: number; rect: DOMRect } => entry !== null);
    if (!entries.length) return null;
    let row = entries.filter(({ rect }) => y >= rect.top && y <= rect.bottom);
    if (!row.length) {
      const nearest = entries.reduce(
        (best, entry) => {
          const distance = y < entry.rect.top ? entry.rect.top - y : y - entry.rect.bottom;
          return distance < best.distance ? { distance, top: entry.rect.top } : best;
        },
        { distance: Infinity, top: 0 },
      );
      row = entries.filter(({ rect }) => rect.top === nearest.top);
    }
    const target =
      row.find(({ rect }) => x >= rect.left && x <= rect.right) ??
      row.reduce(
        (best, entry) => {
          const distance = x < entry.rect.left ? entry.rect.left - x : x - entry.rect.right;
          return distance < best.distance ? { distance, entry } : best;
        },
        { distance: Infinity, entry: row[0] },
      ).entry;
    return { over: target.index, before: x < target.rect.left + target.rect.width / 2 };
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLElement>) => {
    const current = gesture.current;
    if (!current || current.pointer !== event.pointerId) return;
    const dx = event.clientX - current.x;
    const dy = event.clientY - current.y;
    if (!current.active) {
      const moved = Math.hypot(dx, dy);
      if (current.type === "touch") {
        // A finger that moves before the hold is scrolling, not lifting.
        if (moved > TOUCH_SLOP) clear();
        return;
      }
      if (moved < MOUSE_SLOP) return;
      lift();
    }
    event.preventDefault();
    current.element.style.transform = `translate(${dx}px, ${dy}px)`;
    const place = locate(event.clientX, event.clientY, current.index);
    if (place?.over !== current.place?.over || place?.before !== current.place?.before) {
      current.place = place;
      setState({ from: current.index, over: place?.over ?? null, before: place?.before ?? false });
    }
    if (event.clientY < EDGE) window.scrollBy(0, -EDGE_STEP);
    else if (event.clientY > window.innerHeight - EDGE) window.scrollBy(0, EDGE_STEP);
  };

  const onPointerUp = (event: ReactPointerEvent<HTMLElement>) => {
    const current = gesture.current;
    if (!current || current.pointer !== event.pointerId) return;
    if (current.active) {
      event.preventDefault();
      suppressUntil.current = performance.now() + CLICK_SUPPRESS_MS;
      const place = current.place;
      if (place) onMove(current.index, place.before ? place.over : place.over + 1);
    }
    clear();
  };

  const onPointerCancel = (event: ReactPointerEvent<HTMLElement>) => {
    if (gesture.current?.pointer === event.pointerId) clear();
  };

  const onClickCapture = (event: ReactMouseEvent<HTMLElement>) => {
    if (performance.now() < suppressUntil.current) {
      event.preventDefault();
      event.stopPropagation();
    }
  };

  const tileProps = (index: number) => ({
    ref: (element: HTMLElement | null) => {
      tiles.current[index] = element;
    },
    onPointerDown(event: ReactPointerEvent<HTMLElement>) {
      if (!enabled || gesture.current || event.button !== 0 || !event.isPrimary) return;
      if (isHandle && !isHandle(event.target as Element)) return;
      const element = event.currentTarget;
      gesture.current = {
        pointer: event.pointerId,
        type: event.pointerType,
        index,
        x: event.clientX,
        y: event.clientY,
        active: false,
        hold: event.pointerType === "touch" ? window.setTimeout(lift, TOUCH_HOLD_MS) : null,
        element,
        place: null,
      };
      element.setPointerCapture(event.pointerId);
    },
    onPointerMove,
    onPointerUp,
    onPointerCancel,
    onLostPointerCapture: onPointerCancel,
    onClickCapture,
    onKeyDown(event: ReactKeyboardEvent<HTMLElement>) {
      if (!enabled || !event.altKey) return;
      if (event.key === "ArrowLeft" && index > 0) {
        event.preventDefault();
        onMove(index, index - 1);
      } else if (event.key === "ArrowRight" && index < count - 1) {
        event.preventDefault();
        onMove(index, index + 2);
      }
    },
  });

  return { gesture: state, tileProps };
}
