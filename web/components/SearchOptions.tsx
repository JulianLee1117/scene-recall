"use client";

import { useEffect, useId, useRef, useState } from "react";

export type ResultGrouping = "all" | "best-per-movie";

interface SearchOptionsProps {
  resultGrouping: ResultGrouping;
  onResultGroupingChange: (grouping: ResultGrouping) => void;
  showRankingDetails: boolean;
  onShowRankingDetailsChange: (enabled: boolean) => void;
  compositionOtherMovies: boolean;
  onCompositionOtherMoviesChange: (enabled: boolean) => void;
  compositionScopeLocked: boolean;
  compositionUseCurrentText: boolean;
  onCompositionUseCurrentTextChange: (enabled: boolean) => void;
}

export default function SearchOptions({
  resultGrouping,
  onResultGroupingChange,
  showRankingDetails,
  onShowRankingDetailsChange,
  compositionOtherMovies,
  onCompositionOtherMoviesChange,
  compositionScopeLocked,
  compositionUseCurrentText,
  onCompositionUseCurrentTextChange,
}: SearchOptionsProps) {
  const [isOpen, setIsOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const headingId = useId();
  const groupingName = useId();
  const changedOptionCount = [
    resultGrouping !== "all",
    showRankingDetails,
    !compositionOtherMovies,
    compositionUseCurrentText,
  ].filter(Boolean).length;

  useEffect(() => {
    if (!isOpen) return;

    const handlePointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setIsOpen(false);
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setIsOpen(false);
      triggerRef.current?.focus();
    };

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    window.requestAnimationFrame(() => {
      panelRef.current?.querySelector<HTMLInputElement>("input")?.focus();
    });
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen]);

  const reset = () => {
    onResultGroupingChange("all");
    onShowRankingDetailsChange(false);
    onCompositionOtherMoviesChange(true);
    onCompositionUseCurrentTextChange(false);
  };

  return (
    <div className="search-options" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="search-options-trigger"
        aria-expanded={isOpen}
        aria-controls={panelId}
        aria-haspopup="dialog"
        onClick={() => setIsOpen((open) => !open)}
      >
        <svg
          width="13"
          height="13"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          aria-hidden="true"
        >
          <path d="M4 6h16M7 12h10M10 18h4" />
          <circle cx="9" cy="6" r="1.6" fill="currentColor" stroke="none" />
          <circle cx="15" cy="12" r="1.6" fill="currentColor" stroke="none" />
          <circle cx="12" cy="18" r="1.6" fill="currentColor" stroke="none" />
        </svg>
        <span>Options</span>
        {changedOptionCount > 0 && (
          <span className="search-options-count" aria-label={`${changedOptionCount} changed options`}>
            {changedOptionCount}
          </span>
        )}
        <svg
          className="search-options-chevron"
          width="10"
          height="10"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          aria-hidden="true"
        >
          <path d="m4 6 4 4 4-4" />
        </svg>
      </button>

      {isOpen && (
        <div
          ref={panelRef}
          id={panelId}
          className="search-options-popover"
          role="dialog"
          aria-labelledby={headingId}
        >
          <div className="search-options-heading">
            <span id={headingId}>Search options</span>
            <div className="search-options-heading-actions">
              {changedOptionCount > 0 && (
                <button type="button" onClick={reset}>
                  Reset
                </button>
              )}
              <button
                type="button"
                className="search-options-close"
                aria-label="Close search options"
                onClick={() => {
                  setIsOpen(false);
                  triggerRef.current?.focus();
                }}
              >
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 16 16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  aria-hidden="true"
                >
                  <path d="m4 4 8 8M12 4l-8 8" />
                </svg>
              </button>
            </div>
          </div>

          <fieldset className="search-options-section">
            <legend>Results</legend>
            <label className="search-option-row">
              <input
                type="radio"
                name={groupingName}
                checked={resultGrouping === "all"}
                onChange={() => onResultGroupingChange("all")}
              />
              <span className="search-option-indicator" aria-hidden="true" />
              <span className="search-option-copy">
                <span>All ranked scenes</span>
                <span>Keep every returned scene in relevance order.</span>
              </span>
            </label>
            <label className="search-option-row">
              <input
                type="radio"
                name={groupingName}
                checked={resultGrouping === "best-per-movie"}
                onChange={() => onResultGroupingChange("best-per-movie")}
              />
              <span className="search-option-indicator" aria-hidden="true" />
              <span className="search-option-copy">
                <span>Best scene per represented movie</span>
                <span>Keeps the highest-ranked scene from each movie already returned.</span>
              </span>
            </label>
          </fieldset>

          <fieldset className="search-options-section">
            <legend>When matching a result</legend>
            <label className="search-option-row">
              <input
                type="checkbox"
                checked={compositionOtherMovies}
                disabled={compositionScopeLocked}
                onChange={(event) =>
                  onCompositionOtherMoviesChange(event.target.checked)
                }
              />
              <span className="search-option-indicator" aria-hidden="true" />
              <span className="search-option-copy">
                <span>Search other movies only</span>
                <span>
                  {compositionScopeLocked
                    ? "Single-movie scope controls which movie is searched."
                    : "Remove the source movie before composition candidates are chosen."}
                </span>
              </span>
            </label>
            <label className="search-option-row">
              <input
                type="checkbox"
                checked={compositionUseCurrentText}
                onChange={(event) =>
                  onCompositionUseCurrentTextChange(event.target.checked)
                }
              />
              <span className="search-option-indicator" aria-hidden="true" />
              <span className="search-option-copy">
                <span>Keep current text as a constraint</span>
                <span>Otherwise the result action starts a composition-only search.</span>
              </span>
            </label>
          </fieldset>

          <fieldset className="search-options-section">
            <legend>Advanced</legend>
            <label className="search-option-row">
              <input
                type="checkbox"
                checked={showRankingDetails}
                onChange={(event) =>
                  onShowRankingDetailsChange(event.target.checked)
                }
              />
              <span className="search-option-indicator" aria-hidden="true" />
              <span className="search-option-copy">
                <span>Show ranking details</span>
                <span>Display channel ranks, scores, and matched evidence.</span>
              </span>
            </label>
          </fieldset>
        </div>
      )}
    </div>
  );
}
