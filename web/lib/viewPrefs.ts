import type { RankingPreset } from "@/types/api";
import type { RowSize } from "./justifiedRows";

/** How results are shown. Remembered in this browser; filters are not. */
export interface ViewPrefs {
  order: RankingPreset;
  size: RowSize;
  details: boolean;
}

export const DEFAULT_VIEW: ViewPrefs = { order: "balanced", size: "medium", details: false };

/** The orderings, in menu order. They rerank results; none hides any. */
export const ORDER_OPTIONS: ReadonlyArray<{ value: RankingPreset; label: string }> = [
  { value: "balanced", label: "Balanced" },
  { value: "famous", label: "Famous" },
  { value: "gems", label: "Hidden gems" },
];

export const SIZE_OPTIONS: ReadonlyArray<{ value: RowSize; label: string }> = [
  { value: "small", label: "Small" },
  { value: "medium", label: "Medium" },
  { value: "large", label: "Large" },
];

const STORAGE_KEY = "scene-recall.view";

/** Saved prefs; anything unreadable or unknown falls back to the default. */
export function parseViewPrefs(raw: string | null): ViewPrefs {
  let saved: Record<string, unknown> = {};
  try {
    const parsed: unknown = JSON.parse(raw ?? "{}");
    if (parsed && typeof parsed === "object") saved = parsed as Record<string, unknown>;
  } catch {
    // A corrupt entry is ignored.
  }
  return {
    order: ORDER_OPTIONS.find((option) => option.value === saved.order)?.value ?? DEFAULT_VIEW.order,
    size: SIZE_OPTIONS.find((option) => option.value === saved.size)?.value ?? DEFAULT_VIEW.size,
    details: typeof saved.details === "boolean" ? saved.details : DEFAULT_VIEW.details,
  };
}

export function loadViewPrefs(): ViewPrefs {
  try {
    return parseViewPrefs(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    return DEFAULT_VIEW;
  }
}

export function saveViewPrefs(prefs: ViewPrefs): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // Storage blocked (private window): the choice lasts for this visit only.
  }
}
