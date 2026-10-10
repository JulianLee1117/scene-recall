"use client";

import { useCallback, useEffect, useId, useRef } from "react";
import { DEFAULT_VIEW, ORDER_OPTIONS, SIZE_OPTIONS, type ViewPrefs } from "@/lib/viewPrefs";
import { useDismiss } from "@/hooks/useDismiss";
import { useMenuFit } from "@/hooks/useMenuFit";
import ChoiceRow from "./ChoiceRow";
import DirectionIcon from "./DirectionIcon";
import styles from "./viewMenu.module.css";

/**
 * How results are shown: their order, their size and whether each card
 * explains its match. None of it changes what is searched.
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
    <div className="toolbar-menu-root" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="toolbar-menu-trigger"
        aria-expanded={open}
        aria-controls={panelId}
        aria-haspopup="dialog"
        title="Result order, size and debug mode"
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
            <div className={styles.debugSection}>
              <button
                type="button"
                className={styles.debugButton}
                aria-label="Debug mode"
                aria-pressed={view.details}
                aria-describedby={`${panelId}-debug-hint`}
                onClick={() => onChange({ details: !view.details })}
              >
                <svg className={styles.projector} width="36" height="32" viewBox="0 0 36 32" fill="none" shapeRendering="crispEdges" aria-hidden="true">
                  <path d="M5 2h6v2h2v6h-2v2H5v-2H3V4h2Zm13 0h6v2h2v6h-2v2h-6v-2h-2V4h2Z" fill="#84999a" />
                  <path d="M7 5h2v4H7Zm13 0h2v4h-2Z" fill="#182326" />
                  <path d="M5 13h21v13H5Z" fill="#53696b" />
                  <path d="M5 13h21v3H5Z" fill="#a8beb6" />
                  <path d="M8 19h8v4H8Z" fill="#25383b" />
                  <path d="M19 18h4v5h-4Z" fill="#e9bb75" />
                  <path d="M26 17h3v-2h4v10h-4v-2h-3Z" fill="#c6d9ce" />
                  <path className={styles.lens} d="M33 17h2v6h-2Z" fill="#ecab66" />
                  <path d="M13 26h5v3h5v2H8v-2h5Z" fill="#84999a" />
                </svg>
                <span className={styles.debugCopy}>
                  <span className={styles.debugTitle} key={String(view.details)} data-label="DEBUG MODE" aria-hidden="true">DEBUG MODE</span>
                  <span id={`${panelId}-debug-hint`} className={styles.debugHint}>Match scores &amp; descriptions</span>
                </span>
                <span className={styles.debugState} aria-hidden="true">{view.details ? "ON" : "OFF"}</span>
                <span className={styles.debugFx} key={`fx-${view.details}`} aria-hidden="true">
                  <span className={styles.fireball} />
                  <span className={styles.impact} />
                </span>
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
