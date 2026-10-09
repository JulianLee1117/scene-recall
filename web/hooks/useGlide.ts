"use client";

import { useLayoutEffect, useRef, type RefObject } from "react";

/**
 * Glides `moving` from where `anchor` sat to where it sits now whenever
 * `state` changes, instead of jumping there (FLIP). The search bar uses it
 * to travel between its centered home spot and the top of the results.
 */
export function useGlide(
  moving: RefObject<HTMLElement | null>,
  anchor: RefObject<HTMLElement | null>,
  state: unknown,
): void {
  const last = useRef<{ state: unknown; top: number } | null>(null);
  // Measured after every render, so the starting point is never stale.
  useLayoutEffect(() => {
    const element = moving.current;
    const target = anchor.current;
    if (!element || !target) return;
    const top = target.getBoundingClientRect().top + window.scrollY;
    const previous = last.current;
    last.current = { state, top };
    if (!previous || Object.is(previous.state, state)) return;
    const offset = previous.top - top;
    if (Math.abs(offset) < 2 || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
    element.animate?.(
      [{ transform: `translateY(${offset}px)` }, { transform: "none" }],
      { duration: 320, easing: "cubic-bezier(0.22, 0.8, 0.24, 1)" },
    );
  });
}
