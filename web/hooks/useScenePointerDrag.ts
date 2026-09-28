"use client";

import {
  useEffect,
  useRef,
  type DragEvent,
  type MouseEvent,
  type PointerEvent,
} from "react";
import { FACET_LABELS, type MatchDraft } from "@/lib/searchRecipe";
import { formatTime } from "@/lib/format";
import { createSceneDragPreview, SCENE_DRAG_HOTSPOT } from "@/lib/nativeDragPreview";
import type { RecipeMatchFacet } from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

export const SCENE_POINTER_EVENT = "scene-recall:scene-pointer";
export interface ScenePointerDetail {
  phase: "start" | "move" | "drop" | "end";
  draft: MatchDraft;
  originFacet?: RecipeMatchFacet;
  x: number;
  y: number;
}

/** One gesture owns the pointer until release. A click still opens the scene. */
export function useScenePointerDrag(
  draft: MatchDraft | null,
  options: {
    originFacet?: RecipeMatchFacet;
    onDragging?: (active: boolean) => void;
  } = {},
) {
  const gesture = useRef<{
    pointer: number;
    target: HTMLElement;
    x: number;
    y: number;
    active: boolean;
    draft: MatchDraft;
    originFacet?: RecipeMatchFacet;
  } | null>(null);
  const ghost = useRef<HTMLDivElement | null>(null);
  const suppressUntil = useRef(0);
  const notify = (phase: ScenePointerDetail["phase"], x: number, y: number) => {
    const current = gesture.current;
    if (!current) return;
    document.dispatchEvent(
      new CustomEvent<ScenePointerDetail>(SCENE_POINTER_EVENT, {
        detail: {
          phase,
          x,
          y,
          draft: current.draft,
          originFacet: current.originFacet,
        },
      }),
    );
  };
  const clear = () => {
    const current = gesture.current;
    if (!current) return;
    if (current.active) notify("end", 0, 0);
    ghost.current?.remove();
    ghost.current = null;
    gesture.current = null;
    if (current.active) options.onDragging?.(false);
    if (current.target.hasPointerCapture(current.pointer))
      current.target.releasePointerCapture(current.pointer);
  };
  useEffect(
    () => () => {
      const current = gesture.current;
      if (current?.active)
        document.dispatchEvent(
          new CustomEvent<ScenePointerDetail>(SCENE_POINTER_EVENT, {
            detail: {
              phase: "end",
              x: 0,
              y: 0,
              draft: current.draft,
              originFacet: current.originFacet,
            },
          }),
        );
      ghost.current?.remove();
      gesture.current = null;
      if (current?.target.hasPointerCapture(current.pointer))
        current.target.releasePointerCapture(current.pointer);
    },
    [],
  );
  useEffect(() => {
    const cancel = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !gesture.current?.active) return;
      event.preventDefault();
      suppressUntil.current = performance.now() + 250;
      clear();
    };
    document.addEventListener("keydown", cancel);
    return () => document.removeEventListener("keydown", cancel);
  });

  return {
    onPointerDown(event: PointerEvent<HTMLElement>) {
      if (!draft || gesture.current || event.button !== 0 || !event.isPrimary) return;
      gesture.current = {
        pointer: event.pointerId,
        target: event.currentTarget,
        x: event.clientX,
        y: event.clientY,
        active: false,
        draft,
        originFacet: options.originFacet,
      };
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    onPointerMove(event: PointerEvent<HTMLElement>) {
      const current = gesture.current;
      if (!current || current.pointer !== event.pointerId) return;
      if (!current.active) {
        if (
          Math.hypot(event.clientX - current.x, event.clientY - current.y) < 8
        )
          return;
        current.active = true;
        options.onDragging?.(true);
        const display = current.draft.kind === "source" ? current.draft.display : undefined;
        const element = createSceneDragPreview({
          eyebrow: current.originFacet ? `Moving ${FACET_LABELS[current.originFacet]}` : "Scene",
          title: display?.filmTitle || "Scene reference",
          detail: typeof display?.timestamp === "number" ? formatTime(display.timestamp) : undefined,
          imageUrl: display?.keyframeUrl ? `${API_URL}${display.keyframeUrl}` : undefined,
        });
        element.classList.add("scene-pointer-ghost");
        document.body.append(element);
        ghost.current = element;
        notify("start", event.clientX, event.clientY);
      }
      event.preventDefault();
      if (ghost.current) {
        // Keep the picked-up point attached to the cursor, including near an
        // edge. Clipping is preferable to jumping to the cursor's other side.
        ghost.current.style.left = `${event.clientX - SCENE_DRAG_HOTSPOT.x}px`;
        ghost.current.style.top = `${event.clientY - SCENE_DRAG_HOTSPOT.y}px`;
      }
      notify("move", event.clientX, event.clientY);
    },
    onPointerUp(event: PointerEvent<HTMLElement>) {
      const current = gesture.current;
      if (!current || current.pointer !== event.pointerId) return;
      if (current.active) {
        event.preventDefault();
        suppressUntil.current = performance.now() + 250;
        notify("drop", event.clientX, event.clientY);
      }
      clear();
    },
    onPointerCancel(event: PointerEvent<HTMLElement>) {
      if (gesture.current?.pointer !== event.pointerId) return;
      clear();
    },
    onLostPointerCapture(event: PointerEvent<HTMLElement>) {
      if (gesture.current?.pointer !== event.pointerId) return;
      clear();
    },
    onClickCapture(event: MouseEvent<HTMLElement>) {
      if (performance.now() < suppressUntil.current) {
        event.preventDefault();
        event.stopPropagation();
      }
    },
    onDragStart(event: DragEvent<HTMLElement>) {
      // Prevent a second native gesture from applying the same scene twice.
      if (gesture.current) event.preventDefault();
    },
  };
}
