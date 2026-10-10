"use client";

import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import { mediaUrl, seconds } from "@/lib/lab";
import type { LabDocument, MusicPlan, MusicSearchFacet } from "@/types/lab";
import { directionOf } from "./musicEdit";
import EditorIcon from "@/features/lab/kit/EditorIcon";
import EditorPopover from "@/features/lab/kit/EditorPopover";
import MusicalCues from "./MusicalCues";
import { musicCues } from "./musicCues";
import { waveformPath } from "./audioWaveform";
import { playbackScrollLeft } from "./timelineViewport";
import { boundedDialogueStart, dialogueEnd } from "./dialogueAudio";
import dialogueStyles from "./dialogue.module.css";
import cueStyles from "./musicalCues.module.css";
import styles from "./musicWorkspace.module.css";
import toolStyles from "./musicTimelineTools.module.css";

type Props = {
  document: LabDocument;
  plan: MusicPlan;
  filmTitles: Record<string, string>;
  peaks: number[];
  waveformStatus: string;
  playhead: number;
  playing?: boolean;
  selectedId: string | null;
  selectedDialogueId?: string | null;
  onSelectDialogue?: (id: string) => void;
  onRemoveDialogue?: (id: string) => void;
  onMoveDialogue?: (id: string, start: number) => void;
  disabled: boolean;
  snap: boolean;
  onSnap: (value: boolean) => void;
  onSelect: (id: string) => void;
  onSeek: (time: number) => void;
  /** The index identifies the slot immediately to the right of the cut. */
  onCut: (index: number, time: number) => void;
  onSplitAt: (time: number) => void;
  onRemoveCut: (index: number) => void;
  onRemoveScene?: (slotId: string) => void;
  onFillGaps?: () => void;
  canFillGaps?: boolean;
  canAddCut: boolean;
  onAddCut: () => void;
  onDetectBeats: () => void;
  canSetEnd: boolean;
  onSetEnd: () => void;
  canJoinNext: boolean;
  onJoinNext: () => void;
  canReplan: boolean;
  onReplan: () => void;
  draggedSceneDuration?: number | null;
  previewPair?: { anchorSlotId: string; nextSlotId: string; cut: number } | null;
};
const facetLabels: Record<MusicSearchFacet, string> = {
  all: "Scene search",
  scene: "Action / story",
  words: "Dialogue",
  look: "Visual",
  mood: "Mood",
};
const formatTime = (time: number) => seconds(time).replace(/\.00$/, "");

