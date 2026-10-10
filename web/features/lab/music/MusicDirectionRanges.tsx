"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent } from "react";
import { mediaUrl, seconds } from "@/lib/lab";
import { isPlaybackSpace } from "@/lib/playbackShortcut";
import type { EditorDirectionRange, LabDocument } from "@/types/lab";
import { useAudioWaveform } from "./useAudioWaveform";
import { waveformPath } from "./audioWaveform";
import { availableDirectionRange, changeEditorDirection, directionRangeBounds, effectiveEditorDirection, fitDirectionRange, MAX_DIRECTION_RANGES, MIN_DIRECTION_RANGE, updateDirectionRange } from "@/features/lab/kit/editorDirection";
import EditorIcon from "@/features/lab/kit/EditorIcon";
import DirectionTimeInput from "./DirectionTimeInput";
import styles from "./musicDirection.module.css";

interface Props {
  document: LabDocument;
  disabled: boolean;
  active: boolean;
  playhead: number;
  onSeek: (time: number) => void;
  onChange: (update: (document: LabDocument) => LabDocument, group?: string) => void;
  onEndChange: () => void;
  waveform?: { peaks: number[]; status: string };
}

interface Gesture {
  kind: "create" | "move" | "start" | "end";
  pointer: number;
  target: HTMLElement;
  left: number;
  width: number;
  origin: number;
  value: number;
  range?: EditorDirectionRange;
  bounds: { start: number; end: number };
}

