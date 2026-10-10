"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import BookmarkIcon from "./BookmarkIcon";
import ArrangeMenu from "./ArrangeMenu";
import BoardCanvas, { type SceneActions } from "./BoardCanvas";
import { arrangeBoard, hasUserOrder, type Arrangement } from "@/lib/boardOrder";
import { useSceneLooks } from "@/hooks/useSceneLooks";
import { BOARD_SCALE, DEFAULT_BOARD, loadBoardPrefs, saveBoardPrefs, type BoardPrefs } from "@/lib/boardPrefs";
import type { BookmarkRecord } from "@/types/api";
import chrome from "./pageChrome.module.css";
import styles from "./savedView.module.css";

interface SavedViewProps extends SceneActions {
  bookmarks: BookmarkRecord[];
  loading: boolean;
  error: string | null;
  /** Store the board's order, first to last. Resolves to whether it held. */
  onReorder: (bookmarkIds: string[]) => Promise<boolean>;
}

/** The board: saved scenes in your order, resized by a slider, viewed as you like. */
export default function SavedView({
  bookmarks,
  loading,
  error,
  onReorder,
  pendingUnitIds,
  onShotClick,
  onUseInSearch,
  disabledUseFacets,
  onToggleBookmark,
  onRemoveBookmark,
}: SavedViewProps) {
  const [prefs, setPrefs] = useState<BoardPrefs>(DEFAULT_BOARD);
  // Shuffle's deal: a fresh one each visit and each time Shuffle is chosen.
  const [deal, setDeal] = useState(0);
  useEffect(() => {
    const loaded = loadBoardPrefs();
    setPrefs(loaded);
    if (loaded.arrangement === "shuffle") setDeal(newDeal());
  }, []);
  const arrange = (arrangement: Arrangement) => {
    if (arrangement === "shuffle") setDeal(newDeal());
    changePrefs({ arrangement });
  };
  const { lookOf } = useSceneLooks(bookmarks, prefs.arrangement === "colour");
  const changePrefs = (change: Partial<BoardPrefs>) =>
    setPrefs((current) => {
      const next = { ...current, ...change };
      saveBoardPrefs(next);
      return next;
    });

  // One object for the tiles, remade only when a callback changes, so a drag renders only what moves.
  const actions = useMemo<SceneActions>(
    () => ({ pendingUnitIds, onShotClick, onUseInSearch, disabledUseFacets, onToggleBookmark, onRemoveBookmark }),
    [pendingUnitIds, onShotClick, onUseInSearch, disabledUseFacets, onToggleBookmark, onRemoveBookmark],
  );
  const items = useMemo(
    () => arrangeBoard(bookmarks, prefs.arrangement, { lookOf, seed: deal }),
    [bookmarks, prefs.arrangement, lookOf, deal],
  );

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
              onChange={arrange}
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
          items={items}
          scale={prefs.scale}
          canReorder={prefs.arrangement === "yours" && bookmarks.length > 1}
          onReorder={reorder}
          actions={actions}
        />
      )}
    </section>
  );
}

const newDeal = () => Math.floor(Math.random() * 2 ** 32);

/** The slider's ends: a small frame and a larger one. */
function SizeGlyph({ large }: { large: boolean }) {
  const size = large ? 14 : 9;
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <rect x={(16 - size) / 2} y={(16 - size) / 2} width={size} height={size} rx="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}
