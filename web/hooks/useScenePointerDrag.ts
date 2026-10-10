"use client";

import {
  useEffect,
  useRef,
  type DragEvent,
  type MouseEvent,
  type PointerEvent,
} from "react";
import type { MatchDraft } from "@/lib/searchRecipe";
import { liftScene, type SceneCarry } from "@/lib/sceneCarry";
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

/**
 * One gesture owns the pointer until release. A click still opens the scene.
 * Past a short move the scene's picture lifts off its card and follows the
 * pointer; targets hear each phase on the document. A `drop` is cancelable:
 * a target that takes the scene prevents its default, and the carried
 * picture collapses into it instead of flying back.
 */
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
  const carry = useRef<SceneCarry | null>(null);
  const suppressUntil = useRef(0);
  /** Tells the document; for a drop, reports whether a listener took the scene. */
  const notify = (phase: ScenePointerDetail["phase"], x: number, y: number): boolean => {
    const current = gesture.current;
    if (!current) return false;
    const event = new CustomEvent<ScenePointerDetail>(SCENE_POINTER_EVENT, {
      cancelable: phase === "drop",
      detail: { phase, x, y, draft: current.draft, originFacet: current.originFacet },
    });
    return !document.dispatchEvent(event);
  };
  const clear = (outcome: "taken" | "returned") => {
    const current = gesture.current;
    if (!current) return;
    if (current.active) notify("end", 0, 0);
    carry.current?.settle(outcome);
    carry.current = null;
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
            detail: { phase: "end", x: 0, y: 0, draft: current.draft, originFacet: current.originFacet },
          }),
        );
      carry.current?.dispose();
      carry.current = null;
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
      clear("returned");
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
        if (Math.hypot(event.clientX - current.x, event.clientY - current.y) < 8) return;
        current.active = true;
        options.onDragging?.(true);
        const display = current.draft.kind === "source" ? current.draft.display : undefined;
        carry.current = liftScene({
          picture: current.target.querySelector("img"),
          imageUrl: display?.keyframeUrl ? `${API_URL}${display.keyframeUrl}` : undefined,
          grab: { x: current.x, y: current.y },
        });
        notify("start", event.clientX, event.clientY);
      }
      event.preventDefault();
      carry.current?.move(event.clientX, event.clientY);
      notify("move", event.clientX, event.clientY);
    },
    onPointerUp(event: PointerEvent<HTMLElement>) {
      const current = gesture.current;
      if (!current || current.pointer !== event.pointerId) return;
      let taken = false;
      if (current.active) {
        event.preventDefault();
        suppressUntil.current = performance.now() + 250;
        taken = notify("drop", event.clientX, event.clientY);
      }
      clear(taken ? "taken" : "returned");
    },
    onPointerCancel(event: PointerEvent<HTMLElement>) {
      if (gesture.current?.pointer !== event.pointerId) return;
      clear("returned");
    },
    onLostPointerCapture(event: PointerEvent<HTMLElement>) {
      if (gesture.current?.pointer !== event.pointerId) return;
      clear("returned");
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
