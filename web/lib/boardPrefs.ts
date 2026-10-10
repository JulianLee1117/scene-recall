import { ARRANGEMENTS, type Arrangement } from "./boardOrder";

/**
 * How this browser shows the Saved board: the tile size and the arrangement.
 * The board's order itself lives on the server, with the bookmarks.
 */
export interface BoardPrefs {
  /** Multiplies the medium row height. */
  scale: number;
  arrangement: Arrangement;
}

export const BOARD_SCALE = { min: 0.6, max: 1.8, step: 0.05 } as const;
export const DEFAULT_BOARD: BoardPrefs = { scale: 1, arrangement: "yours" };

const STORAGE_KEY = "scene-recall.saved";

/** Saved prefs; anything unreadable or unknown falls back to the default. */
export function parseBoardPrefs(raw: string | null): BoardPrefs {
  let saved: Record<string, unknown> = {};
  try {
    const parsed: unknown = JSON.parse(raw ?? "{}");
    if (parsed && typeof parsed === "object") saved = parsed as Record<string, unknown>;
  } catch {
    // A corrupt entry is ignored.
  }
  const scale = typeof saved.scale === "number" && Number.isFinite(saved.scale)
    ? Math.min(BOARD_SCALE.max, Math.max(BOARD_SCALE.min, saved.scale))
    : DEFAULT_BOARD.scale;
  return {
    scale,
    arrangement: ARRANGEMENTS.find((option) => option.value === saved.arrangement)?.value ?? DEFAULT_BOARD.arrangement,
  };
}

export function loadBoardPrefs(): BoardPrefs {
  try {
    return parseBoardPrefs(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    return DEFAULT_BOARD;
  }
}

export function saveBoardPrefs(prefs: BoardPrefs): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // Storage blocked (private window): the choice lasts for this visit only.
  }
}
