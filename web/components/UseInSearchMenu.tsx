"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import FacetIcon from "./FacetIcon";
import { FACET_LABELS, MATCH_FACETS } from "@/lib/searchRecipe";
import { SEARCH_CLUE_COPY } from "@/lib/searchClues";
import type { RecipeMatchFacet, SearchResult } from "@/types/api";

interface UseInSearchMenuProps {
  shot: SearchResult;
  onUse: (shot: SearchResult, facet: RecipeMatchFacet) => void;
  disabled?: boolean;
  disabledFacets?: ReadonlySet<RecipeMatchFacet>;
  variant?: "card" | "modal";
}

export default function UseInSearchMenu({
  shot,
  onUse,
  disabled = false,
  disabledFacets,
  variant = "card",
}: UseInSearchMenuProps) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [position, setPosition] = useState<{ top: number; left: number; maxHeight: number } | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useLayoutEffect(() => {
    if (!open) return;
    const positionMenu = () => {
      const trigger = triggerRef.current?.getBoundingClientRect();
      if (!trigger) return;
      const width = Math.min(290, window.innerWidth - 24);
      const height = Math.min(370, window.innerHeight - 24);
      const below = trigger.bottom + 6;
      const top = below + height <= window.innerHeight - 12
        ? below
        : Math.max(12, trigger.top - height - 6);
      setPosition({
        top,
        left: Math.max(12, Math.min(trigger.right - width, window.innerWidth - width - 12)),
        maxHeight: window.innerHeight - top - 12,
      });
    };
    positionMenu();
    window.addEventListener("resize", positionMenu);
    window.addEventListener("scroll", positionMenu, true);
    return () => {
      window.removeEventListener("resize", positionMenu);
      window.removeEventListener("scroll", positionMenu, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;

    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node) && !menuRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
        triggerRef.current?.focus();
        return;
      }
      if (event.key === "Tab" && menuRef.current?.contains(event.target as Node)) {
        setOpen(false);
        triggerRef.current?.focus();
        return;
      }

      const items = Array.from(
        menuRef.current?.querySelectorAll<HTMLButtonElement>(
          "[role=menuitem]:not(:disabled)",
        ) ?? [],
      );
      if (!items.length || (!rootRef.current?.contains(event.target as Node) && !menuRef.current?.contains(event.target as Node))) {
        return;
      }

      const focusedIndex = items.indexOf(
        document.activeElement as HTMLButtonElement,
      );
      const currentIndex = focusedIndex >= 0 ? focusedIndex : 0;
      let nextIndex: number | null = null;
      if (event.key === "ArrowDown" || event.key === "ArrowRight") {
        nextIndex = (currentIndex + 1) % items.length;
      } else if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
        nextIndex = (currentIndex - 1 + items.length) % items.length;
      } else if (event.key === "Home") {
        nextIndex = 0;
      } else if (event.key === "End") {
        nextIndex = items.length - 1;
      }
      if (nextIndex === null) return;
      event.preventDefault();
      event.stopPropagation();
      items[nextIndex].focus();
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    window.requestAnimationFrame(() => {
      menuRef.current
        ?.querySelector<HTMLButtonElement>("[role=menuitem]:not(:disabled)")
        ?.focus();
    });
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  return (
    <div
      ref={rootRef}
      className={`use-in-search use-in-search-${variant}`}
      onDragStart={(event) => event.preventDefault()}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget) && !menuRef.current?.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        className={variant === "card" ? "result-card-action use-in-search-trigger" : "use-in-search-trigger"}
        disabled={disabled}
        aria-label="Find related scenes"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        title="Find scenes related to this one"
        onClick={() => {
          const firstEnabled = MATCH_FACETS.findIndex(
            (facet) => !disabledFacets?.has(facet),
          );
          setActiveIndex(Math.max(0, firstEnabled));
          setOpen((current) => !current);
        }}
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.7"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <circle cx="10.5" cy="10.5" r="6.5" />
          <path d="m15.5 15.5 4 4M10.5 7.5v6M7.5 10.5h6" />
        </svg>
        <span>Related</span>
      </button>

      {open && position && createPortal(
        <div
          ref={menuRef}
          id={menuId}
          className="use-in-search-menu use-in-search-context-menu"
          style={position}
          role="menu"
          aria-label="Find related scenes by"
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget) && !rootRef.current?.contains(event.relatedTarget)) setOpen(false);
          }}
        >
          <p className="use-in-search-menu-title" aria-hidden="true">Find scenes with similar…</p>
          {MATCH_FACETS.map((facet, index) => (
            <button
              key={facet}
              type="button"
              role="menuitem"
              disabled={disabledFacets?.has(facet)}
              tabIndex={
                !disabledFacets?.has(facet) && index === activeIndex ? 0 : -1
              }
              aria-label={`Similar ${FACET_LABELS[facet]}`}
              title={
                disabledFacets?.has(facet)
                  ? "Remove a clue to add this one"
                  : SEARCH_CLUE_COPY[facet].description
              }
              onFocus={() => setActiveIndex(index)}
              onClick={() => {
                if (disabledFacets?.has(facet)) return;
                setOpen(false);
                triggerRef.current?.focus();
                onUse(shot, facet);
              }}
            >
              <FacetIcon facet={facet} size={15} />
              <span>
                <strong>{FACET_LABELS[facet]}</strong>
                <small>{SEARCH_CLUE_COPY[facet].description}</small>
              </span>
            </button>
          ))}
        </div>,
        document.body,
      )}
    </div>
  );
}
