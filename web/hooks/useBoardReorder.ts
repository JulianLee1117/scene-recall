"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
} from "react";

const MOUSE_SLOP = 6;
const TOUCH_SLOP = 10;
const TOUCH_HOLD_MS = 280;
const EDGE = 72;
const EDGE_STEP = 14;
const CLICK_SUPPRESS_MS = 300;

/** The attribute a tile carries so one set of handlers serves every tile. */
export const BOARD_INDEX_ATTRIBUTE = "data-board-index";

/**
 * The gesture that lifts a tile and carries it: a mouse or pen lifts after a
 * short drag, a finger holds for a moment first so a swipe still scrolls,
 * Escape puts it back, and the page scrolls when the pointer nears an edge.
 * Where the tile goes is the board's business: it hears the lift with the
 * pointer, every move, and the drop. Alt with the left or right arrow moves a
 * tile one place for keyboards. The handlers are one stable object for every
 * tile, which reads its index from the tile's attribute, so tiles can be
 * memoized and a drag re-renders only the tiles that move.
 */
export function useBoardReorder({ enabled, isHandle, onLift, onDrag, onDrop, onCancel, onKeyMove }: {
  enabled: boolean;
  /** Whether a press on this element may start a move; controls inside a tile should not. */
  isHandle?: (target: Element) => boolean;
  onLift: (index: number, x: number, y: number) => void;
  onDrag: (x: number, y: number) => void;
  onDrop: () => void;
  onCancel: () => void;
  onKeyMove: (index: number, direction: -1 | 1) => void;
}) {
  const latest = useRef({ onLift, onDrag, onDrop, onCancel, onKeyMove });
  latest.current = { onLift, onDrag, onDrop, onCancel, onKeyMove };
  const gesture = useRef<{
    pointer: number;
    type: string;
    index: number;
    x: number;
    y: number;
    active: boolean;
    hold: number | null;
    element: HTMLElement;
  } | null>(null);
  const suppressUntil = useRef(0);
  const [lifted, setLifted] = useState<number | null>(null);

  const preventScroll = useCallback((event: TouchEvent) => {
    if (gesture.current?.active) event.preventDefault();
  }, []);

  const clear = useCallback(() => {
    const current = gesture.current;
    if (!current) return;
    if (current.hold !== null) window.clearTimeout(current.hold);
    if (current.element.hasPointerCapture(current.pointer)) current.element.releasePointerCapture(current.pointer);
    document.removeEventListener("touchmove", preventScroll);
    // The press focused the tile's button, as a click would; after a lift that
    // focus would keep the scene's controls showing, so the tile lets it go.
    const focused = document.activeElement as HTMLElement | null;
    if (current.active && focused && current.element.contains(focused)) focused.blur();
    gesture.current = null;
    setLifted(null);
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
      latest.current.onCancel();
      clear();
    };
    document.addEventListener("keydown", cancel);
    return () => document.removeEventListener("keydown", cancel);
  }, [clear]);

  const lift = useCallback((x: number, y: number) => {
    const current = gesture.current;
    if (!current || current.active) return;
    current.active = true;
    current.hold = null;
    // Captured only now. Capturing at the press would make the browser deliver
    // a plain click to the tile instead of the scene's own button under it.
    current.element.setPointerCapture(current.pointer);
    if (current.type === "touch") document.addEventListener("touchmove", preventScroll, { passive: false });
    setLifted(current.index);
    latest.current.onLift(current.index, x, y);
  }, [preventScroll]);

  const tileProps = useMemo(() => {
    const indexOf = (element: HTMLElement) => Number(element.getAttribute(BOARD_INDEX_ATTRIBUTE));
    const cancel = (event: ReactPointerEvent<HTMLElement>) => {
      const current = gesture.current;
      if (current?.pointer !== event.pointerId) return;
      if (current.active) latest.current.onCancel();
      clear();
    };
    return {
      onPointerDown(event: ReactPointerEvent<HTMLElement>) {
        if (!enabled || gesture.current || event.button !== 0 || !event.isPrimary) return;
        if (isHandle && !isHandle(event.target as Element)) return;
        const element = event.currentTarget;
        const index = indexOf(element);
        if (!Number.isInteger(index)) return;
        const { clientX, clientY } = event;
        gesture.current = {
          pointer: event.pointerId,
          type: event.pointerType,
          index,
          x: clientX,
          y: clientY,
          active: false,
          hold: event.pointerType === "touch" ? window.setTimeout(() => lift(clientX, clientY), TOUCH_HOLD_MS) : null,
          element,
        };
      },
      onPointerMove(event: ReactPointerEvent<HTMLElement>) {
        const current = gesture.current;
        if (!current || current.pointer !== event.pointerId) return;
        if (!current.active) {
          const moved = Math.hypot(event.clientX - current.x, event.clientY - current.y);
          if (current.type === "touch") {
            // A finger that moves before the hold is scrolling, not lifting.
            if (moved > TOUCH_SLOP) clear();
            return;
          }
          if (moved < MOUSE_SLOP) return;
          lift(event.clientX, event.clientY);
        }
        event.preventDefault();
        latest.current.onDrag(event.clientX, event.clientY);
        if (event.clientY < EDGE) window.scrollBy(0, -EDGE_STEP);
        else if (event.clientY > window.innerHeight - EDGE) window.scrollBy(0, EDGE_STEP);
      },
      onPointerUp(event: ReactPointerEvent<HTMLElement>) {
        const current = gesture.current;
        if (!current || current.pointer !== event.pointerId) return;
        if (current.active) {
          event.preventDefault();
          suppressUntil.current = performance.now() + CLICK_SUPPRESS_MS;
          latest.current.onDrop();
        }
        clear();
      },
      onPointerCancel: cancel,
      onLostPointerCapture: cancel,
      onClickCapture(event: ReactMouseEvent<HTMLElement>) {
        if (performance.now() < suppressUntil.current) {
          event.preventDefault();
          event.stopPropagation();
        }
      },
      onKeyDown(event: ReactKeyboardEvent<HTMLElement>) {
        if (!enabled || !event.altKey) return;
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        const index = indexOf(event.currentTarget);
        if (!Number.isInteger(index)) return;
        event.preventDefault();
        latest.current.onKeyMove(index, event.key === "ArrowLeft" ? -1 : 1);
      },
    };
  }, [enabled, isHandle, lift, clear]);

  return { lifted, tileProps };
}
