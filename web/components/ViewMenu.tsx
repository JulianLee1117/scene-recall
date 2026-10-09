"use client";

import { useCallback, useEffect, useId, useRef } from "react";
import { ORDER_OPTIONS, SIZE_OPTIONS, type ViewPrefs } from "@/lib/viewPrefs";
import { useDismiss } from "@/hooks/useDismiss";
import DirectionIcon from "./DirectionIcon";

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
  useEffect(() => {
    if (open) panelRef.current?.focus();
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
        <div ref={panelRef} id={panelId} className="toolbar-menu" role="dialog" aria-label="View options" tabIndex={-1}>
          <div className="toolbar-menu-list">
            <Segmented label="Order" options={ORDER_OPTIONS} value={view.order} onChange={(order) => onChange({ order })} />
            <Segmented label="Size" options={SIZE_OPTIONS} value={view.size} onChange={(size) => onChange({ size })} />
            <button
              type="button"
              className="toolbar-menu-toggle"
              role="switch"
              aria-checked={view.details}
              onClick={() => onChange({ details: !view.details })}
            >
              <span>Details</span>
              <span className="toolbar-menu-switch" aria-hidden="true" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: ReadonlyArray<{ value: T; label: string }>;
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="toolbar-menu-setting">
      <span>{label}</span>
      <div className="toolbar-menu-segmented" role="radiogroup" aria-label={label}>
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={value === option.value}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}
