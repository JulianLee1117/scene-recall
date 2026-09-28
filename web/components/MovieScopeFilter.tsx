"use client";

import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import type { LibraryFilm } from "@/types/api";
import { useSearchFilms } from "@/hooks/useSearchFilms";
import { displayFilmTitle as displayTitle } from "@/lib/movieSuggestions";
import DirectionIcon from "./DirectionIcon";

const SEARCH_THRESHOLD = 6;

interface MovieScopeFilterProps {
  selectedFilmIds: string[];
  onChange: (filmIds: string[]) => void;
  films?: LibraryFilm[];
}

export default function MovieScopeFilter({
  selectedFilmIds,
  onChange,
  films: suppliedFilms,
}: MovieScopeFilterProps) {
  const catalog = useSearchFilms(suppliedFilms === undefined);
  const films = suppliedFilms ?? catalog;
  const [isOpen, setIsOpen] = useState(false);
  const [filterQuery, setFilterQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const popoverId = useId();

  const indexedFilms = useMemo(
    () =>
      films
        .filter(
          (film): film is LibraryFilm & { film_id: string } =>
            film.status === "indexed" &&
            typeof film.film_id === "string" &&
            film.film_id.length > 0,
        )
        .sort((left, right) =>
          displayTitle(left).localeCompare(displayTitle(right), undefined, {
            sensitivity: "base",
          }),
        ),
    [films],
  );

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
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen]);

  useEffect(() => {
    if (isOpen && indexedFilms.length >= SEARCH_THRESHOLD) {
      window.requestAnimationFrame(() => searchRef.current?.focus());
    }
    if (!isOpen) setFilterQuery("");
  }, [indexedFilms.length, isOpen]);

  const selectedSet = useMemo(
    () => new Set(selectedFilmIds),
    [selectedFilmIds],
  );
  const selectedFilm =
    selectedFilmIds.length === 1
      ? indexedFilms.find((film) => film.film_id === selectedFilmIds[0])
      : undefined;

  const filteredFilms = useMemo(() => {
    const normalized = filterQuery.trim().toLocaleLowerCase();
    if (!normalized) return indexedFilms;
    return indexedFilms.filter((film) =>
      `${displayTitle(film)} ${film.filename}`
        .toLocaleLowerCase()
        .includes(normalized),
    );
  }, [filterQuery, indexedFilms]);

  if (indexedFilms.length === 0 && selectedFilmIds.length === 0) return null;

  const scopeLabel =
    selectedFilmIds.length === 0
      ? "All movies"
      : selectedFilmIds.length === 1
        ? selectedFilm
          ? displayTitle(selectedFilm)
          : "1 movie"
        : `${selectedFilmIds.length} movies`;

  const toggleFilm = (filmId: string) => {
    const nextSelection = new Set(selectedFilmIds);
    if (nextSelection.has(filmId)) nextSelection.delete(filmId);
    else nextSelection.add(filmId);
    onChange([...nextSelection]);
  };

  return (
    <div className="movie-scope" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="movie-scope-trigger"
        aria-expanded={isOpen}
        aria-controls={popoverId}
        aria-haspopup="dialog"
        title={`Search scope: ${scopeLabel}`}
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
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <rect x="3" y="5" width="18" height="14" rx="2" />
          <path d="M7 5v14M17 5v14M3 9h4M17 9h4M3 15h4M17 15h4" />
        </svg>
        <span className="movie-scope-trigger-label">{scopeLabel}</span>
        <DirectionIcon
          name="chevron-down"
          className="movie-scope-chevron"
          size={12}
        />
      </button>

      {isOpen && (
        <div
          id={popoverId}
          className="movie-scope-popover"
          role="dialog"
          aria-label="Choose movies to search"
        >
          <div className="movie-scope-heading">
            <span>Search in</span>
            <span>
              {indexedFilms.length} movie
              {indexedFilms.length === 1 ? "" : "s"}
            </span>
          </div>

          {indexedFilms.length >= SEARCH_THRESHOLD && (
            <div className="movie-scope-search-wrap">
              <svg
                width="13"
                height="13"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                aria-hidden="true"
              >
                <circle cx="11" cy="11" r="7" />
                <path d="m20 20-4-4" />
              </svg>
              <input
                ref={searchRef}
                className="movie-scope-search"
                type="search"
                value={filterQuery}
                onChange={(event) => setFilterQuery(event.target.value)}
                placeholder="Find a movie"
                aria-label="Find a movie"
              />
            </div>
          )}

          <div
            className="movie-scope-options"
            role="listbox"
            aria-label="Movie search scope"
            aria-multiselectable="true"
          >
            {!filterQuery && (
              <button
                type="button"
                className="movie-scope-option movie-scope-option-all"
                role="option"
                aria-selected={selectedFilmIds.length === 0}
                onClick={() => onChange([])}
              >
                <span className="movie-scope-check" aria-hidden="true">
                  {selectedFilmIds.length === 0 ? "✓" : ""}
                </span>
                <span className="movie-scope-option-copy">
                  <span>All movies</span>
                  <span>Entire indexed library</span>
                </span>
              </button>
            )}

            {filteredFilms.map((film) => {
              const selected = selectedSet.has(film.film_id);
              return (
                <button
                  type="button"
                  className="movie-scope-option"
                  role="option"
                  aria-selected={selected}
                  key={film.film_id}
                  onClick={() => toggleFilm(film.film_id)}
                >
                  <span className="movie-scope-check" aria-hidden="true">
                    {selected ? "✓" : ""}
                  </span>
                  <span className="movie-scope-option-copy">
                    <span>{displayTitle(film)}</span>
                    <span>
                      {typeof film.duration === "number"
                        ? `${Math.max(1, Math.round(film.duration / 60))} min`
                        : "Indexed"}
                    </span>
                  </span>
                </button>
              );
            })}

            {filteredFilms.length === 0 && (
              <p className="movie-scope-empty">No matching movies</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
