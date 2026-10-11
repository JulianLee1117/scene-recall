"use client";

import { useEffect, useState, type RefObject } from "react";
import { createSearchHistory, type HistoryView, type SearchHistory } from "@/lib/searchHistory";

const NO_HISTORY: SearchHistory = {
  start() {},
  pop() {},
  leave() {},
  record() {},
  update() {},
  home() {},
  switchTab() {},
  openShot() {},
  closeShot() {},
};

/**
 * Back and Forward on the search page (lib/searchHistory.ts). `view` is read
 * when a screen is shown, so the page can fill it in after its own callbacks.
 */
export function useSearchHistory(view: RefObject<Omit<HistoryView, "scrollY"> | null>): SearchHistory {
  const [history] = useState(() => typeof window === "undefined" ? null : createSearchHistory(window.history, {
    show: (screen) => view.current?.show(screen),
    tab: () => view.current?.tab() ?? "search",
    scrollY: () => window.scrollY,
  }));
  useEffect(() => {
    if (!history) return;
    // The page puts each screen's scroll back itself, once its results are drawn.
    const restoration = window.history.scrollRestoration;
    window.history.scrollRestoration = "manual";
    const onPop = () => history.pop();
    window.addEventListener("popstate", onPop);
    return () => {
      window.removeEventListener("popstate", onPop);
      window.history.scrollRestoration = restoration;
    };
  }, [history]);
  return history ?? NO_HISTORY;
}
