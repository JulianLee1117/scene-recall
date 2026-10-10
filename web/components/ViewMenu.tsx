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
        {/* Lucide eye / eye-closed (ISC); see web/THIRD_PARTY_NOTICES.md. */}
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
          <g className={styles.eyeClosed}>
            <path d="m15 18-.722-3.25" />
            <path d="M2 8a10.645 10.645 0 0 0 20 0" />
            <path d="m20 15-1.726-2.05" />
            <path d="m4 15 1.726-2.05" />
            <path d="m9 18 .722-3.25" />
          </g>
          <g className={styles.eyeOpen}>
            <path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0" />
            <circle cx="12" cy="12" r="3" />
          </g>
        </svg>
      </button>
    </div>
  );
}
