"use client";

import { useId, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import EditorIcon from "./EditorIcon";
import { editorPopoverPosition } from "./editorPopoverPosition";
import styles from "./editorPopover.module.css";

type Props = {
  title: string;
  label: ReactNode;
  triggerLabel?: string;
  triggerTitle?: string;
  triggerClassName?: string;
  triggerStyle?: CSSProperties;
  panelClassName?: string;
  width?: number;
  align?: "start" | "end";
  disabled?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  children: ReactNode | ((close: () => void) => ReactNode);
};

/** Native top layer retains editor styles while escaping clipping and page flow. */
export default function EditorPopover({ title, label, triggerLabel, triggerTitle, triggerClassName, triggerStyle, panelClassName, width = 360, align = "end", disabled, open: controlledOpen, onOpenChange, children }: Props) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const [localOpen, setLocalOpen] = useState(false);
  const open = controlledOpen ?? localOpen;
  const latest = useRef({ onOpenChange, open });
  latest.current = { onOpenChange, open };

  function change(value: boolean) {
    setLocalOpen(value);
    latest.current.onOpenChange?.(value);
  }
  function close(restoreFocus = true) {
    if (panel.current?.matches(":popover-open")) panel.current.hidePopover();
    change(false);
    if (restoreFocus) trigger.current?.focus({ preventScroll: true });
  }

  useLayoutEffect(() => {
    const node = panel.current, button = trigger.current;
    if (!node || !button) return;
    if (!open) { if (node.matches(":popover-open")) node.hidePopover(); return; }
    function position() {
      if (!node || !button) return;
      const viewport = { width: window.innerWidth, height: window.innerHeight };
      const anchor = button.getBoundingClientRect();
      if (anchor.bottom < 0 || anchor.top > viewport.height || anchor.right < 0 || anchor.left > viewport.width) { close(false); return; }
      node.style.width = `${Math.max(1, Math.min(width, viewport.width - 24))}px`;
      const next = editorPopoverPosition(anchor, node.scrollHeight + 2, viewport, width, align);
      Object.assign(node.style, { left: `${next.left}px`, top: `${next.top}px`, width: `${next.width}px`, maxHeight: `${next.maxHeight}px` });
    }
    if (!node.matches(":popover-open")) node.showPopover();
    position();
    node.focus({ preventScroll: true });
    const scroll = (event: Event) => { if (!(event.target instanceof Node) || !node.contains(event.target)) position(); };
    const observer = new ResizeObserver(position);
    observer.observe(node);
    observer.observe(button);
    window.addEventListener("resize", position);
    window.addEventListener("scroll", scroll, true);
    return () => { observer.disconnect(); window.removeEventListener("resize", position); window.removeEventListener("scroll", scroll, true); };
  }, [open, width, align]);

  return <>
    <button ref={trigger} type="button" className={triggerClassName} style={triggerStyle} disabled={disabled}
      aria-label={triggerLabel} title={triggerTitle} aria-expanded={open} aria-controls={id} aria-haspopup="dialog"
      onClick={() => { if (open) close(); else change(true); }}>{label}</button>
    <div ref={panel} id={id} popover="auto" role="dialog" aria-label={title} tabIndex={-1}
      className={`${styles.panel} ${panelClassName ?? ""}`}
      onToggle={(event) => { const visible = event.currentTarget.matches(":popover-open"); if (visible !== latest.current.open) change(visible); }}
      onBlur={(event) => {
        const next = event.relatedTarget;
        if (next instanceof Node && !event.currentTarget.contains(next) && !trigger.current?.contains(next)) close(false);
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          const nested = (event.target as Element).closest('[role="dialog"]');
          if (nested && nested !== panel.current) return;
          event.stopPropagation(); close();
        } else if (event.key === " ") {
          if (event.target === panel.current) event.preventDefault();
          event.stopPropagation();
        }
      }}>
      <header className={styles.heading}><strong>{title}</strong><button type="button" onClick={() => close()} aria-label={`Close ${title}`}><EditorIcon name="close" size={14} /></button></header>
      <div className={styles.content}>{typeof children === "function" ? children(() => close()) : children}</div>
    </div>
  </>;
}