export default function MusicDirectionRanges({ document, disabled, active, playhead, onSeek, onChange, onEndChange, waveform }: Props) {
  const track = document.track;
  const { start, end } = document.passage;
  const span = Math.max(.1, end - start);
  const ranges = effectiveEditorDirection(document).ranges;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = ranges.find((range) => range.id === selectedId) ?? null;
  const [draft, setDraft] = useState<{ start: number; end: number } | null>(null);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState("");
  const [width, setWidth] = useState(800);
  const root = useRef<HTMLDivElement>(null);
  const wave = useRef<HTMLDivElement>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const rangeInput = useRef<HTMLTextAreaElement>(null);
  const focusCreatedRange = useRef<string | null>(null);
  const gesture = useRef<Gesture | null>(null);
  const decoded = useAudioWaveform(waveform ? null : track);
  const { peaks, status } = waveform ?? decoded;
  const latest = useRef({ active, playhead, onSeek, start, end, onEndChange });
  latest.current = { active, playhead, onSeek, start, end, onEndChange };
  const path = useMemo(() => waveformPath(peaks, track?.duration ?? 0, start, end, width), [peaks, track?.duration, start, end, width]);
  const percent = (time: number) => (time - start) / span * 100;
  const relative = (time: number) => `${time < start ? "−" : ""}${seconds(Math.abs(time - start))}`;
  const available = ranges.length < MAX_DIRECTION_RANGES ? availableDirectionRange(ranges, document.passage, playhead) : null;
  const selectedBounds = selected ? directionRangeBounds(ranges, selected.id, track?.duration ?? end) : null;

  function endGesture() {
    const current = gesture.current;
    if (current?.target.hasPointerCapture(current.pointer)) current.target.releasePointerCapture(current.pointer);
    gesture.current = null;
    setDraft(null);
    latest.current.onEndChange();
  }

  function cancelGesture() {
    const current = gesture.current;
    if (current?.range) onChange((document) => updateDirectionRange(document, current.range!.id,
      { start: current.range!.start, end: current.range!.end }), `direction-range:${current.range.id}`);
    endGesture();
  }
  const cancelCurrentGesture = useRef(cancelGesture);
  cancelCurrentGesture.current = cancelGesture;

  useEffect(() => {
    const node = wave.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry.contentRect.width > 0) setWidth(entry.contentRect.width);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!selected || selected.id !== focusCreatedRange.current) return;
    rangeInput.current?.focus();
    focusCreatedRange.current = null;
  }, [selected?.id]);

  useEffect(() => {
    if (!active) audio.current?.pause();
    if (!active || disabled) endGesture();
  }, [active, disabled]);

  useEffect(() => {
    const player = audio.current;
    if (player) { player.pause(); player.currentTime = start; }
    endGesture();
    return () => player?.pause();
  }, [track?.id, start, end]);

  async function togglePlayback() {
    const player = audio.current;
    if (!player || !latest.current.active) return;
    if (!player.paused) { player.pause(); return; }
    const time = latest.current.playhead;
    player.currentTime = time >= start && time < end - .02 ? time : start;
    setError("");
    try { await player.play(); }
    catch { setError("The song could not play. Try again or choose another song."); }
  }
  const shortcut = useRef(togglePlayback);
  shortcut.current = togglePlayback;

  useEffect(() => {
    const handle = (event: KeyboardEvent) => {
      if (!latest.current.active || window.document.querySelector("dialog[open]")) return;
      if (gesture.current && !event.isComposing) {
        if (event.key === "Escape") { event.preventDefault(); cancelCurrentGesture.current(); return; }
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") endGesture();
      }
      if (!isPlaybackSpace(event)) return;
      event.preventDefault();
      if (!event.repeat) void shortcut.current();
    };
    window.addEventListener("keydown", handle);
    return () => window.removeEventListener("keydown", handle);
  }, []);

  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      const player = audio.current;
      if (!player) return;
      if (!latest.current.active || window.document.hidden || window.document.querySelector("dialog[open]")) { player.pause(); return; }
      const at = Math.min(latest.current.end, player.currentTime);
      latest.current.onSeek(at);
      if (at >= latest.current.end) player.pause();
    }, 50);
    return () => window.clearInterval(timer);
  }, [playing]);

  function seek(time: number) {
    const value = Math.max(start, Math.min(end, time));
    if (audio.current) audio.current.currentTime = value;
    onSeek(value);
  }

  function insert(range: { start: number; end: number }) {
    if (disabled || !active || !track) return;
    const id = crypto.randomUUID();
    onEndChange();
    onChange((current) => {
      const ranges = effectiveEditorDirection(current).ranges;
      if (ranges.length >= MAX_DIRECTION_RANGES || ranges.some((item) => item.start < range.end && item.end > range.start)) return current;
      return changeEditorDirection(current, { ranges: [...ranges, { ...range, id, instruction: "" }] });
    });
    focusCreatedRange.current = id;
    setSelectedId(id);
    seek(range.start);
  }

  function begin(event: PointerEvent<HTMLElement>, kind: Gesture["kind"], range?: EditorDirectionRange) {
    if (event.button !== 0 || !active || !track) return;
    event.stopPropagation();
    const rect = wave.current?.getBoundingClientRect();
    if (!rect?.width) return;
    const at = Math.max(start, Math.min(end, start + (event.clientX - rect.left) / rect.width * span));
    if (kind === "create") {
      seek(at);
      setSelectedId(null);
    } else setSelectedId(range!.id);
    if (disabled || (kind === "create" && ranges.length >= MAX_DIRECTION_RANGES)) return;
    onEndChange();
    const neighbors = range ? directionRangeBounds(ranges, range.id, track.duration) : {
      start: Math.max(start, ...ranges.filter((item) => item.end <= at).map((item) => item.end)),
      end: Math.min(end, ...ranges.filter((item) => item.start >= at).map((item) => item.start)),
    };
    if (kind === "create" && ranges.some((item) => item.start < at && item.end > at)) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = { kind, pointer: event.pointerId, target: event.currentTarget, left: rect.left, width: rect.width,
      origin: at, value: at, range, bounds: { start: Math.max(start, neighbors.start), end: Math.min(end, neighbors.end) } };
    // A partially visible range retains its source-track extent when dragged.
    if (range) gesture.current.bounds = neighbors;
  }

  function move(event: PointerEvent<HTMLElement>) {
    const current = gesture.current;
    if (!current || event.pointerId !== current.pointer || disabled || !active) return;
    const time = start + (event.clientX - current.left) / current.width * span;
    current.value = Math.max(current.bounds.start, Math.min(current.bounds.end, time));
    if (current.kind === "create") {
      setDraft({ start: Math.min(current.origin, current.value), end: Math.max(current.origin, current.value) });
    } else {
      onChange((document) => {
        const ranges = effectiveEditorDirection(document).ranges;
        if (!ranges.some((range) => range.id === current.range!.id)) return document;
        const bounds = directionRangeBounds(ranges, current.range!.id, document.track?.duration ?? end);
        const next = fitDirectionRange(current.range!, current.kind as "move" | "start" | "end", time - current.origin, bounds);
        return updateDirectionRange(document, current.range!.id, next);
      }, `direction-range:${current.range!.id}`);
    }
  }

  function finish(event: PointerEvent<HTMLElement>, cancelled = false) {
    const current = gesture.current;
    if (!current || event.pointerId !== current.pointer) return;
    if (cancelled) { cancelGesture(); return; }
    // Pointer-up can carry a final position that was not delivered as a move.
    move(event);
    if (!cancelled && current.kind === "create") {
      const from = Math.min(current.origin, current.value), to = Math.max(current.origin, current.value);
      if (to - from >= MIN_DIRECTION_RANGE && Math.abs(current.value - current.origin) / span * current.width > 4)
        insert({ start: Math.max(current.bounds.start, Math.round(from * 1000) / 1000),
          end: Math.min(current.bounds.end, Math.round(to * 1000) / 1000) });
    }
    endGesture();
  }

  function nudge(range: EditorDirectionRange, kind: "move" | "start" | "end", delta: number) {
    if (disabled || !active || !track) return;
    onChange((document) => {
      const ranges = effectiveEditorDirection(document).ranges;
      const current = ranges.find((item) => item.id === range.id);
      if (!current) return document;
      const next = fitDirectionRange(current, kind, delta, directionRangeBounds(ranges, current.id, document.track?.duration ?? end));
      return updateDirectionRange(document, current.id, next);
    }, `direction-range:${range.id}`);
  }

  function rangeKey(event: ReactKeyboardEvent<HTMLElement>, range: EditorDirectionRange, edge: "move" | "start" | "end") {
    if (event.defaultPrevented || event.nativeEvent.isComposing || event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      nudge(range, edge, (event.key === "ArrowLeft" ? -1 : 1) * (event.shiftKey ? 1 : .1));
    }
  }

  return <div className={styles.ranges} ref={root}>
    {track && <audio ref={audio} src={mediaUrl(`/lab/tracks/${track.id}/audio`)} preload="metadata"
      onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)}
      onError={() => setError("The song could not be loaded for playback.")} />}
    <div className={styles.rangeHeading}>
      <div><h3>Directions for parts of the song</h3><p>Drag across the waveform to give part of the song its own direction.</p></div>
      <button type="button" disabled={disabled || !track || !available} onClick={() => available && insert(available)}>
        <EditorIcon name="plus" size={13} /> Add direction
      </button>
    </div>
    <div className={styles.transport}>
      <button type="button" disabled={!track || !active} onClick={() => void togglePlayback()} aria-label={playing ? "Pause song" : "Play song"} title="Play / pause (Space)">
        <EditorIcon name={playing ? "pause" : "play"} size={15} />
      </button>
      <span>{relative(Math.max(start, Math.min(end, playhead)))} / {seconds(span)}</span>
      <small>Times relative to your selected song section</small>
    </div>
    <div className={styles.directionWave} ref={wave} data-playback-space tabIndex={0}
      aria-label="Song waveform. Click to seek or drag empty space to create a direction range."
      onPointerDown={(event) => begin(event, "create")} onPointerMove={move}
      onPointerUp={(event) => finish(event)} onPointerCancel={(event) => finish(event, true)}>
      <svg viewBox={`0 0 ${width} 72`} preserveAspectRatio="none" aria-hidden="true"><path d={path} /></svg>
      {ranges.filter((range) => range.start < end && range.end > start).map((range, index) => {
        const left = Math.max(start, range.start), right = Math.min(end, range.end);
        return <div className={`${styles.rangeBlock} ${selectedId === range.id ? styles.selectedRange : ""}`} key={range.id}
          style={{ left: `${percent(left)}%`, width: `${(right - left) / span * 100}%` }}>
          <button type="button" data-playback-space className={styles.rangeBody} aria-pressed={selectedId === range.id}
            title={range.instruction || "Write direction for this range"} aria-label={`Select direction ${index + 1}: ${relative(range.start)} to ${relative(range.end)}`}
            onClick={() => setSelectedId(range.id)} onFocus={() => setSelectedId(range.id)} onPointerDown={(event) => begin(event, "move", range)}
            onKeyDown={(event) => rangeKey(event, range, "move")} onKeyUp={onEndChange} onBlur={onEndChange}><span>{range.instruction || `Direction ${index + 1}`}</span></button>
          {(["start", "end"] as const).map((edge) => <button key={edge} type="button" className={`${styles.rangeEdge} ${styles[edge]}`}
            aria-label={`Adjust direction ${index + 1} ${edge}`} disabled={disabled} data-playback-space
            onFocus={() => setSelectedId(range.id)} onPointerDown={(event) => begin(event, edge, range)}
            onKeyDown={(event) => rangeKey(event, range, edge)} onKeyUp={onEndChange} onBlur={onEndChange} />)}
        </div>;
      })}
      {draft && <i className={styles.draftRange} style={{ left: `${percent(draft.start)}%`, width: `${(draft.end - draft.start) / span * 100}%` }} />}
      {playhead >= start && playhead <= end && <i className={styles.playhead} style={{ left: `${percent(playhead)}%` }} />}
    </div>
    <div className={styles.ruler} aria-hidden="true">{[0, .25, .5, .75, 1].map((fraction) => <span key={fraction}>{seconds(span * fraction)}</span>)}</div>
    {status && <p className={styles.hint}>{status}</p>}
    {error && <p className={styles.error} role="alert">{error}</p>}
    {ranges.length > 0 && <div className={styles.rangeList} aria-label="Saved passage directions">
      {[...ranges].sort((a, b) => a.start - b.start).map((range) => {
        const outside = range.end <= start || range.start >= end;
        const extendsOutside = !outside && (range.start < start || range.end > end);
        return <button key={range.id} type="button" aria-pressed={selectedId === range.id}
          onClick={() => { onEndChange(); setSelectedId(range.id); if (!outside) seek(Math.max(start, range.start)); }}>
          <span>{outside ? `${seconds(range.start)} – ${seconds(range.end)} · song time` : `${relative(Math.max(start, range.start))} – ${relative(Math.min(end, range.end))}`}</span>
          <strong>{range.instruction || "Add direction…"}</strong>{(outside || extendsOutside) && <small>{outside ? "Outside selected section" : "Continues outside section"}</small>}
        </button>;
      })}
    </div>}
    {selected && <div className={styles.rangeEditor}>
      <div><label htmlFor={`range-${selected.id}`}>Direction for this range</label>
        <button type="button" disabled={disabled} aria-label="Delete selected direction range" onClick={() => {
          onEndChange(); onChange((current) => changeEditorDirection(current, { ranges: effectiveEditorDirection(current).ranges.filter((range) => range.id !== selected.id) })); setSelectedId(null);
        }}><EditorIcon name="trash" size={13} /> Remove</button></div>
      <textarea ref={rangeInput} id={`range-${selected.id}`} rows={2} value={selected.instruction} maxLength={2000} disabled={disabled}
        placeholder="Use bright, fragmented city imagery here, then hold one quiet image into the chorus."
        onChange={(event) => { const instruction = event.target.value; onChange((current) => updateDirectionRange(current, selected.id, { instruction }), `direction-text:${selected.id}`); }} onBlur={onEndChange} />
      <details className={styles.precision}><summary>Adjust exact range</summary><div>
        {(["start", "end"] as const).map((edge) => <DirectionTimeInput key={`${selected.id}-${edge}`}
          label={`${edge === "start" ? "In" : "Out"} · seconds from section start`}
          min={(edge === "start" ? selectedBounds!.start : selected.start + MIN_DIRECTION_RANGE) - start}
          max={(edge === "start" ? selected.end - MIN_DIRECTION_RANGE : selectedBounds!.end) - start}
          value={selected[edge] - start} disabled={disabled}
          onChange={(value) => onChange((current) => updateDirectionRange(current, selected.id, { [edge]: value + start }), `direction-range:${selected.id}`)}
          onEndChange={onEndChange} />)}
      </div></details>
    </div>}
    {ranges.length >= MAX_DIRECTION_RANGES && <p className={styles.hint}>32 directions added. Remove one to add another.</p>}
  </div>;
}
