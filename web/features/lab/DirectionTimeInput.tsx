"use client";

import { useEffect, useId, useRef, useState } from "react";
import styles from "./musicDirection.module.css";

const format = (value: number) => String(Number(value.toFixed(3)));
const parsed = (text: string) => /^[-+]?(?:\d+\.?\d*|\.\d+)$/.test(text.trim()) ? Number(text) : NaN;

/** Keep partial typing local; valid edits reach the project before Save or Generate. */
export default function DirectionTimeInput({ label, value, min, max, disabled, onChange, onEndChange }: {
  label: string;
  value: number;
  min: number;
  max: number;
  disabled: boolean;
  onChange: (value: number) => void;
  onEndChange: () => void;
}) {
  const hintId = useId();
  const [draft, setDraft] = useState<string | null>(null);
  const typed = useRef<string | null>(null);
  const expected = useRef(value);
  const lastValid = useRef(value);
  const number = draft === null ? value : parsed(draft);
  const outside = Number.isFinite(number) && (number < min || number > max);
  const bound = (next: number) => Math.max(min, Math.min(max, next));

  useEffect(() => {
    if (value !== expected.current) {
      typed.current = null;
      setDraft(null);
    }
    expected.current = value;
    lastValid.current = value;
  }, [value]);

  function publish(next: number) {
    if (next === lastValid.current) return;
    lastValid.current = next;
    expected.current = next;
    onChange(next);
  }

  function finish() {
    if (typed.current !== null) {
      const next = parsed(typed.current);
      if (Number.isFinite(next)) publish(bound(next));
    }
    typed.current = null;
    setDraft(null);
    onEndChange();
  }

  return <label>{label}
    <input type="text" inputMode="decimal" value={draft ?? format(value)} disabled={disabled} aria-label={label}
      aria-invalid={outside || undefined} aria-describedby={outside ? hintId : undefined}
      onFocus={() => { typed.current = format(value); setDraft(typed.current); }}
      onChange={(event) => {
        const text = event.target.value;
        typed.current = text;
        setDraft(text);
        const next = parsed(text);
        if (Number.isFinite(next) && next >= min && next <= max) publish(next);
      }}
      onBlur={finish}
      onKeyDown={(event) => {
        if (event.nativeEvent.isComposing || event.altKey || event.ctrlKey || event.metaKey) return;
        if (event.key === "Enter") {
          event.preventDefault();
          finish();
          event.currentTarget.blur();
        } else if (event.key === "Escape") {
          event.preventDefault();
          typed.current = null;
          setDraft(null);
          onEndChange();
          event.currentTarget.blur();
        } else if (event.key === "ArrowUp" || event.key === "ArrowDown") {
          event.preventDefault();
          const number = typed.current === null ? value : parsed(typed.current);
          const next = bound(Number(((Number.isFinite(number) ? number : lastValid.current) +
            (event.key === "ArrowUp" ? 1 : -1) * (event.shiftKey ? 1 : .1)).toFixed(3)));
          typed.current = format(next);
          setDraft(typed.current);
          publish(next);
        }
      }} />
    {outside && <small id={hintId} className={styles.hint}>Use {format(min)} to {format(max)} seconds.</small>}
  </label>;
}
