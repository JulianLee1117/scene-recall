"use client";

import { useEffect, useRef, type MouseEvent, type PointerEvent } from "react";
import type { LabClip, MusicMatchEvidence } from "@/types/lab";
import styles from "./labSceneDrag.module.css";

type Scene = { clip: LabClip; evidence: MusicMatchEvidence | null; title: string };
type Options = {
  enabled: boolean;
  onDragScene?: (clip: LabClip | null, evidence?: MusicMatchEvidence | null) => void;
  onDropScene?: (slotId: string) => void;
};
type Gesture = Scene & {
  pointer: number;
  x: number;
  y: number;
  origin: HTMLElement;
  active: boolean;
};

/** A local pointer gesture; only explicitly eligible Lab slots receive a drop. */
export function useLabSceneDrag(options: Options) {
  const latest = useRef(options);
  latest.current = options;
  const gesture = useRef<Gesture | null>(null);
  const ghost = useRef<HTMLDivElement | null>(null);
  const target = useRef<HTMLElement | null>(null);
  const suppressClickUntil = useRef(0);
  const cancel = useRef<() => void>(() => {});

  useEffect(() => {
    function setTarget(next: HTMLElement | null) {
      if (next === target.current) return;
      target.current?.removeAttribute("data-scene-drop-active");
      next?.setAttribute("data-scene-drop-active", "true");
      target.current = next;
    }
    function targetAt(x: number, y: number) {
      return document.elementFromPoint(x, y)?.closest<HTMLElement>(
        '[data-lab-scene-slot][data-accepts-scene="true"]',
      ) ?? null;
    }
    function clear() {
      const current = gesture.current;
      gesture.current = null;
      setTarget(null);
      ghost.current?.remove();
      ghost.current = null;
      if (current?.active) {
        suppressClickUntil.current = performance.now() + 350;
        latest.current.onDragScene?.(null);
      }
      if (current?.origin.hasPointerCapture(current.pointer))
        current.origin.releasePointerCapture(current.pointer);
    }
    cancel.current = clear;
    function move(event: globalThis.PointerEvent) {
      const current = gesture.current;
      if (!current || current.pointer !== event.pointerId) return;
      if (!latest.current.enabled || !current.origin.isConnected) return clear();
      if (!current.active) {
        if (Math.hypot(event.clientX - current.x, event.clientY - current.y) < 8) return;
        current.active = true;
        current.origin.setPointerCapture(current.pointer);
        latest.current.onDragScene?.(current.clip, current.evidence);
        const element = document.createElement("div");
        element.className = styles.ghost;
        element.setAttribute("aria-hidden", "true");
        element.textContent = current.title;
        document.body.append(element);
        ghost.current = element;
      }
      event.preventDefault();
      setTarget(targetAt(event.clientX, event.clientY));
      if (ghost.current) {
        ghost.current.style.left = `${event.clientX + 14}px`;
        ghost.current.style.top = `${event.clientY + 14}px`;
        ghost.current.dataset.accepted = String(!!target.current);
      }
    }
    function finish(event: globalThis.PointerEvent) {
      const current = gesture.current;
      if (!current || current.pointer !== event.pointerId) return;
      try {
        if (current.active) {
          event.preventDefault();
          const destination = targetAt(event.clientX, event.clientY);
          const slotId = destination?.dataset.labSceneSlot;
          if (latest.current.enabled && slotId) {
            latest.current.onDropScene?.(slotId);
            // The timeline owns Space after a drop, rather than the source's
            // Preview button retaining focus and opening its review dialog.
            destination.focus({ preventScroll: true });
          }
        }
      } finally {
        clear();
      }
    }
    function cancelPointer(event: globalThis.PointerEvent) {
      if (gesture.current?.pointer === event.pointerId) clear();
    }
    function lostCapture(event: globalThis.PointerEvent) {
      if (event.target === gesture.current?.origin) cancelPointer(event);
    }
    function escape(event: KeyboardEvent) {
      if (event.key !== "Escape" || !gesture.current) return;
      event.preventDefault();
      clear();
    }
    window.addEventListener("pointermove", move, { passive: false });
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", cancelPointer);
    window.addEventListener("lostpointercapture", lostCapture);
    window.addEventListener("keydown", escape);
    window.addEventListener("blur", clear);
    return () => {
      clear();
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", cancelPointer);
      window.removeEventListener("lostpointercapture", lostCapture);
      window.removeEventListener("keydown", escape);
      window.removeEventListener("blur", clear);
    };
  }, []);

  useEffect(() => {
    if (!options.enabled) cancel.current();
  }, [options.enabled]);

  return {
    begin(event: PointerEvent<HTMLElement>, scene: Scene) {
      if (!latest.current.enabled || event.button !== 0 || !event.isPrimary) return;
      cancel.current();
      gesture.current = {
        ...scene, pointer: event.pointerId, x: event.clientX, y: event.clientY,
        origin: event.currentTarget, active: false,
      };
    },
    onClickCapture(event: MouseEvent<HTMLElement>) {
      if (performance.now() >= suppressClickUntil.current) return;
      event.preventDefault();
      event.stopPropagation();
    },
  };
}
