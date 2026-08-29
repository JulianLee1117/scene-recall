"use client";

import { useEffect, useId, useRef, useState } from "react";
import FacetIcon from "./FacetIcon";
import { FACET_LABELS, MATCH_FACETS } from "@/lib/searchRecipe";
import type { RecipeMatchFacet, SearchResult } from "@/types/api";

interface UseInSearchMenuProps {
  shot: SearchResult;
  onUse: (shot: SearchResult, facet: RecipeMatchFacet) => void;
  disabled?: boolean;
  variant?: "card" | "modal";
}

export default function UseInSearchMenu({
  shot,
  onUse,
  disabled = false,
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
        menuRef.current?.querySelectorAll<HTMLButtonElement>("[role=menuitem]") ??
          [],
      );
      if (!items.length || !rootRef.current?.contains(event.target as Node)) {
        return;
      }

      const currentIndex = items.indexOf(
        document.activeElement as HTMLButtonElement,
      );
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
      setActiveIndex(nextIndex);
      items[nextIndex].focus();
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    window.requestAnimationFrame(() => {
      menuRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
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
        aria-label="Use scene in search"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        title="Use in search"
        onClick={() => {
          setActiveIndex(0);
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
        {variant === "modal" && <span>Use in search</span>}
      </button>

      {open && (
        <div
          ref={menuRef}
          id={menuId}
          className="use-in-search-menu"
          role="menu"
          aria-label="Use scene as"
        >
          {MATCH_FACETS.map((facet, index) => (
            <button
              key={facet}
              type="button"
              role="menuitem"
              tabIndex={index === activeIndex ? 0 : -1}
              aria-label={`Use scene for ${FACET_LABELS[facet]}`}
              title={FACET_LABELS[facet]}
              onFocus={() => setActiveIndex(index)}
              onClick={() => {
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
