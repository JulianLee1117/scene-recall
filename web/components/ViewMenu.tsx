"use client";

import { useCallback, useEffect, useId, useRef } from "react";
import { DEFAULT_VIEW, ORDER_OPTIONS, SIZE_OPTIONS, type ViewPrefs } from "@/lib/viewPrefs";
import { useDismiss } from "@/hooks/useDismiss";
import { useMenuFit } from "@/hooks/useMenuFit";
import ChoiceRow from "./ChoiceRow";
import DirectionIcon from "./DirectionIcon";
import styles from "./viewMenu.module.css";

/**
 * Result order and size, with an independent match-details toggle beside
 * the menu. None of these controls changes what is searched.
 */
export default function ViewMenu({
  open,
  onOpenChange,
  view,
  onChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  view: ViewPrefs;
  onChange: (change: Partial<ViewPrefs>) => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const close = useCallback(() => onOpenChange(false), [onOpenChange]);
  useDismiss(open, rootRef, close, triggerRef);
  useMenuFit(open, panelRef);
  useEffect(() => {
    if (open) panelRef.current?.focus({ preventScroll: true });
  }, [open]);

  return (
    <div className={styles.controls}>
      <div className="toolbar-menu-root" ref={rootRef}>
        <button
          ref={triggerRef}
          type="button"
          className="toolbar-menu-trigger"
          aria-expanded={open}
          aria-controls={panelId}
          aria-haspopup="dialog"
          title="Result order and size"
          onClick={() => onOpenChange(!open)}
        >
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
            <rect x="3.5" y="4.5" width="7" height="6" rx="1" />
            <rect x="13.5" y="4.5" width="7" height="6" rx="1" />
            <rect x="3.5" y="13.5" width="7" height="6" rx="1" />
            <rect x="13.5" y="13.5" width="7" height="6" rx="1" />
          </svg>
          <span>View</span>
          <DirectionIcon name="chevron-down" className="toolbar-menu-chevron" size={12} />
        </button>

        {open && (
          <div ref={panelRef} id={panelId} className={`toolbar-menu ${styles.panel}`} role="dialog" aria-label="View options" tabIndex={-1}>
            <div className={`toolbar-menu-list is-scroll ${styles.content}`}>
              <ChoiceRow
                label="Order"
                options={ORDER_OPTIONS}
                value={view.order}
                defaultValue={DEFAULT_VIEW.order}
                onChange={(order) => onChange({ order })}
              />
              <ChoiceRow
                label="Size"
                options={SIZE_OPTIONS}
                value={view.size}
                defaultValue={DEFAULT_VIEW.size}
                onChange={(size) => onChange({ size })}
              />
            </div>
          </div>
        )}
      </div>
      <button
        type="button"
        className={styles.detailsToggle}
        aria-label="Match details"
        aria-pressed={view.details}
        title={view.details ? "Hide match scores and descriptions" : "Show match scores and descriptions"}
        onClick={() => onChange({ details: !view.details })}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
          {view.details ? <>
            <path d="M2.5 12C4.7 7.8 8 5.5 12 5.5S19.3 7.8 21.5 12C19.3 16.2 16 18.5 12 18.5S4.7 16.2 2.5 12Z" />
            <circle cx="12" cy="12" r="2.75" />
          </> : <>
            <path d="M3 9c3 6 15 6 18 0" />
            <path d="m5.4 12-1.6 2M12 13.5v2.8m6.6-4.3 1.6 2" />
          </>}
        </svg>
      </button>
    </div>
  );
}
