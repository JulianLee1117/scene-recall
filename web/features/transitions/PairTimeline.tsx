"use client";

import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent } from "react";
import { recipeTitle, type SourceSelection, type TransitionRecipe, type TransitionRetime } from "./transitions";
import { clamp, pairLayout, sequenceAtSourceTime, sourceAtClipTime, sourceAtSequenceTime, timelineDuration, timelineTrim, timelineTrimLimits,
  type PairLayout, type PairPosition, type PairSide, type TrimEdge } from "./pair-timeline";
import styles from "./pair-timeline.module.css";

export interface PairTimelineProps {
  a: SourceSelection | null;
  b: SourceSelection | null;
  recipe: TransitionRecipe | null;
  retime: TransitionRetime;
  selectedSide: PairSide | null;
  position: PairPosition | null;
  playbackTime?: number | null;
  filmDurations?: { a?: number; b?: number };
  disabled: boolean;
  onSelect: (side: PairSide) => void;
  onTrim: (side: PairSide, edge: TrimEdge, time: number) => void;
  onSeek: (side: PairSide, sourceTime: number) => void;
  onChoose: (side: PairSide) => void;
  onDuration: (seconds: number) => void;
  onSelectTransition?: () => void;
  onPreviewSeek?: (sequenceSeconds: number) => void;
}
type Drag = { pointer: number; x: number; width: number; layout: PairLayout; aFilm?: string; bFilm?: string }
  & ({ kind: "trim"; side: PairSide; edge: TrimEdge; source: SourceSelection } | { kind: "duration"; duration: number });
const timeLabel = (value: number) => `${value.toFixed(2)}s`;

