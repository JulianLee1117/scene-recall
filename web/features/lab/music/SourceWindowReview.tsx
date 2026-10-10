"use client";

import { useEffect, useId, useRef, useState } from "react";
import type { Crop, LabClip } from "@/types/lab";
import SourceSceneEditor from "./SourceSceneEditor";
import styles from "./sourceWindowReview.module.css";

type Props = {
  clip: LabClip;
  onClose: () => void;
  onApply?: (clip: LabClip) => void;
  disabled?: boolean;
  /** A verified film duration permits browsing outside the supplied window. */
  filmDuration?: number;
  outputRatio?: number;
};
type Draft = { source_start: number; crop: Crop | null };
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));
const ignoreTime = () => {};
const sameCrop = (left: Crop | null | undefined, right: Crop | null) =>
  !left && !right || !!left && !!right && (["x", "y", "width", "height"] as const).every((key) => Math.abs(left[key] - right[key]) < 1e-6);

export default function SourceWindowReview(props: Props) {
  const { clip } = props;
  return <Review key={[clip.id, clip.film_id, clip.source_start, clip.source_end, JSON.stringify(clip.crop)].join(":")} {...props} />;
}

function Review({ clip, onClose, onApply, disabled = false, filmDuration, outputRatio = 16 / 9 }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [value, setValue] = useState<Draft>({ source_start: clip.source_start, crop: clip.crop ?? null });
  const [center, setCenter] = useState(clip.source_start);
  const [ready, setReady] = useState(false);
  const length = clip.source_end - clip.source_start;
  const canEdit = !!onApply && !clip.locked;
  const knownDuration = typeof filmDuration === "number" && Number.isFinite(filmDuration) && filmDuration > 0;
  const availableEnd = knownDuration ? filmDuration : clip.source_end;
  const maxIn = Math.max(0, availableEnd - length);
  const neighborhood = Math.max(5, length * 2);
  const boundedCenter = clamp(center, 0, maxIn);
  // Keep the visible interval near this scene, even for a two-hour source.
  const range = knownDuration && canEdit ? {
    start: Math.max(0, Math.min(boundedCenter - neighborhood, value.source_start)),
    end: Math.min(availableEnd, Math.max(boundedCenter + neighborhood + length, value.source_start + length)),
  } : { start: clip.source_start, end: clip.source_end };
  const invalid = !Number.isFinite(value.source_start) || !Number.isFinite(length) || length <= 0 || value.source_start < 0 || value.source_start + length > availableEnd + 1e-6;

  useEffect(() => {
    const node = dialog.current;
    node?.showModal();
    node?.querySelector<HTMLElement>("[data-playback-space]")?.focus({ preventScroll: true });
    return () => node?.close();
  }, []);

  function change(next: Draft) {
    if (disabled || !canEdit) return;
    if (Math.abs(next.source_start - value.source_start) > 1e-6) setReady(false);
    setValue(next);
  }
  function moveTo(start: number) {
    const source_start = clamp(start, 0, maxIn);
    change({ ...value, source_start });
    setCenter(source_start);
  }
  function apply() {
    if (!onApply || !canEdit || disabled || invalid || !ready) return;
    const changed = Math.abs(value.source_start - clip.source_start) > 1e-6 || !sameCrop(clip.crop, value.crop);
    onApply(changed ? { ...clip, source_start: value.source_start, source_end: value.source_start + length,
      crop: value.crop, reference_time: null, window_start: null, window_end: null } : clip);
  }

  return (
    <dialog ref={dialog} className={styles.review} aria-labelledby={titleId}
      onCancel={(event) => { event.preventDefault(); onClose(); }}>
      <header className={styles.header}>
        <div><h2 id={titleId}>{canEdit ? "Adjust scene" : "Review footage"}</h2><p>{clip.title || "Selected scene"}</p></div>
        <button type="button" onClick={onClose} aria-label="Close footage review">✕</button>
      </header>
      <SourceSceneEditor filmId={clip.film_id} range={range} duration={length} value={value}
        outputRatio={outputRatio} disabled={disabled} suspended={disabled} readOnly={!canEdit}
        onChange={change} onTimeChange={ignoreTime} onReadyChange={setReady} />
      {knownDuration && canEdit && <details className={styles.more}>
        <summary>Browse farther</summary>
        <div className={styles.navigation}>
          <button type="button" disabled={disabled || value.source_start <= 0} onClick={() => moveTo(value.source_start - neighborhood)}>Earlier footage</button>
          <button type="button" disabled={disabled || value.source_start >= maxIn} onClick={() => moveTo(value.source_start + neighborhood)}>Later footage</button>
          <button type="button" disabled={disabled || Math.abs(value.source_start - clip.source_start) < 1e-6} onClick={() => moveTo(clip.source_start)}>Original moment</button>
          <label>Source start (seconds)<input type="number" min={0} max={maxIn} step={1 / 24}
            value={Number(value.source_start.toFixed(3))} disabled={disabled}
            onChange={(event) => { const time = Number(event.target.value); if (event.target.value && Number.isFinite(time)) moveTo(time); }} /></label>
        </div>
      </details>}
      {invalid && <p className={styles.error} role="alert">This moment extends beyond the available footage.</p>}
      <footer className={styles.footer}>
        <span>{canEdit ? "Changes stay local until you use this footage." : "Playback does not change the edit."}</span>
        <button type="button" onClick={onClose}>{canEdit ? "Cancel" : "Close"}</button>
        {canEdit && <button type="button" className={styles.apply} disabled={disabled || invalid || !ready} onClick={apply}>Use this footage</button>}
      </footer>
    </dialog>
  );
}

