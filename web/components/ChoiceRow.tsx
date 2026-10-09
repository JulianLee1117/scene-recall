"use client";

import { useRef, type KeyboardEvent } from "react";

/**
 * One setting in a toolbar menu: its label, then one choice among a few.
 * Something is always chosen, starting at the default ("Any" for a filter);
 * a choice other than the default is marked, like a set movie filter.
 */
export default function ChoiceRow<T extends string>({
  label,
  options,
  value,
  defaultValue,
  onChange,
}: {
  label: string;
  options: ReadonlyArray<{ value: T; label: string }>;
  value: T;
  defaultValue: T;
  onChange: (value: T) => void;
}) {
  const groupRef = useRef<HTMLDivElement>(null);
  // Like a native segmented control, Tab enters at the choice and the arrow
  // keys move it.
  const step = (event: KeyboardEvent, index: number) => {
    const delta = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!delta) return;
    event.preventDefault();
    const next = (index + delta + options.length) % options.length;
    onChange(options[next].value);
    groupRef.current?.querySelectorAll<HTMLButtonElement>("button")[next]?.focus();
  };

  return (
    <div className="toolbar-menu-setting">
      <span>{label}</span>
      <div ref={groupRef} className="toolbar-menu-choices" role="radiogroup" aria-label={label}>
        {options.map((option, index) => {
          const chosen = option.value === value;
          return (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={chosen}
              tabIndex={chosen ? 0 : -1}
              className={chosen && value !== defaultValue ? "is-set" : undefined}
              onClick={() => onChange(option.value)}
              onKeyDown={(event) => step(event, index)}
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