export default function PairTimeline({ a, b, recipe, retime, selectedSide, position, playbackTime = null, filmDurations, disabled,
  onSelect, onTrim, onSeek, onChoose, onDuration, onSelectTransition, onPreviewSeek }: PairTimelineProps) {
  const layout = useMemo(() => pairLayout(a, b, recipe, retime), [a, b, recipe, retime]);
  const rail = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const [frozen, setFrozen] = useState<PairLayout | null>(null);
  const display = layout;
  const span = Math.max(frozen?.duration ?? layout.duration, 1 / 30);
  const percent = (time: number) => clamp(time / span * 100, 0, 100);
  const current = selectedSide ? position?.side === selectedSide ? sequenceAtSourceTime(display, position) : null
    : Number.isFinite(playbackTime) && playbackTime !== null ? clamp(playbackTime, 0, display.duration) : null;
  const hasTiming = !!layout.a || !!layout.b;
  const canDuration = !!recipe && recipe.id !== "hard-cut" && layout.maxOverlapFrames >= 3;
  const transitionName = recipe ? recipeTitle(recipe.id) : "Choose transition";

  function endDrag(event?: { pointerId: number }) {
    if (event && drag.current?.pointer !== event.pointerId) return;
    drag.current = null; setFrozen(null);
  }
  useEffect(() => { endDrag(); }, [disabled, a?.film_id, b?.film_id, recipe?.id, retime.mode, retime.speed, retime.span, retime.curve]);

  function inspect(side: PairSide, sequenceTime: number) {
    if (disabled) return;
    const target = sourceAtClipTime(layout, side, sequenceTime);
    if (target) { onSelect(side); onSeek(side, target.sourceTime); }
  }
  function scrub(time: number) {
    if (disabled || !Number.isFinite(time)) return;
    const targetTime = clamp(time, 0, layout.duration);
    if (onPreviewSeek) onPreviewSeek(targetTime);
    else { const target = sourceAtSequenceTime(layout, targetTime, selectedSide); if (target) { onSelect(target.side); onSeek(target.side, target.sourceTime); } }
  }
  function clipClick(event: MouseEvent<HTMLButtonElement>, side: PairSide) {
    const clip = layout[side], rect = rail.current?.getBoundingClientRect();
    if (!clip || !rect || rect.width <= 0) return;
    const time = event.detail === 0 ? clip.start + clip.duration / 2 : clamp((event.clientX - rect.left) / rect.width, 0, 1) * layout.duration;
    inspect(side, time);
  }
  function applyTrim(side: PairSide, edge: TrimEdge, requested: number, original = side === "a" ? a : b) {
    const currentSource = side === "a" ? a : b;
    if (disabled || !original || !currentSource || currentSource.film_id !== original.film_id) return;
    const value = timelineTrim(original, edge, requested, recipe, retime, filmDurations?.[side]);
    if (value === null) return;
    onSelect(side); onTrim(side, edge, value);
    onSeek(side, edge === "source_start" ? value : Math.max(currentSource.source_start, value - .1));
  }
  function startTrim(event: PointerEvent<HTMLButtonElement>, side: PairSide, edge: TrimEdge) {
    const source = side === "a" ? a : b, rect = rail.current?.getBoundingClientRect();
    if (disabled || event.button !== 0 || !source || !rect || rect.width <= 0 || !timelineTrimLimits(source, edge, recipe, retime, filmDurations?.[side])) return;
    event.preventDefault(); event.stopPropagation(); event.currentTarget.focus(); onSelect(side);
    onSeek(side, edge === "source_start" ? source.source_start : Math.max(source.source_start, source.source_end - .1));
    drag.current = { kind: "trim", side, edge, source, pointer: event.pointerId, x: event.clientX, width: rect.width, layout, aFilm: a?.film_id, bFilm: b?.film_id };
    setFrozen(layout); event.currentTarget.setPointerCapture(event.pointerId);
  }
  function startDuration(event: PointerEvent<HTMLButtonElement>) {
    const rect = rail.current?.getBoundingClientRect();
    if (disabled || !canDuration || event.button !== 0 || !rect || rect.width <= 0) return;
    event.preventDefault(); event.stopPropagation(); event.currentTarget.focus(); onSelectTransition?.();
    drag.current = { kind: "duration", duration: layout.overlap, pointer: event.pointerId, x: event.clientX, width: rect.width, layout, aFilm: a?.film_id, bFilm: b?.film_id };
    setFrozen(layout); event.currentTarget.setPointerCapture(event.pointerId);
  }
  function move(event: PointerEvent<HTMLButtonElement>) {
    const moving = drag.current;
    if (disabled || !moving || moving.pointer !== event.pointerId || moving.aFilm !== a?.film_id || moving.bFilm !== b?.film_id) return;
    const delta = (event.clientX - moving.x) / moving.width * moving.layout.duration;
    if (moving.kind === "trim") {
      const currentSource = moving.side === "a" ? a : b, other = moving.edge === "source_start" ? "source_end" : "source_start";
      if (currentSource?.[other] !== moving.source[other]) { endDrag(); return; }
      applyTrim(moving.side, moving.edge, moving.source[moving.edge] + delta, moving.source);
    } else { const duration = timelineDuration(moving.layout, moving.duration - delta); if (duration !== null) onDuration(duration); }
  }
  function trimKey(event: KeyboardEvent<HTMLButtonElement>, side: PairSide, edge: TrimEdge) {
    const source = side === "a" ? a : b;
    if (disabled || !source) return;
    const limits = timelineTrimLimits(source, edge, recipe, retime, filmDurations?.[side]);
    const delta = event.key === "ArrowLeft" || event.key === "ArrowDown" ? -1 : event.key === "ArrowRight" || event.key === "ArrowUp" ? 1 : 0;
    if (!limits || (!delta && event.key !== "Home" && event.key !== "End")) return;
    event.preventDefault(); applyTrim(side, edge, event.key === "Home" ? limits.min : event.key === "End" ? limits.max : source[edge] + delta * (event.shiftKey ? .1 : 1 / 30));
  }
  function durationKey(event: KeyboardEvent<HTMLButtonElement>) {
    if (disabled || !canDuration) return;
    const delta = event.key === "ArrowLeft" || event.key === "ArrowDown" ? -1 : event.key === "ArrowRight" || event.key === "ArrowUp" ? 1 : 0;
    if (!delta && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault(); const duration = timelineDuration(layout, event.key === "Home" ? .1 : event.key === "End" ? 2 : layout.overlap + delta * (event.shiftKey ? 3 : 1) / 30);
    if (duration !== null) { onSelectTransition?.(); onDuration(duration); }
  }

  return <section className={styles.timeline} aria-label="Transition clip timeline">
    <div className={styles.heading}><strong>A → B</strong><span>{hasTiming ? `${timeLabel(layout.duration)}${layout.approximate && selectedSide ? " · source timing ≈" : ""}` : "Choose two clips"}</span>
      <div>{(["a", "b"] as const).map((side) => <button type="button" key={side} disabled={disabled} onClick={() => onChoose(side)} aria-label={`${side === "a" ? a ? "Change" : "Choose" : b ? "Change" : "Choose"} clip ${side.toUpperCase()}`}>{(side === "a" ? a : b) ? "Change" : "Choose"} {side.toUpperCase()}</button>)}</div>
    </div>
    {hasTiming ? <div className={styles.canvas} ref={rail}>
      <div className={styles.ruler}><span>0s</span><span>{timeLabel(span / 2)}</span><span>{timeLabel(span)}</span>
        <input type="range" min="0" max={span} step={1 / 30} value={current ?? 0} disabled={disabled}
          aria-label={onPreviewSeek ? "Preview timeline position" : "Source inspection timeline position"} aria-valuetext={timeLabel(current ?? 0)} onChange={(event) => scrub(event.target.valueAsNumber)} />
      </div>
      <div className={styles.track}>
        {(["a", "b"] as const).map((side) => { const clip = display[side], live = layout[side]; if (!clip || !live) return null;
          const start = side === "a" ? 0 : display.overlapEnd, end = side === "a" ? display.overlapStart : display.duration;
          const bodyStart = display.a && display.b ? start : clip.start, bodyEnd = display.a && display.b ? end : clip.start + clip.duration;
          return <div className={`${styles.clip} ${side === "a" ? styles.clipA : styles.clipB} ${selectedSide === side ? styles.selected : ""}`} key={side} style={{ left: `${percent(clip.start)}%`, width: `${percent(clip.duration)}%` }}>
            <button type="button" className={styles.clipBody} disabled={disabled} aria-label={`Inspect clip ${side.toUpperCase()}: ${clip.source.title}`} aria-pressed={selectedSide === side}
              style={{ left: `${Math.max(0, (bodyStart - clip.start) / clip.duration * 100)}%`, width: `${Math.max(0, (bodyEnd - bodyStart) / clip.duration * 100)}%` }} onClick={(event) => clipClick(event, side)}>
              <strong>{side.toUpperCase()}</strong><span>{clip.source.title}<small>{timeLabel(live.duration)}</small></span>
            </button>
          </div>;
        })}
        {layout.a && layout.b && <button type="button" className={`${styles.transition} ${layout.overlap ? "" : styles.hardCut}`} disabled={disabled || !onSelectTransition}
          style={{ left: `${percent((display.overlapStart + display.overlapEnd) / 2)}%`, width: `${percent(display.overlap)}%` }} aria-label={`${transitionName}, ${Math.round(layout.overlap * 30)} frames. Inspect transition`} title={transitionName} onClick={onSelectTransition}>
          <span>{layout.overlap ? `${Math.round(layout.overlap * 30)}f` : "Cut"}</span>
        </button>}
        {(["a", "b"] as const).flatMap((side) => { const clip = display[side], source = side === "a" ? a : b; if (!clip || !source) return [];
          return (["source_start", "source_end"] as const).map((edge) => { const limits = timelineTrimLimits(source, edge, recipe, retime, filmDurations?.[side]);
            const inner = side === "a" ? edge === "source_end" : edge === "source_start";
            const point = clip.start + (edge === "source_end" ? clip.duration : 0);
            return <button type="button" role="slider" key={`${side}-${edge}`} className={`${styles.trimHandle} ${inner ? side === "a" ? styles.aOut : styles.bIn : styles.outerHandle}`}
              style={{ left: `${percent(point)}%` }} disabled={disabled || !limits} aria-label={`Clip ${side.toUpperCase()} ${edge === "source_start" ? "in" : "out"} trim`}
              aria-valuemin={limits?.min} aria-valuemax={limits?.max} aria-valuenow={source[edge]} aria-valuetext={`${timeLabel(source[edge])} source time`}
              title={`Trim ${side.toUpperCase()} ${edge === "source_start" ? "in" : "out"}. Arrows: 1/30s; Shift: 0.1s.`}
              onPointerDown={(event) => startTrim(event, side, edge)} onPointerMove={move} onPointerUp={endDrag} onPointerCancel={endDrag} onLostPointerCapture={endDrag} onKeyDown={(event) => trimKey(event, side, edge)} />;
          }); })}
      </div>
      {canDuration && <button type="button" role="slider" className={styles.durationHandle} style={{ left: `${percent(display.overlapStart)}%` }} disabled={disabled}
        aria-label="Transition overlap duration" aria-valuemin={3} aria-valuemax={layout.maxOverlapFrames} aria-valuenow={Math.round(layout.overlap * 30)} aria-valuetext={`${Math.round(layout.overlap * 30)} output frames`}
        title="Drag left to lengthen the overlap, right to shorten. Arrow keys adjust one output frame."
        onPointerDown={startDuration} onPointerMove={move} onPointerUp={endDrag} onPointerCancel={endDrag} onLostPointerCapture={endDrag} onKeyDown={durationKey}>↔</button>}
      {current !== null && <div className={styles.playhead} style={{ left: `${percent(current)}%` }} aria-hidden="true" />}
    </div> : <div className={styles.empty}>{(["a", "b"] as const).map((side) => <button type="button" key={side} disabled={disabled} onClick={() => onChoose(side)}><strong>{side.toUpperCase()}</strong><span>{side === "a" ? "Choose the first clip" : "Choose the next clip"}</span></button>)}</div>}
    {hasTiming && <div className={styles.hint}><span>Click a clip to inspect · drag its edges to trim</span>{layout.overlap > 0 && <span>↔ overlap</span>}</div>}
  </section>;
}
