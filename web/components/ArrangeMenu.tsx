"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ARRANGEMENTS, type Arrangement } from "@/lib/boardOrder";
import { useDismiss } from "@/hooks/useDismiss";
import { useMenuFit } from "@/hooks/useMenuFit";
import ChoiceRow from "./ChoiceRow";
import DirectionIcon from "./DirectionIcon";
import styles from "./arrangeMenu.module.css";

/**
 * How the Saved board is ordered. Your order is the board's own and the only
 * one stored; Newest and By film are views over it that never move a scene.
 */
export default function ArrangeMenu({ value, placed, onChange }: {
  value: Arrangement;
  /** Whether a scene has been placed by hand yet. */
  placed: boolean;
  onChange: (arrangement: Arrangement) => void;
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
  const hint = value !== "yours" ? "Drag scenes in Your order." : placed ? null : "Drag a scene to place it.";

  return (
    <div className="toolbar-menu-root" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="toolbar-menu-trigger"
        aria-expanded={open}
        aria-controls={panelId}
        aria-haspopup="dialog"
        title="Arrange the board"
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
            {hint && <p className={styles.hint}>{hint}</p>}
          </div>
        </div>
      )}
    </div>
  );
}
