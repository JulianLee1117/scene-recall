"use client";

import { useEffect, type RefObject } from "react";

/**
 * While a popover is open, a press outside `root` or Escape closes it;
 * Escape also returns focus to its trigger.
 */
export function useDismiss(
  open: boolean,
  root: RefObject<HTMLElement | null>,
  close: () => void,
  trigger?: RefObject<HTMLElement | null>,
): void {
  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) close();
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      close();
      trigger?.current?.focus();
    };
    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open, root, close, trigger]);
}
