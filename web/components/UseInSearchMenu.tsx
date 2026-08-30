"use client";

import { useEffect, useId, useRef, useState } from "react";
import FacetIcon from "./FacetIcon";
import { FACET_LABELS, MATCH_FACETS } from "@/lib/searchRecipe";
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
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  useEffect(() => {
    if (!open) return;

    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
        triggerRef.current?.focus();
        return;
      }

      const items = Array.from(
        menuRef.current?.querySelectorAll<HTMLButtonElement>(
          "[role=menuitem]:not(:disabled)",
        ) ?? [],
      );
      if (!items.length || !rootRef.current?.contains(event.target as Node)) {
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
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        className={variant === "card" ? "result-card-action use-in-search-trigger" : "use-in-search-trigger"}
        disabled={disabled}
        aria-label="Choose how to match this scene"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        title="Match by…"
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
        {variant === "modal" && <span>Match by…</span>}
      </button>

      {open && (
        <div
          ref={menuRef}
          id={menuId}
          className="use-in-search-menu"
          role="menu"
          aria-label="Choose what to match"
        >
          {MATCH_FACETS.map((facet, index) => (
            <button
              key={facet}
              type="button"
              role="menuitem"
              disabled={disabledFacets?.has(facet)}
              tabIndex={
                !disabledFacets?.has(facet) && index === activeIndex ? 0 : -1
              }
              aria-label={`Use scene for ${FACET_LABELS[facet]}`}
              title={
                disabledFacets?.has(facet)
                  ? "Remove a match to use this category"
                  : FACET_LABELS[facet]
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
              <span>{FACET_LABELS[facet]}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
