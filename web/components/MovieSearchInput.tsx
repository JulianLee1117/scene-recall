"use client";

import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type RefObject } from "react";
import { getMovieMentionRange, getMovieSuggestions, type MovieSuggestion } from "@/lib/movieSuggestions";
import { acceptMovieMention, movieCompletionText, removeMovieMention, validMovieMentions, type MovieMention } from "@/lib/movieMentions";
import type { LibraryFilm } from "@/types/api";

interface Props {
  inputRef: RefObject<HTMLInputElement | null>;
  value: string;
  films: readonly LibraryFilm[];
  mentions: readonly MovieMention[];
  suggestionsEnabled: boolean;
  placeholder: string;
  describedBy?: string;
  onChange: (value: string, caret?: number, composing?: boolean) => void;
  onKeyDown: (event: KeyboardEvent<HTMLInputElement>) => void;
  onMovieSelect: (suggestion: MovieSuggestion) => void;
  /** Example searches the empty bar shows in its placeholder, once, while it has focus. */
  hints?: readonly string[];
}

/** How long the placeholder, then each example, holds; then the closing fade back. */
const HINT_FIRST_MS = 2600;
const HINT_MS = 2200;
const HINT_SETTLE_MS = 600;

/** Native text editing, with a visual mention layer and a floating catalog picker. */
export default function MovieSearchInput(props: Props) {
  const { inputRef, value, films, onMovieSelect } = props;
  const id = useId();
  const wrapperRef = useRef<HTMLDivElement>(null);
  const measureRef = useRef<HTMLSpanElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const pendingCaretRef = useRef<{ value: string; position: number } | null>(null);
  const pendingRevealRef = useRef(false);
  const [focused, setFocused] = useState(false);
  const [composing, setComposing] = useState(false);
  const [selection, setSelection] = useState({ value, start: value.length, end: value.length });
  const [scrollLeft, setScrollLeft] = useState(0);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const [completionCancelled, setCompletionCancelled] = useState(false);
  const [choice, setChoice] = useState<{ key: string; filmId: string } | null>(null);
  const [position, setPosition] = useState<{ left: number; top: number; width: number } | null>(null);
  // Examples crossfade through the placeholder once while the empty bar has
  // focus, starting and ending on the placeholder itself (step 0 and the last
  // step). Typing or leaving the bar stops them; reduced motion keeps it still.
  const [hintStep, setHintStep] = useState(-1);
  const hintsDone = useRef(false);
  const hintCount = props.hints?.length ?? 0;
  const canHint = focused && !composing && !value && hintCount > 0;
  const hintText = (step: number) => (step === 0 || step > hintCount ? props.placeholder : props.hints![step - 1]);
  const caret = selection.value === value ? selection.start : value.length;
  const collapsed = selection.value !== value || selection.start === selection.end;
  const key = `${value}\u0000${caret}`;
  const confirmed = useMemo(() => validMovieMentions({ text: value, mentions: props.mentions }), [value, props.mentions]);
  const completionText = useMemo(() => movieCompletionText({ text: value, mentions: confirmed }), [value, confirmed]);
  const mention = useMemo(() => getMovieMentionRange(completionText, caret, films), [completionText, caret, films]);
  const suggestions = useMemo(() => getMovieSuggestions(completionText, films, [], caret), [completionText, films, caret]);
  const open = focused && !composing && collapsed && !completionCancelled && props.suggestionsEnabled && dismissed !== key && Boolean(mention || suggestions.length);
  const chosenIndex = choice?.key === key ? suggestions.findIndex((item) => item.film.film_id === choice.filmId) : -1;
  // @ explicitly starts completion. Ordinary title recognition never takes Enter.
  const activeIndex = chosenIndex >= 0 ? chosenIndex : mention && choice?.key !== key && suggestions.length ? 0 : -1;
  const active = open && activeIndex >= 0 ? suggestions[activeIndex] : undefined;
  const optionId = (filmId: string) => `${id}-${filmId}`;

  // Autofocus can land before hydration, when no focus event reaches React.
  useLayoutEffect(() => {
    const input = inputRef.current;
    if (input && input.ownerDocument?.activeElement === input) setFocused(true);
  }, [inputRef]);

  useEffect(() => {
    if (!canHint) {
      if (hintStep >= 0) {
        hintsDone.current = true;
        setHintStep(-1);
      }
      return;
    }
    if (hintsDone.current) return;
    if (hintStep < 0) {
      if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) hintsDone.current = true;
      else setHintStep(0);
      return;
    }
    const last = hintStep > hintCount;
    const timer = window.setTimeout(() => {
      if (!last) return setHintStep(hintStep + 1);
      hintsDone.current = true;
      setHintStep(-1);
    }, hintStep === 0 ? HINT_FIRST_MS : last ? HINT_SETTLE_MS : HINT_MS);
    return () => window.clearTimeout(timer);
  }, [canHint, hintStep, hintCount]);

  function syncSelection(input: HTMLInputElement) {
    const next = { value: input.value, start: input.selectionStart ?? input.value.length, end: input.selectionEnd ?? input.value.length };
    setSelection((old) => old.value === next.value && old.start === next.start && old.end === next.end ? old : next);
    setScrollLeft(input.scrollLeft ?? 0);
  }

  function accept(suggestion: MovieSuggestion) {
    const next = acceptMovieMention({ text: value, mentions: confirmed }, suggestion);
    pendingCaretRef.current = { value: next.draft.text, position: next.caret };
    setDismissed(`${next.draft.text}\u0000${next.caret}`);
    setChoice(null);
    onMovieSelect(suggestion);
    inputRef.current?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (composing || event.nativeEvent?.isComposing) return;
    const start = event.currentTarget?.selectionStart ?? caret;
    const end = event.currentTarget?.selectionEnd ?? caret;
    if (start === end && !event.altKey && !event.ctrlKey && !event.metaKey) {
      const adjacent = confirmed.find((item) => (event.key === "Backspace" && item.end === start)
        || (event.key === "Delete" && item.start === start));
      if (adjacent) {
        event.preventDefault();
        const next = removeMovieMention({ text: value, mentions: confirmed }, adjacent);
        pendingCaretRef.current = { value: next.draft.text, position: next.caret };
        setDismissed(`${next.draft.text}\u0000${next.caret}`);
        props.onChange(next.draft.text, next.caret);
        return;
      }
    }
    if (open) {
      if (event.key === "Escape") {
        event.preventDefault();
        setCompletionCancelled(true);
        setChoice(null);
        if (mention) {
          const nextValue = value.slice(0, mention.start) + value.slice(mention.start + 1);
          const nextCaret = Math.max(mention.start, caret - 1);
          pendingCaretRef.current = { value: nextValue, position: nextCaret };
          setDismissed(`${nextValue}\u0000${nextCaret}`);
          props.onChange(nextValue, nextCaret);
        } else {
          setDismissed(key);
        }
        return;
      }
      if ((event.key === "ArrowDown" || event.key === "ArrowUp") && suggestions.length) {
        event.preventDefault();
        const next = activeIndex < 0 ? (event.key === "ArrowDown" ? 0 : suggestions.length - 1)
          : (activeIndex + (event.key === "ArrowDown" ? 1 : -1) + suggestions.length) % suggestions.length;
        setChoice({ key, filmId: suggestions[next].film.film_id });
        return;
      }
      if ((event.key === "Enter" || (event.key === "Tab" && !event.shiftKey)) && active) {
        event.preventDefault();
        accept(active);
        return;
      }
    }
    if (event.key === "Enter") setDismissed(key);
    props.onKeyDown(event);
  }

  useLayoutEffect(() => {
    if (!value) setCompletionCancelled(false);
    const pending = pendingCaretRef.current;
    if (!pending || pending.value !== value || !inputRef.current) return;
    inputRef.current.setSelectionRange(pending.position, pending.position);
    pendingRevealRef.current = true;
    syncSelection(inputRef.current);
    pendingCaretRef.current = null;
  }, [value, inputRef, props.mentions]);

  useLayoutEffect(() => {
    const input = inputRef.current, measure = measureRef.current;
    if (!input || !measure) return;
    const reveal = () => {
      if (!focused || composing || input.selectionStart !== input.selectionEnd || input.selectionStart !== caret) return;
      const width = input.clientWidth;
      if (!width) return;
      const x = measure.offsetWidth, left = input.scrollLeft;
      const next = x < left ? x : x > left + width - 4 ? x - width + 4 : left;
      input.scrollLeft = Math.max(0, Math.min(next, input.scrollWidth - width));
      setScrollLeft(input.scrollLeft);
      pendingRevealRef.current = false;
    };
    // setSelectionRange does not consistently scroll after controlled text
    // replacement. Wait for the mirror to measure the new caret's prefix.
    if (pendingRevealRef.current) reveal();
    let width = input.clientWidth;
    const observer = new ResizeObserver(() => {
      if (input.clientWidth === width) return;
      width = input.clientWidth;
      reveal();
    });
    observer.observe(input);
    return () => observer.disconnect();
  }, [value, caret, focused, composing, inputRef, props.mentions]);

  useLayoutEffect(() => {
    if (!open) { setPosition(null); return; }
    const wrapper = wrapperRef.current;
    const measure = measureRef.current;
    const input = inputRef.current;
    if (!wrapper || !measure || !input) return;
    const update = () => {
      const rect = wrapper.getBoundingClientRect();
      const bar = wrapper.closest(".search-text-entry")?.getBoundingClientRect() ?? rect;
      const viewportWidth = document.documentElement.clientWidth;
      const width = Math.min(304, viewportWidth - 32);
      const caretX = rect.left + Math.max(0, Math.min(rect.width, measure.offsetWidth - input.scrollLeft));
      const x = Math.max(16, Math.min(caretX, viewportWidth - width - 16));
      const height = popoverRef.current?.offsetHeight ?? 120;
      const below = bar.bottom + 8;
      const top = below + height > window.innerHeight - 12 && bar.top > height + 20 ? bar.top - height - 8 : below;
      const next = { left: x - rect.left, top: top - rect.top, width };
      setPosition((old) => old?.left === next.left && old?.top === next.top && old?.width === next.width ? old : next);
      setScrollLeft(input.scrollLeft);
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(wrapper);
    if (popoverRef.current) observer.observe(popoverRef.current);
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    input.addEventListener("scroll", update);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
      input.removeEventListener("scroll", update);
    };
  }, [open, value, caret, inputRef, suggestions.length]);

  const painted = [];
  let paintedEnd = 0;
  const ranges = [...confirmed.map((item) => ({ ...item, confirmed: true })),
    ...(mention && !completionCancelled ? [{ ...mention, confirmed: false }] : [])].sort((a, b) => a.start - b.start);
  for (const range of ranges) {
    if (range.start < paintedEnd) continue;
    painted.push(value.slice(paintedEnd, range.start));
    painted.push(<mark key={`${range.start}-${range.end}`} className={range.confirmed ? "movie-keyword movie-keyword-confirmed" : "movie-keyword"}>{value.slice(range.start, range.end)}</mark>);
    paintedEnd = range.end;
  }
  painted.push(value.slice(paintedEnd));

  return (
    <div className={`movie-search-input${composing ? " is-composing" : ""}${hintStep >= 0 ? " is-hinting" : ""}`} ref={wrapperRef}>
      <div className="movie-input-paint" aria-hidden="true">
        <span className="movie-caret-measure" ref={measureRef}>{value.slice(0, caret)}</span>
        <span className="movie-input-text" style={{ transform: `translateX(${-scrollLeft}px)` }}>
          {painted}
        </span>
        {hintStep > 0 && <span key={`out-${hintStep}`} className="movie-input-hint is-leaving">{hintText(hintStep - 1)}</span>}
        {hintStep >= 0 && (
          <span key={`in-${hintStep}`} className={`movie-input-hint${hintStep === 0 ? " is-still" : ""}`}>{hintText(hintStep)}</span>
        )}
      </div>
      <input
        ref={inputRef} className="search-main-input" type="text" value={value} maxLength={500}
        role="combobox" aria-autocomplete="list" aria-haspopup="listbox"
        aria-expanded={open} aria-controls={open && suggestions.length ? `${id}-list` : undefined}
        aria-activedescendant={active ? optionId(active.film.film_id) : undefined}
        aria-label="Describe a scene" aria-describedby={[props.describedBy, confirmed.length ? `${id}-scope` : undefined].filter(Boolean).join(" ") || undefined}
        placeholder={props.placeholder} autoComplete="off" spellCheck={false} autoFocus
        onChange={(event) => {
          setFocused(true);
          syncSelection(event.target);
          setChoice(null);
          if (completionCancelled) {
            // Continuing an escaped phrase is plain text. Only a fresh @
            // trigger (typed or pasted), or clearing the field, starts again.
            const nextValue = event.target.value;
            let start = 0;
            while (start < value.length && start < nextValue.length && value[start] === nextValue[start]) start += 1;
            let oldEnd = value.length, newEnd = nextValue.length;
            while (oldEnd > start && newEnd > start && value[oldEnd - 1] === nextValue[newEnd - 1]) { oldEnd -= 1; newEnd -= 1; }
            const inserted = nextValue.slice(start, newEnd);
            const freshTrigger = Array.from(inserted.matchAll(/@/g)).some((match) => {
              const at = start + match.index!;
              return at === 0 || /\s/.test(nextValue[at - 1]);
            });
            if (!nextValue || freshTrigger) setCompletionCancelled(false);
          }
          props.onChange(event.target.value, event.target.selectionStart ?? event.target.value.length,
            composing || (event.nativeEvent as InputEvent | undefined)?.isComposing);
        }}
        onSelect={(event) => syncSelection(event.currentTarget)}
        onScroll={(event) => setScrollLeft(event.currentTarget.scrollLeft)}
        onFocus={() => { setFocused(true); setDismissed(null); }}
        onBlur={() => { setFocused(false); setChoice(null); }}
        onCompositionStart={() => setComposing(true)}
        onCompositionEnd={(event) => {
          setComposing(false);
          syncSelection(event.currentTarget);
          props.onChange(event.currentTarget.value, event.currentTarget.selectionStart ?? event.currentTarget.value.length, false);
        }}
        onKeyDown={onKeyDown}
      />
      {confirmed.length > 0 && <span id={`${id}-scope`} className="sr-only">Movie filters: {[...new Set(confirmed.map((item) => item.text.slice(1)))].join(", ")}. Backspace after or Delete before a movie removes its filter. Editing its title makes it ordinary text.</span>}
      {open && (
        <div className="movie-autocomplete" ref={popoverRef}
          style={{ left: position?.left ?? 0, top: position?.top ?? "100%", width: position?.width, visibility: position ? "visible" : "hidden" }}
          onPointerDown={(event) => event.preventDefault()}>
          <div className="movie-autocomplete-heading"><span className="movie-mention-symbol" aria-hidden="true">@</span><span>Movies</span><span>Filter your search</span></div>
          {suggestions.length ? (
            <div id={`${id}-list`} role="listbox" aria-label="Movies">
              {suggestions.map((suggestion, index) => {
                const title = suggestion.title.replace(/\s+[([]\d{4}[)\]]$/, "");
                const year = suggestion.title.match(/\s+[([](\d{4})[)\]]$/)?.[1];
                return <button key={suggestion.film.film_id} id={optionId(suggestion.film.film_id)} type="button"
                  className="movie-autocomplete-option" role="option" tabIndex={-1} aria-selected={index === activeIndex}
                  aria-label={`Use ${suggestion.title} as a movie filter`}
                  onPointerMove={() => setChoice({ key, filmId: suggestion.film.film_id })}
                  onClick={() => accept(suggestion)}>
                  <span className="movie-option-icon" aria-hidden="true"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M7 4v16M17 4v16M3 9h4m-4 6h4m10-6h4m-4 6h4" /></svg></span>
                  <span className="movie-option-title">{title}</span>{year && <span className="movie-option-year">{year}</span>}
                  <span className="movie-option-enter" aria-hidden="true">↵</span>
                </button>;
              })}
            </div>
          ) : <p className="movie-autocomplete-empty" role="status">{mention && value.slice(mention.start, mention.end) === "@" ? "Type a movie name…" : "No matching movies in your library"}</p>}
          {suggestions.length > 0 && <div className="movie-autocomplete-footer"><span><kbd>↑</kbd><kbd>↓</kbd> navigate</span><span><kbd>{active ? "↵" : "↓"}</kbd> {active ? "select" : "choose"}</span><span><kbd>esc</kbd> close</span></div>}
        </div>
      )}
    </div>
  );
}