export default function MusicEditTimeline({
  document,
  plan,
  filmTitles,
  peaks,
  waveformStatus,
  playhead,
  playing = false,
  selectedId,
  selectedDialogueId,
  onSelectDialogue,
  onRemoveDialogue,
  onMoveDialogue,
  disabled,
  snap,
  onSnap,
  onSelect,
  onSeek,
  onCut,
  onSplitAt,
  onRemoveCut,
  onRemoveScene,
  onFillGaps,
  canFillGaps = false,
  canAddCut,
  onAddCut,
  onDetectBeats,
  canSetEnd,
  onSetEnd,
  canJoinNext,
  onJoinNext,
  canReplan,
  onReplan,
  draggedSceneDuration = null,
  previewPair = null,
}: Props) {
  const scroller = useRef<HTMLDivElement>(null);
  const surface = useRef<HTMLDivElement>(null);
  const region = useRef<HTMLElement>(null);
  const hintId = useId();
  const [zoom, setZoom] = useState(1);
  const [expanded, setExpanded] = useState(false);
  const [showBeats, setShowBeats] = useState(true);
  const [showCues, setShowCues] = useState(false);
  const [selectedCueId, setSelectedCueId] = useState<string | null>(null);
  const cues = useMemo(() => musicCues(document), [document]);
  const [viewportWidth, setViewportWidth] = useState(900);
  const [scrollLeft, setScrollLeft] = useState(0);
  const savedScrollLeft = useRef(0);
  const hiddenViewport = useRef(false);
  // Slot ids survive neighboring splits; numeric indices do not.
  const [selectedCutId, setSelectedCutId] = useState<string | null>(null);
  const [dragCut, setDragCut] = useState<{
    index: number;
    value: number;
  } | null>(null);
  const [dragDialogue, setDragDialogue] = useState<{ id: string; start: number } | null>(null);
  const suppressDialogueClick = useRef(false);
  const drag = useRef<{
    kind: "seek" | "cut" | "dialogue";
    index?: number;
    pointer: number;
    target: HTMLElement;
    left: number;
    width: number;
    originX: number;
    originValue: number;
    value: number;
    moved: boolean;
  } | null>(null);
  const { start, end } = document.passage;
  const duration = Math.max(0.001, end - start);
  const selectedCutIndex = plan.slots.findIndex(
    (slot) => slot.id === selectedCutId,
  );
  const activeCutIndex = selectedCutIndex > 0 ? selectedCutIndex : null;
  const clips = useMemo(
    () => new Map(document.clips.map((clip) => [clip.id, clip])),
    [document.clips],
  );
  const dialogueLayout = useMemo(() => {
    const ends: number[] = [], rows = new Map<string, number>();
    for (const voice of [...(document.dialogue_clips ?? [])].sort((a, b) => a.start - b.start)) {
      let row = ends.findIndex((end) => end <= voice.start + 1e-6);
      if (row < 0) row = ends.length;
      ends[row] = dialogueEnd(voice); rows.set(voice.id, row % 3);
    }
    const count = Math.min(3, ends.length);
    return { rows, count, height: count > 1 ? count * 30 + 12 : 48 };
  }, [document.dialogue_clips]);
  const missingSlots = plan.slots.filter((slot) => !clips.has(slot.clip_id ?? ""));
  const previewIndex = plan.slots.findIndex((slot) => slot.id === previewPair?.anchorSlotId);
  const previewAnchor = plan.slots[previewIndex];
  const previewNext = plan.slots[previewIndex + 1];
  const visiblePair = previewPair && previewAnchor && previewNext?.id === previewPair.nextSlotId &&
    Number.isFinite(previewPair.cut) && previewPair.cut > previewAnchor.start && previewPair.cut < previewNext.end
    ? { start: previewAnchor.start, end: previewNext.end, cut: previewPair.cut } : null;


  useEffect(() => {
    if (!scroller.current) return;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width;
      if (!width || width <= 0) {
        hiddenViewport.current = true;
        return;
      }
      setViewportWidth(width);
      restoreViewport();
    });
    observer.observe(scroller.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    const node = scroller.current;
    if (!node || node.clientWidth <= 0) return;
    node.scrollLeft =
      zoom === 1
        ? 0
        : ((playhead - start) / duration) * node.clientWidth * zoom -
          node.clientWidth * 0.4;
    rememberScroll(node.scrollLeft);
    // Deliberate zoom changes center the current editing position.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zoom]);
  useEffect(() => {
    const node = scroller.current;
    // A drag owns the viewport until it ends, even if audio is still playing.
    if (!node || disabled || hiddenViewport.current || node.clientWidth <= 0 || !playing || drag.current) return;
    const contentWidth = node.clientWidth * zoom;
    const next = playbackScrollLeft(((playhead - start) / duration) * contentWidth,
      node.scrollLeft, node.clientWidth, contentWidth);
    if (next !== node.scrollLeft) {
      node.scrollLeft = next;
      rememberScroll(next);
    }
  }, [disabled, playing, playhead, start, duration, zoom, viewportWidth]);

  function rememberScroll(value: number) {
    savedScrollLeft.current = value;
    setScrollLeft(value);
  }
  function restoreViewport() {
    const node = scroller.current;
    if (!node || node.clientWidth <= 0 || !hiddenViewport.current) return;
    node.scrollLeft = savedScrollLeft.current;
    hiddenViewport.current = false;
    rememberScroll(node.scrollLeft);
  }
  useEffect(() => {
    if (scroller.current?.clientWidth === 0) hiddenViewport.current = true;
    else restoreViewport();
  }, [disabled]);

  function releaseDrag() {
    const active = drag.current;
    if (active?.target.hasPointerCapture(active.pointer))
      active.target.releasePointerCapture(active.pointer);
    drag.current = null;
    setDragCut(null);
    setDragDialogue(null);
  }
  useEffect(() => {
    // Do not commit a gesture to stale slots after a job or external edit.
    releaseDrag();
  }, [disabled, plan, document.dialogue_clips]);

  const position = (time: number) => ((time - start) / duration) * 100;
  const { beats, downbeats, snapTimes } = useMemo(() => {
    const beatValues = (key: string) => {
      const values = document.rhythm?.[key];
      return Array.isArray(values)
        ? values.filter(
            (v): v is number =>
              typeof v === "number" && Number.isFinite(v) && v >= start && v <= end,
          )
        : [];
    };
    const beats = beatValues("beats"), downbeats = beatValues("downbeats");
    return {
      beats,
      downbeats,
      snapTimes: [...new Set([...beats, ...downbeats, ...beatValues("markers")])],
    };
  }, [document.rhythm, start, end]);
  const waveform = useMemo(() => {
    if (!document.track || !peaks.length) return "";
    const visibleStart = start + (scrollLeft / (viewportWidth * zoom)) * duration;
    return waveformPath(peaks, document.track.duration, visibleStart,
      visibleStart + duration / zoom, viewportWidth);
  }, [peaks, document.track, start, duration, scrollLeft, viewportWidth, zoom]);
  function snapped(time: number, width: number, enabled: boolean) {
    if (!enabled || !snap || !snapTimes.length) return time;
    const nearest = snapTimes.reduce((a, b) =>
      Math.abs(a - time) <= Math.abs(b - time) ? a : b,
    );
    return Math.abs(nearest - time) <= (duration / width) * 10 ? nearest : time;
  }
  function timeAt(x: number, left: number, width: number, snapping = false) {
    return snapped(
      Math.max(start, Math.min(end, start + ((x - left) / width) * duration)),
      width,
      snapping,
    );
  }
  function cutIsLocked(index: number) {
    const left = plan.slots[index - 1],
      right = plan.slots[index];
    return (
      !left ||
      !right ||
      !!clips.get(left.clip_id ?? "")?.locked ||
      !!clips.get(right.clip_id ?? "")?.locked
    );
  }
  function canMoveCut(index: number) {
    return (
      !disabled &&
      !cutIsLocked(index) &&
      plan.slots[index].end - plan.slots[index - 1].start > 0.5
    );
  }
  function boundedCut(index: number, value: number) {
    return Math.max(
      plan.slots[index - 1].start + 0.25,
      Math.min(plan.slots[index].end - 0.25, value),
    );
  }
  function selectSlot(id: string, time: number) {
    if (disabled) return;
    setSelectedCutId(null);
    onSelect(id);
    onSeek(time);
  }
  function begin(
    event: PointerEvent<HTMLElement>,
    kind: "seek" | "cut" | "dialogue",
    index?: number,
  ) {
    if (
      disabled || event.button !== 0 ||
      (kind === "cut" && cutIsLocked(index!))
    )
      return;
    const rect = surface.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return;
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.focus({ preventScroll: true });
    const value =
      kind === "cut"
        ? plan.slots[index!].start
        : kind === "dialogue" ? document.dialogue_clips![index!].start
        : timeAt(event.clientX, rect.left, rect.width);
    if (kind === "dialogue") {
      suppressDialogueClick.current = false;
      onSelectDialogue?.(document.dialogue_clips![index!].id);
    }
    drag.current = {
      kind,
      index,
      pointer: event.pointerId,
      target: event.currentTarget,
      left: rect.left,
      width: rect.width,
      originX: event.clientX,
      originValue: value,
      value,
      moved: false,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setSelectedCutId(kind === "cut" ? plan.slots[index!].id : null);
    onSeek(value);
  }
  function move(event: PointerEvent<HTMLElement>) {
    const active = drag.current;
    if (!active || active.pointer !== event.pointerId) return;
    if (disabled) { releaseDrag(); return; }
    if (active.kind === "cut" && !canMoveCut(active.index!)) return;
    const delta = event.clientX - active.originX;
    if (active.kind !== "seek" && !active.moved && Math.abs(delta) < 4) return;
    active.moved = true;
    if (active.kind === "cut") {
      const value = boundedCut(
        active.index!,
        snapped(
          active.originValue + (delta / active.width) * duration,
          active.width,
          !event.altKey,
        ),
      );
      active.value = value;
      setDragCut({ index: active.index!, value });
    } else if (active.kind === "dialogue") {
      const voice = document.dialogue_clips?.[active.index!];
      if (!voice) { releaseDrag(); return; }
      const value = boundedDialogueStart(document, voice.id,
        snapped(active.originValue + (delta / active.width) * duration, active.width, !event.altKey));
      active.value = value;
      setDragDialogue({ id: voice.id, start: value });
    } else {
      const value = timeAt(event.clientX, active.left, active.width);
      if (value === active.value) return;
      active.value = value;
      onSeek(value);
    }
  }
  function finish(event: PointerEvent<HTMLElement>, cancel = false) {
    const active = drag.current;
    if (!active || active.pointer !== event.pointerId) return;
    if (
      !cancel &&
      active.kind === "cut" &&
      active.moved &&
      canMoveCut(active.index!) &&
      Math.abs(active.value - active.originValue) > 0.001
    )
      onCut(active.index!, active.value);
    if (!cancel && !disabled && active.kind === "dialogue" && active.moved) {
      suppressDialogueClick.current = true;
      const voice = document.dialogue_clips?.[active.index!];
      if (voice && Math.abs(active.value - active.originValue) > .001) {
        onMoveDialogue?.(voice.id, active.value);
        onSeek(active.value);
      }
    }
    releaseDrag();
  }
  function nudgeCut(index: number, delta: number) {
    if (canMoveCut(index))
      onCut(index, boundedCut(index, plan.slots[index].start + delta));
  }
  function joinCut(index: number) {
    if (disabled || cutIsLocked(index)) return;
    onRemoveCut(index);
    setSelectedCutId(null);
    region.current?.focus({ preventScroll: true });
  }
  function keyboard(event: KeyboardEvent<HTMLElement>) {
    if (
      event.defaultPrevented ||
      disabled ||
      event.nativeEvent.isComposing ||
      event.altKey ||
      event.ctrlKey ||
      event.metaKey ||
      (event.target as Element).closest(
        "input, textarea, select, [popover], dialog, [role='dialog'], [contenteditable]:not([contenteditable='false']), [role='textbox'], [role='combobox']",
      )
    )
      return;
    if (event.key.toLowerCase() === "m") {
      event.preventDefault();
      if (!event.repeat && !disabled && canAddCut) onAddCut();
    } else if (
      event.key === "Delete" || event.key === "Backspace"
    ) {
      if (activeCutIndex !== null) {
        event.preventDefault();
        if (!event.repeat) joinCut(activeCutIndex);
      } else if (onRemoveScene && selectedId) {
        const slot = plan.slots.find((item) => item.id === selectedId);
        const clip = clips.get(slot?.clip_id ?? "");
        if (clip && !clip.locked) {
          event.preventDefault();
          if (!event.repeat) onRemoveScene(selectedId);
        }
      }
    } else if (event.key === "Escape") {
      releaseDrag();
      setSelectedCutId(null);
    }
  }
  const handlers = {
    onPointerMove: move,
    onPointerUp: (event: PointerEvent<HTMLElement>) => finish(event),
    onPointerCancel: (event: PointerEvent<HTMLElement>) => finish(event, true),
  };
  const tickStep =
    [0.25, 0.5, 1, 2, 5, 10, 15, 30, 60].find(
      (step) => step >= duration / Math.max(2, (viewportWidth * zoom) / 85),
    ) ?? 60;
  const ticks = Array.from(
    { length: Math.ceil(duration / tickStep) },
    (_, i) => i * tickStep,
  ).filter((value) => duration - value >= tickStep * 0.5);
  ticks.push(duration);
  const activeCutTime =
    activeCutIndex === null
      ? null
      : dragCut?.index === activeCutIndex
        ? dragCut.value
        : plan.slots[activeCutIndex].start;

  return (
    <section
      ref={region}
      className={`${styles.timeline} ${expanded ? styles.timelineExpanded : ""}`}
      aria-label="Music and clip timeline"
      aria-describedby={hintId}
      tabIndex={-1}
      onKeyDown={keyboard}
    >
      <div className={styles.timelineTools}>
        <div className={styles.timelineTitle}>
          <strong>Timeline</strong>
          <span>
            {plan.slots.length} {plan.slots.length === 1 ? "clip" : "clips"} ·{" "}
            {formatTime(duration)}
          </span>
          {missingSlots.length > 0 && <button className={styles.missingScenes}
            aria-label={`Review ${missingSlots.length} missing ${missingSlots.length === 1 ? "scene" : "scenes"}`}
            title="Jump to the next empty position to find a scene"
            onClick={() => {
              const selectedStart = plan.slots.find((slot) => slot.id === selectedId)?.start ?? -Infinity;
              const next = missingSlots.find((slot) => slot.start > selectedStart) ?? missingSlots[0];
              selectSlot(next.id, next.start);
            }}>
            {missingSlots.length} missing
          </button>}
        </div>
        <div className={styles.timelineActions}>
          {onFillGaps && <button
            className={toolStyles.toolButton}
            disabled={disabled || !canFillGaps}
            onClick={onFillGaps}
            title="Find scenes for empty positions while keeping placed scenes and cuts"
          >
            <EditorIcon name="sparkles" /> Fill gaps
          </button>}
          <button
            className={`${styles.addCut} ${toolStyles.toolButton}`}
            disabled={disabled || !canAddCut}
            onClick={onAddCut}
            title="Split the clip at the playhead (M)"
            aria-keyshortcuts="M"
          >
            <EditorIcon name="scissors" />
            Split
          </button>
          <button
            className={`${styles.snapToggle} ${toolStyles.toolButton}`}
            aria-pressed={snap}
            onClick={() => onSnap(!snap)}
            title="Snap dragged cuts to detected beats. Hold Alt while dragging to ignore snapping."
          >
            <EditorIcon name="magnet" />
            Snap
          </button>
          <EditorPopover title="Music guides" label={<><EditorIcon name="beats" /> Beats</>}
            triggerLabel="Beat guides" triggerClassName={toolStyles.toolButton} width={330}>
            {(close) => <div className={styles.timelineOptions}>
              <label><input type="checkbox" checked={showBeats} disabled={!beats.length && !downbeats.length}
                onChange={(event) => setShowBeats(event.target.checked)} /> Show beat guides{beats.length ? ` · ${beats.length}` : ""}</label>
              <label><input type="checkbox" checked={showCues} onChange={(event) => { setShowCues(event.target.checked); setSelectedCueId(null); }} /> Show musical moments & lyrics</label>
              <p className={styles.hint}>Beats are timing guides. Detecting them keeps your current cuts and clips.</p>
              <button disabled={disabled || !document.track} onClick={() => { close(); onDetectBeats(); }}>
                <EditorIcon name="beats" /> {beats.length ? "Detect beats again" : "Detect beats"}
              </button>
            </div>}
          </EditorPopover>
          <div
            className={styles.timelineZoom}
            role="group"
            aria-label="Timeline zoom"
          >
            <button
              aria-label="Zoom out timeline"
              disabled={zoom <= 1}
              onClick={() => setZoom((value) => Math.max(1, value / 2))}
            >
              <EditorIcon name="minus" size={14} />
            </button>
            <span aria-label={`${zoom} times zoom`}>{zoom}×</span>
            <button
              aria-label="Zoom in timeline"
              disabled={zoom >= 16}
              onClick={() => setZoom((value) => Math.min(16, value * 2))}
            >
              <EditorIcon name="plus" size={14} />
            </button>
            <button
              onClick={() => setZoom(1)}
              aria-label="Fit timeline"
              className={toolStyles.toolButton}
              title="Fit the whole edit in the timeline"
            >
              <EditorIcon name="fit" size={15} />
            </button>
          </div>
          <button
            className={toolStyles.toolButton}
            onClick={() => setExpanded((value) => !value)}
            aria-expanded={expanded}
            aria-label={expanded ? "Collapse timeline" : "Expand timeline"}
            title={
              expanded
                ? "Return to standard timeline height"
                : "Expand the timeline to see more prompt detail"
            }
          >
            <EditorIcon name={expanded ? "collapse" : "expand"} />
          </button>
          <EditorPopover
            title="Cut tools"
            label="Cut tools"
            triggerLabel="Cut tools"
            triggerTitle="Adjust the selected clip or suggest new cuts"
            triggerClassName={toolStyles.toolButton}
            width={360}
          >
            {(close) => (
              <div className={styles.timelineOptions}>
                <button disabled={disabled || !canSetEnd} onClick={() => { close(); onSetEnd(); }}>
                  <EditorIcon name="end" /> End selected clip at playhead
                </button>
                <button disabled={disabled || !canJoinNext} onClick={() => { close(); onJoinNext(); }}>
                  <EditorIcon name="minus" /> Join with following clip
                </button>
                <section className={styles.replanCuts}>
                  <div>
                    <strong>Suggest cuts from music</strong>
                    <p>
                      Replaces current cuts and scenes with empty placeholders; Undo restores your edit. To rebuild the whole video, generate from AI direction.
                      {!canReplan && " Choose music and unlock placed clips first."}
                    </p>
                  </div>
                  <button
                    disabled={disabled || !canReplan}
                    onClick={() => {
                      close();
                      onReplan();
                    }}
                  >
                    <EditorIcon name="sparkles" /> Suggest cuts
                  </button>
                </section>
              </div>
            )}
          </EditorPopover>
        </div>
      </div>
      {plan.provisional_timing && (
        <p className={toolStyles.starterNotice}>Starter layout · Generate edit shapes the cuts around the music and available scenes.</p>
      )}
      <div className={styles.timelineTracks}>
        <div className={styles.trackNames} aria-hidden="true">
          <div className={styles.trackRulerSpacer}>Cuts</div>
          <div className={styles.clipTrackName}>
            <b>V1</b>
            <span>Scenes</span>
          </div>
          <div className={styles.musicTrackName}>
            <b>A1</b>
            <span>Music</span>
          </div>
          {onSelectDialogue && <div className={dialogueStyles.trackName} style={{ height: dialogueLayout.height }}><b>A2</b><span>Dialogue</span></div>}
          {showCues && <div className={cueStyles.trackName}>
            <b>Cues</b>
            <small>AI / Lyrics</small>
          </div>}
        </div>
        <div ref={scroller} className={styles.timelineScroll}
          onScroll={(event) => {
            // Browsers reset scroll offsets while display:none. Keep the last
            // visible position until the mounted editor is revealed again.
            if (event.currentTarget.clientWidth > 0 && !hiddenViewport.current)
              rememberScroll(event.currentTarget.scrollLeft);
          }}>
          <div
            ref={surface}
            className={styles.timelineCanvas}
            style={{ width: `${zoom * 100}%` }}
            {...handlers}
            onDoubleClick={(event) => {
              if (
                disabled ||
                (event.target as Element).closest("[data-cut-marker]")
              )
                return;
              // The first click moves the playhead underneath the pointer.
              // Handle its shared ancestor so the second click still splits.
              const ruler = event.currentTarget
                .querySelector("[data-timeline-ruler]")
                ?.getBoundingClientRect();
              const rect = event.currentTarget.getBoundingClientRect();
              if (
                ruler &&
                event.clientY >= ruler.top &&
                event.clientY <= ruler.bottom
              )
                onSplitAt(
                  timeAt(event.clientX, rect.left, rect.width, !event.altKey),
                );
            }}
          >
            <div
              className={styles.ruler}
              data-timeline-ruler
              data-playback-space
              tabIndex={0}
              aria-label="Time ruler. Click to seek; double-click to add a cut. M adds a cut at the playhead."
              onPointerDown={(event) => begin(event, "seek")}
            >
              {ticks.map((time) => (
                <span
                  key={time}
                  style={{ left: `${(time / duration) * 100}%` }}
                >
                  {formatTime(time).replace(/(\.\d)0$/, "$1")}
                </span>
              ))}
            </div>
            <div
              className={styles.clipLane}
              aria-label="Video clips and search prompts"
            >
              {plan.slots.map((slot, index) => {
                const clip = clips.get(slot.clip_id ?? ""),
                  direction = directionOf(document, slot);
                const acceptsScene =
                  !disabled &&
                  !clip?.locked &&
                  draggedSceneDuration !== null &&
                  draggedSceneDuration + 1e-6 >= slot.end - slot.start;
                const query = direction.query.trim();
                const beginAt =
                  dragCut?.index === index ? dragCut.value : slot.start;
                const endAt =
                  dragCut?.index === index + 1 ? dragCut.value : slot.end;
                const renderedWidth = ((endAt - beginAt) / duration) * viewportWidth * zoom;
                const pending = slot.needs_direction || !query;
                const title = clip
                  ? filmTitles[clip.film_id] || `Clip ${index + 1}`
                  : "Scene needed";
                return (
                  <button
                    key={slot.id}
                    className={`${styles.timelineClip} ${toolStyles.dropTarget} ${clip ? "" : styles.gapClip} ${renderedWidth < 110 ? styles.compactClip : ""} ${renderedWidth < 55 ? styles.narrowClip : ""} ${selectedId === slot.id && activeCutIndex === null ? styles.selectedClip : ""}`}
                    data-lab-scene-slot={slot.id}
                    data-accepts-scene={acceptsScene}
                    style={{
                      left: `${position(beginAt)}%`,
                      width: `${((endAt - beginAt) / duration) * 100}%`,
                    }}
                    aria-label={`Clip ${index + 1}, ${seconds(slot.start - start)} to ${seconds(slot.end - start)}. ${title}. ${pending ? "Prompt needs attention. " : ""}${clip && query ? "Search intent: " : ""}${query || "No prompt yet."}`}
                    data-playback-space
                    title={`${title}\n${query ? `${clip ? "Search intent: " : ""}${query}` : "Plan a prompt or describe the scene in the inspector."}${direction.purpose ? `\n${direction.purpose}` : ""}`}
                    aria-pressed={
                      selectedId === slot.id && activeCutIndex === null
                    }
                    onClick={() => selectSlot(slot.id, slot.start)}
                  >
                    {clip?.unit_id && (
                      <img
                        src={mediaUrl(
                          `/media/keyframe/${encodeURIComponent(clip.unit_id)}/0`,
                        )}
                        alt=""
                        draggable={false}
                        loading="lazy"
                        onError={(event) => {
                          event.currentTarget.style.visibility = "hidden";
                        }}
                      />
                    )}
                    <span className={styles.clipTopline}>
                      <b>
                        {String(index + 1).padStart(2, "0")}
                        {clip?.locked ? " · Locked" : ""}
                      </b>
                      <span>{Number((endAt - beginAt).toFixed(2))}s</span>
                    </span>
                    <span className={styles.clipImageLabel}>{title}</span>
                    <span className={styles.clipDirection}>
                      <span className={styles.clipDirectionMeta}>
                        <span>
                          {clip ? "Search intent" : facetLabels[direction.search_facet] ??
                            "Scene search"}
                        </span>
                        {pending && (
                          <em className={styles.pendingDirection}>
                            {query ? "Update prompt" : "Needs prompt"}
                          </em>
                        )}
                      </span>
                      <span
                        className={`${styles.clipPrompt} ${query ? "" : styles.emptyPrompt}`}
                      >
                        {query ||
                          "Describe a scene, or let AI plan this moment."}
                      </span>
                      {expanded &&
                        (direction.purpose || direction.music_cue) && (
                          <span className={styles.clipPurpose}>
                            {direction.purpose || direction.music_cue}
                          </span>
                        )}
                    </span>
                  </button>
                );
              })}
              {visiblePair && <div
                className={toolStyles.previewPair}
                style={{ left: `${position(visiblePair.start)}%`, width: `${((visiblePair.end - visiblePair.start) / duration) * 100}%` }}
                role="img"
                aria-label={`Preview affects clips ${previewIndex + 1} and ${previewIndex + 2}; proposed cut at ${seconds(visiblePair.cut - start)}. The saved edit is unchanged.`}
              />}
            </div>
            <div
              className={styles.musicLane}
              onPointerDown={(event) => begin(event, "seek")}
              data-playback-space
              tabIndex={0}
              aria-label="Music waveform. Click or drag to seek."
            >
              <svg
                viewBox={`0 0 ${viewportWidth} 72`}
                style={{ position: "absolute", left: scrollLeft, width: viewportWidth, pointerEvents: "none" }}
                preserveAspectRatio="none"
                aria-hidden="true"
              >
                <path
                  d={waveform}
                  stroke="currentColor"
                  strokeWidth=".8"
                  fill="none"
                />
              </svg>
              {showBeats &&
                beats.map((beat, i) => (
                  <i
                    key={i}
                    className={styles.beatLine}
                    style={{ left: `${position(beat)}%` }}
                    title={`Beat ${seconds(beat - start)}`}
                  />
                ))}
              {showBeats &&
                downbeats.map((beat, i) => (
                  <i
                    key={i}
                    className={styles.downbeatLine}
                    style={{ left: `${position(beat)}%` }}
                    title={`Downbeat ${seconds(beat - start)}`}
                  />
                ))}
              {!!document.audio_fade_in_seconds && (
                <i
                  className={styles.fadeShape}
                  style={{
                    width: `${Math.min(100, (document.audio_fade_in_seconds / duration) * 100)}%`,
                  }}
                  aria-hidden="true"
                />
              )}
              {waveformStatus && (
                <span className={styles.waveformMessage}>{waveformStatus}</span>
              )}
            </div>
            {onSelectDialogue && <div className={dialogueStyles.lane} style={{ height: dialogueLayout.height }} aria-label="Dialogue lane">
              {!(document.dialogue_clips?.length) && <span>Use dialogue from a scene</span>}
              {(document.dialogue_clips ?? []).map((voice, index) => <button type="button" key={voice.id}
                className={dialogueStyles.voiceClip} disabled={disabled} aria-pressed={selectedDialogueId === voice.id}
                data-moving={dragDialogue?.id === voice.id}
                aria-label={`Dialogue from ${filmTitles[voice.film_id] ?? voice.title}, ${seconds(voice.start - start)} to ${seconds(dialogueEnd(voice) - start)}`}
                style={{ left: `${position(dragDialogue?.id === voice.id ? dragDialogue.start : voice.start)}%`, width: `${(dialogueEnd(voice) - voice.start) / duration * 100}%`,
                  top: 6 + (dialogueLayout.rows.get(voice.id) ?? 0) * 30, height: dialogueLayout.count > 1 ? 26 : 35,
                  zIndex: dragDialogue?.id === voice.id ? 4 : selectedDialogueId === voice.id ? 3 : 1 }}
                onPointerDown={(event) => { if (onMoveDialogue) begin(event, "dialogue", index); else event.stopPropagation(); }}
                onDoubleClick={(event) => event.stopPropagation()}
                onKeyDown={(event) => {
                  if (event.key !== "Delete" && event.key !== "Backspace") return;
                  event.preventDefault(); event.stopPropagation();
                  if (!disabled && !event.repeat) onRemoveDialogue?.(voice.id);
                }}
                onClick={() => { if (suppressDialogueClick.current) { suppressDialogueClick.current = false; return; } onSelectDialogue(voice.id); onSeek(voice.start); }}>
                <strong>{voice.text || filmTitles[voice.film_id] || voice.title || "Film voice"}</strong>
                <small>{dragDialogue?.id === voice.id ? `Start ${seconds(dragDialogue.start - start)}` : `${seconds(voice.source_end - voice.source_start)} · ${voice.gain_db > 0 ? "+" : ""}${voice.gain_db} dB`}</small>
              </button>)}
            </div>}
            {showCues && <MusicalCues
              cues={cues}
              start={start}
              end={end}
              selectedId={selectedCueId}
              onSelect={(cue) => {
                if (disabled) return;
                setSelectedCueId(cue.id);
                setSelectedCutId(null);
                onSeek(cue.start);
              }}
              onClose={(id) => setSelectedCueId((current) => current === id ? null : current)}
            />}
            <div className={styles.cutStrip} role="group" aria-label="Cut markers" data-timeline-cut-strip>
              {plan.slots.slice(1).map((slot, offset) => {
                const index = offset + 1,
                  value = dragCut?.index === index ? dragCut.value : slot.start,
                  locked = cutIsLocked(index);
                const previous = index > 1
                  ? dragCut?.index === index - 1 ? dragCut.value : plan.slots[index - 1].start
                  : start;
                const next = index + 1 < plan.slots.length
                  ? dragCut?.index === index + 1 ? dragCut.value : plan.slots[index + 1].start
                  : end;
                const labelSpacing = Math.min(value - previous, next - value) / duration * viewportWidth * zoom;
                const crowded = labelSpacing < 20 && activeCutIndex !== index && dragCut?.index !== index;
                return (
                  <button
                    key={slot.id}
                    role="slider"
                    data-cut-marker
                    aria-orientation="horizontal"
                    aria-label={`Cut ${index}${locked ? ", locked" : ""}`}
                    aria-describedby={hintId}
                    data-playback-space
                    aria-valuemin={Math.min(
                      value - start,
                      plan.slots[index - 1].start - start + 0.25,
                    )}
                    aria-valuemax={Math.max(
                      value - start,
                      slot.end - start - 0.25,
                    )}
                    aria-valuenow={value - start}
                    aria-valuetext={`${seconds(value - start)} into the edit`}
                    disabled={disabled || locked}
                    className={`${styles.cutHandle} ${crowded ? styles.crowdedCut : ""} ${activeCutIndex === index ? styles.selectedCut : ""}`}
                    style={{ left: `${position(value)}%` }}
                    onFocus={() => setSelectedCutId(slot.id)}
                    onPointerDown={(event) => begin(event, "cut", index)}
                    onClick={(event) => {
                      if (event.detail === 0) {
                        setSelectedCutId(slot.id);
                        onSeek(slot.start);
                      }
                    }}
                    onKeyDown={(event) => {
                      if (
                        (event.key === "ArrowLeft" ||
                          event.key === "ArrowRight") &&
                        !event.altKey &&
                        !event.ctrlKey &&
                        !event.metaKey
                      ) {
                        event.preventDefault();
                        nudgeCut(
                          index,
                          (event.key === "ArrowLeft" ? -1 : 1) *
                            (event.shiftKey ? 0.5 : 1 / document.fps),
                        );
                      }
                    }}
                    title={
                      locked
                        ? "Unlock the adjacent clips to edit this cut"
                        : `Cut ${index} · ${seconds(value - start)}. Drag to move; arrow keys nudge one frame; Shift + arrow nudges 0.5s; Delete joins.`
                    }
                  >
                    <i aria-hidden="true">{index}</i>
                    {dragCut?.index === index && (
                      <span className={styles.cutTime}>
                        {seconds(value - start)}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
            <i
              className={styles.playheadLine}
              style={{
                left: `${Math.max(0, Math.min(100, position(playhead)))}%`,
              }}
              aria-hidden="true"
            />
            {visiblePair && <div
              className={toolStyles.previewCut}
              style={{ left: `${position(visiblePair.cut)}%` }}
              aria-hidden="true"
            >
              <span className={position(visiblePair.cut) > 75 ? toolStyles.previewCutLabelLeft : ""}>Preview cut</span>
            </div>}
            <button
              role="slider"
              aria-label="Edit playhead"
              aria-orientation="horizontal"
              data-playback-space
              aria-valuemin={0}
              aria-valuemax={duration}
              aria-valuenow={Math.max(0, Math.min(duration, playhead - start))}
              aria-valuetext={seconds(playhead - start)}
              className={styles.editPlayhead}
              style={{
                left: `${Math.max(0, Math.min(100, position(playhead)))}%`,
              }}
              onPointerDown={(event) => begin(event, "seek")}
              onKeyDown={(event) => {
                if (
                  !disabled &&
                  ["ArrowLeft", "ArrowRight", "Home", "End"].includes(
                    event.key,
                  ) &&
                  !event.altKey &&
                  !event.ctrlKey &&
                  !event.metaKey
                ) {
                  event.preventDefault();
                  setSelectedCutId(null);
                  onSeek(
                    event.key === "Home"
                      ? start
                      : event.key === "End"
                        ? end
                        : Math.max(
                            start,
                            Math.min(
                              end,
                              playhead +
                                (event.key === "ArrowLeft" ? -1 : 1) *
                                  (event.shiftKey ? 1 : 1 / document.fps),
                            ),
                          ),
                  );
                }
              }}
              title="Drag to scrub. Arrow keys move one frame; Home / End jump to the section edges."
            >
              <span aria-hidden="true" />
            </button>
          </div>
        </div>
      </div>
      <div className={styles.timelineFooter}>
        <p id={hintId} className={styles.timelineHint}>
          Double-click the ruler to split. Drag numbered cuts above the clips to retime.{" "}
          {showBeats && beats.length > 0 && "Thin lines are beat guides. "}
          <kbd>Space</kbd> play / pause · <kbd>M</kbd> add cut
        </p>
        {activeCutIndex !== null && activeCutTime !== null && (
          <div className={styles.cutSelection} aria-label="Selected cut">
            <span>
              Cut {activeCutIndex} <b>{seconds(activeCutTime - start)}</b>
            </span>
            <button
              onClick={() => nudgeCut(activeCutIndex, -1 / document.fps)}
              disabled={!canMoveCut(activeCutIndex)}
              aria-label="Nudge selected cut one frame earlier"
              title="One frame earlier (Left arrow)"
            >
              <EditorIcon name="previous" size={14} />
            </button>
            <button
              onClick={() => nudgeCut(activeCutIndex, 1 / document.fps)}
              disabled={!canMoveCut(activeCutIndex)}
              aria-label="Nudge selected cut one frame later"
              title="One frame later (Right arrow)"
            >
              <EditorIcon name="next" size={14} />
            </button>
            <button
              className={toolStyles.toolButton}
              onClick={() => joinCut(activeCutIndex)}
              disabled={disabled || cutIsLocked(activeCutIndex)}
              title="Remove this cut and join its two clips (Delete)"
            >
              <EditorIcon name="trash" size={14} />
              Remove cut
            </button>
          </div>
        )}
      </div>
    </section>
  );
}
