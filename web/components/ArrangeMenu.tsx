"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ARRANGEMENTS, type Arrangement } from "@/lib/boardOrder";
import { useDismiss } from "@/hooks/useDismiss";
import { useMenuFit } from "@/hooks/useMenuFit";
import ChoiceRow from "./ChoiceRow";
import DirectionIcon from "./DirectionIcon";
import styles from "./arrangeMenu.module.css";

/**
 * How the Saved board is laid out. Your order is the board's own and the only
 * one stored; Newest and By film are views over it, so they never move a
 * scene. A view becomes your order only by keeping it, and your order can be
 * reset to newest.
 */
export default function ArrangeMenu({ value, hasOrder, onChange, onKeep, onReset }: {
  value: Arrangement;
  /** Whether anything has been placed by hand, so a reset means something. */
  hasOrder: boolean;
  onChange: (arrangement: Arrangement) => void;
  /** Make the current view the board's order. */
  onKeep: () => void;
  /** Forget the board's order: newest first again. */
  onReset: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const close = useCallback(() => setOpen(false), []);
  useDismiss(open, rootRef, close, triggerRef);
  useMenuFit(open, panelRef);
  useEffect(() => {
    if (open) panelRef.current?.focus({ preventScroll: true });
  }, [open]);
  const current = ARRANGEMENTS.find((option) => option.value === value) ?? ARRANGEMENTS[0];

  return (
    <div className="toolbar-menu-root" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="toolbar-menu-trigger"
        aria-expanded={open}
        aria-controls={panelId}
        aria-haspopup="dialog"
        title="How the board is arranged"
        onClick={() => setOpen((state) => !state)}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" aria-hidden="true">
          <path d="M4 7h16M4 12h10M4 17h13" />
        </svg>
        <span>{current.label}</span>
        <DirectionIcon name="chevron-down" className="toolbar-menu-chevron" size={12} />
      </button>

      {open && (
        <div ref={panelRef} id={panelId} className={`toolbar-menu ${styles.panel}`} role="dialog" aria-label="Arrange the board" tabIndex={-1}>
          <div className={`toolbar-menu-list ${styles.content}`}>
            <ChoiceRow label="Arrange" options={ARRANGEMENTS} value={value} defaultValue="yours" onChange={onChange} />
            {value !== "yours" ? (
              <p className={styles.note}>
                A view over your order; nothing moves. Drag scenes in Your order, or keep this one.
                <button type="button" className="toolbar-menu-link" onClick={() => { onKeep(); close(); }}>
                  Keep this as your order
                </button>
              </p>
            ) : hasOrder ? (
              <p className={styles.note}>
                Drag a scene to place it. New saves land at the top.
                <button type="button" className="toolbar-menu-link" onClick={() => { onReset(); close(); }}>
                  Reset to newest
                </button>
              </p>
            ) : (
              <p className={styles.note}>Newest first until you drag a scene somewhere.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
