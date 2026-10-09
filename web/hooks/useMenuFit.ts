"use client";

import { useLayoutEffect, type RefObject } from "react";

/** Space kept between an open menu and the edges of the window. */
const MARGIN = 16;
/** Below this a menu overflows rather than shrinking to a sliver. */
const MIN_ROOM = 180;

/**
 * Keeps an open menu inside the window without moving the page: its height
 * is capped at the room below it (`--menu-room`), so a tall menu scrolls
 * inside, and a wide one shifts left (`--menu-nudge`) rather than past the
 * right edge.
 */
export function useMenuFit(open: boolean, menu: RefObject<HTMLElement | null>): void {
  useLayoutEffect(() => {
    const element = menu.current;
    if (!open || !element) return;
    const fit = () => {
      element.style.removeProperty("--menu-nudge");
      const box = element.getBoundingClientRect();
      const nudge = Math.max(MARGIN - box.left, Math.min(0, window.innerWidth - MARGIN - box.right));
      element.style.setProperty("--menu-nudge", `${Math.round(nudge)}px`);
      element.style.setProperty("--menu-room", `${Math.max(MIN_ROOM, Math.floor(window.innerHeight - box.top - MARGIN))}px`);
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [open, menu]);
}
