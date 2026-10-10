"use client";

import { useCallback, useEffect, useId, useRef } from "react";
import { DEFAULT_VIEW, ORDER_OPTIONS, SIZE_OPTIONS, type ViewPrefs } from "@/lib/viewPrefs";
import { useDismiss } from "@/hooks/useDismiss";
import { useMenuFit } from "@/hooks/useMenuFit";
import ChoiceRow from "./ChoiceRow";
import DirectionIcon from "./DirectionIcon";
import styles from "./viewMenu.module.css";

const DETAIL_OPTIONS = [
  { value: "hidden", label: "Hidden" },
  { value: "shown", label: "Shown" },
] as const;

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
        title="Order, size and details of the results"
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
            <ChoiceRow
              label="Details"
              options={DETAIL_OPTIONS}
              value={view.details ? "shown" : "hidden"}
              defaultValue={DEFAULT_VIEW.details ? "shown" : "hidden"}
              onChange={(details) => onChange({ details: details === "shown" })}
            />
          </div>
        </div>
      )}
    </div>
  );
}
