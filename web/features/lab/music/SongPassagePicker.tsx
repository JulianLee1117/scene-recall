"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type PointerEvent,
} from "react";
import { mediaUrl, seconds } from "@/lib/lab";
import { MAX_MUSIC_PASSAGE_SECONDS } from "@/lib/labLimits";
import { isPlaybackSpace } from "@/lib/playbackShortcut";
import type { LabDocument } from "@/types/lab";
import { useAudioWaveform } from "./useAudioWaveform";
import EditorIcon from "@/features/lab/kit/EditorIcon";
import styles from "./songPassagePicker.module.css";

type Range = { start: number; end: number };
type Props = {
  document: LabDocument;
  disabled: boolean;
  upload: (file: File) => Promise<void>;
  onApply: (range: Range, fade: number) => void;
  onClose: () => void;
};
type Drag = {
  kind: "move" | "start" | "end" | "seek";
  pointer: number;
  x: number;
  left: number;
  scale: number;
  viewStart: number;
  initial: Range;
  initialFade: number;
  initialHead: number;
  wasPlaying: boolean;
  moved: boolean;
};
const clamp = (value: number, low: number, high: number) =>
  Math.max(low, Math.min(high, value));
const round = (value: number) => Math.round(value * 100) / 100;
const clock = (value: number) => seconds(value).replace(/\.\d+$/, "");

function validRange(range: Range, duration: number): Range {
  const length = clamp(range.end - range.start, 0.1, Math.min(MAX_MUSIC_PASSAGE_SECONDS, duration));
  const start = clamp(range.start, 0, Math.max(0, duration - length));
  return { start, end: Math.min(duration, start + length) };
}

export default function SongPassagePicker({
  document,
  disabled,
  upload,
  onApply,
  onClose,
}: Props) {
  const track = document.track;
  const duration = track?.duration ?? 30;
  const initial = validRange(document.passage, duration);
  const [range, setRange] = useState<Range>(initial);
  const rangeRef = useRef(range);
  rangeRef.current = range;
  const [fade, setFade] = useState(
    Math.min(document.audio_fade_in_seconds ?? 0, initial.end - initial.start),
  );
  const fadeRef = useRef(fade);
  fadeRef.current = fade;
  const [head, setHead] = useState(initial.start);
  const [playing, setPlaying] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [view, setView] = useState<Range>({ start: 0, end: duration });
  const [dragging, setDragging] = useState<Drag["kind"] | null>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const picker = useRef<HTMLElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const waveform = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const { peaks, status } = useAudioWaveform(track);
  const locked = disabled || uploading;
  const shortcut = useRef({
    locked,
    hasTrack: !!track,
    toggle: () => void play(),
  });
  shortcut.current = { locked, hasTrack: !!track, toggle: () => void play() };
  const length = range.end - range.start;
  const span = Math.max(0.1, view.end - view.start);
  const maxZoom = Math.log2(duration / Math.min(1, duration));
  const zoomLevel = Math.log2(duration / span);
  const left = clamp(((range.start - view.start) / span) * 100, 0, 100);
  const right = clamp(((range.end - view.start) / span) * 100, 0, 100);
  const percent = (time: number) => ((time - view.start) / span) * 100;

  useEffect(() => {
    const next = validRange(document.passage, duration);
    rangeRef.current = next;
    setRange(next);
    setFade(
      Math.min(document.audio_fade_in_seconds ?? 0, next.end - next.start),
    );
    setHead(next.start);
    setPlaying(false);
    setError("");
    setView({ start: 0, end: duration });
    const player = audio.current;
    if (player) {
      player.pause();
      player.currentTime = next.start;
    }
    return () => player?.pause();
    // A new upload resets the local draft. Auditioning never mutates the project.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [track?.id]);

  useEffect(() => {
    function handleSpace(event: globalThis.KeyboardEvent) {
      const root = picker.current;
      const target = event.target;
      if (
        !root ||
        shortcut.current.locked ||
        !shortcut.current.hasTrack ||
        !(target instanceof Node) ||
        !isPlaybackSpace(event)
      )
        return;
      const dialog = root.closest("dialog");
      if (dialog && !dialog.open) return;
      if (!(dialog ?? root).contains(target)) return;
      event.preventDefault();
      if (!event.repeat) shortcut.current.toggle();
    }
    window.addEventListener("keydown", handleSpace);
    return () => window.removeEventListener("keydown", handleSpace);
  }, []);

  function pause() {
    audio.current?.pause();
    setPlaying(false);
  }
  function updatePlayback(player: HTMLAudioElement) {
    const selected = rangeRef.current;
    const trimming = drag.current && drag.current.kind !== "seek";
    if (!player.paused && !trimming && player.currentTime >= selected.end) {
      player.pause();
      setPlaying(false);
    }
    player.volume =
      fadeRef.current > 0 && player.currentTime >= selected.start
        ? clamp((player.currentTime - selected.start) / fadeRef.current, 0, 1)
        : 1;
    setHead(player.currentTime);
    if (!player.paused && !drag.current) reveal(player.currentTime);
  }
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(() => {
      if (audio.current) updatePlayback(audio.current);
    }, 25);
    return () => window.clearInterval(timer);
  }, [playing]);

  function seek(time: number) {
    const value = clamp(time, 0, duration);
    setHead(value);
    if (audio.current) audio.current.currentTime = value;
  }
  function setSelection(next: Range) {
    rangeRef.current = next;
    setRange(next);
    setFade((value) => Math.min(value, next.end - next.start));
    const player = audio.current;
    if (player && !player.paused) {
      // Editing boundaries never relocates a running transport.
      updatePlayback(player);
      return;
    }
    const current = player?.currentTime ?? head;
    const nextHead = clamp(current, next.start, next.end);
    if (nextHead !== current) seek(nextHead);
  }
  function scrub(time: number) {
    const selected = rangeRef.current;
    const value = clamp(time, selected.start, selected.end);
    seek(value);
  }
  async function play() {
    const player = audio.current;
    if (!player || locked) return;
    if (!player.paused) {
      pause();
      return;
    }
    setError("");
    const selected = rangeRef.current;
    if (
      player.currentTime < selected.start ||
      player.currentTime >= selected.end - 0.02
    ) {
      player.currentTime = selected.start;
    }
    setHead(player.currentTime);
    reveal(player.currentTime);
    player.volume =
      fadeRef.current > 0
        ? clamp((player.currentTime - selected.start) / fadeRef.current, 0, 1)
        : 1;
    try {
      await player.play();
    } catch {
      setError("This audio could not play. Try again or choose another song.");
    }
  }
  function begin(event: PointerEvent<HTMLElement>, kind: Drag["kind"]) {
    if (locked || drag.current || event.button !== 0 || !waveform.current) return;
    event.preventDefault();
    event.stopPropagation();
    const box = waveform.current.getBoundingClientRect();
    drag.current = {
      kind,
      pointer: event.pointerId,
      x: event.clientX,
      left: box.left,
      scale: span / box.width,
      viewStart: view.start,
      initial: { ...rangeRef.current },
      initialFade: fadeRef.current,
      initialHead: audio.current?.currentTime ?? head,
      wasPlaying: Boolean(audio.current && !audio.current.paused),
      moved: false,
    };
    setDragging(kind);
    // The edge can leave the viewport while dragging. Capture on the stable
    // waveform so its unmount cannot strand or interrupt the gesture.
    waveform.current.setPointerCapture(event.pointerId);
    event.currentTarget.focus();
    if (kind === "seek")
      scrub(view.start + ((event.clientX - box.left) * span) / box.width);
  }
  function move(event: PointerEvent<HTMLElement>) {
    const current = drag.current;
    if (!current || current.pointer !== event.pointerId || locked) return;
    if (Math.abs(event.clientX - current.x) > 3) current.moved = true;
    if (current.kind === "seek") {
      scrub(current.viewStart + (event.clientX - current.left) * current.scale);
      return;
    }
    if (!current.moved) return;
    const delta = (event.clientX - current.x) * current.scale;
    const next = { ...current.initial };
    if (current.kind === "move") {
      const width = next.end - next.start;
      next.start = clamp(
        round(current.initial.start + delta),
        0,
        duration - width,
      );
      next.end = next.start + width;
    } else if (current.kind === "start") {
      next.start = clamp(
        round(current.initial.start + delta),
        Math.max(0, next.end - MAX_MUSIC_PASSAGE_SECONDS),
        next.end - 0.1,
      );
    } else {
      next.end = clamp(
        round(current.initial.end + delta),
        next.start + 0.1,
        Math.min(duration, next.start + MAX_MUSIC_PASSAGE_SECONDS),
      );
    }
    setSelection(next);
  }
  function finish(event: PointerEvent<HTMLElement>, cancel = false) {
    const current = drag.current;
    if (!current || current.pointer !== event.pointerId) return;
    if (cancel) {
      setSelection(current.initial);
      setFade(current.initialFade);
      fadeRef.current = current.initialFade;
      if (!current.wasPlaying) seek(current.initialHead);
    }
    else if (current.kind === "move" && !current.moved) {
      scrub(current.viewStart + (event.clientX - current.left) * current.scale);
    }
    drag.current = null;
    setDragging(null);
    if (waveform.current?.hasPointerCapture(event.pointerId))
      waveform.current.releasePointerCapture(event.pointerId);
    if (audio.current && !audio.current.paused) updatePlayback(audio.current);
  }
  function keyboard(event: KeyboardEvent<HTMLElement>, kind: Drag["kind"]) {
    if (
      locked ||
      !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
    )
      return;
    event.preventDefault();
    const amount =
      (event.key === "ArrowLeft" ? -1 : 1) * (event.altKey ? 0.01 : event.shiftKey ? 1 : 0.1);
    const advance = (value: number, low: number, high: number) =>
      event.key === "Home"
        ? low
        : event.key === "End"
          ? high
          : clamp(round(value + amount), low, high);
    if (kind === "seek") {
      scrub(advance(head, range.start, range.end));
      return;
    }
    if (kind === "start")
      setSelection({
        start: advance(
          range.start,
          Math.max(0, range.end - MAX_MUSIC_PASSAGE_SECONDS),
          range.end - 0.1,
        ),
        end: range.end,
      });
    if (kind === "end")
      setSelection({
        start: range.start,
        end: advance(
          range.end,
          range.start + 0.1,
          Math.min(duration, range.start + MAX_MUSIC_PASSAGE_SECONDS),
        ),
      });
    if (kind === "move") {
      const start = advance(range.start, 0, duration - length);
      setSelection({ start, end: start + length });
    }
  }
  function frameView(width: number, center: number) {
    width = clamp(width, Math.min(1, duration), duration);
    const start = clamp(center - width / 2, 0, duration - width);
    setView({ start, end: start + width });
  }
  function zoom(value: number) {
    const width = duration / 2 ** clamp(value, 0, maxZoom);
    const position = audio.current?.currentTime ?? head;
    const visible = position >= view.start && position <= view.end;
    const running = audio.current && !audio.current.paused;
    const anchor = visible || running
      ? position : (view.start + view.end) / 2;
    // Keep the anchor at the same screen position. In particular, zooming
    // while playing from IN does not pan the beginning out of view.
    const fraction = visible ? (anchor - view.start) / span : running ? 0.15 : 0.5;
    const start = clamp(anchor - fraction * width, 0, duration - width);
    setView({ start, end: start + width });
  }
  function reveal(time: number) {
    setView((current) => {
      if (time >= current.start && time <= current.end) return current;
      const width = current.end - current.start;
      const start = clamp(time - width * 0.15, 0, duration - width);
      return { start, end: start + width };
    });
  }
  const pointerHandlers = {
    onPointerMove: move,
    onPointerUp: (event: PointerEvent<HTMLElement>) => finish(event),
    onPointerCancel: (event: PointerEvent<HTMLElement>) => finish(event, true),
    onLostPointerCapture: (event: PointerEvent<HTMLElement>) => finish(event, true),
  };
  const wave = useMemo(() => {
    if (!peaks.length) return "";
    const count = 700;
    return Array.from({ length: count }, (_, index) => {
      const from = Math.floor(
        ((view.start + (span * index) / count) / duration) * peaks.length,
      );
      const to = Math.max(
        from + 1,
        Math.ceil(
          ((view.start + (span * (index + 1)) / count) / duration) *
            peaks.length,
        ),
      );
      let peak = 0;
      for (
        let sample = Math.max(0, from);
        sample < Math.min(peaks.length, to);
        sample++
      )
        peak = Math.max(peak, peaks[sample]);
      const height = Math.max(0.5, peak * 40);
      return `M${((index + 0.5) / count) * 1000},${50 - height}v${height * 2}`;
    }).join(" ");
  }, [peaks, view.start, span, duration]);

  return (
    <section
      ref={picker}
      className={styles.picker}
      aria-label="Choose a music section"
    >
      <header className={styles.header}>
        <div>
          <h2>{track ? track.name : "Choose your music"}</h2>
          <p>
            {track
              ? `${clock(duration)} song · select up to ${MAX_MUSIC_PASSAGE_SECONDS / 60} minutes`
              : "Pick a song to start your edit."}
          </p>
        </div>
        {track && (
          <button
            type="button"
            className={styles.quiet}
            disabled={locked}
            onClick={() => input.current?.click()}
          >
            Change song
          </button>
        )}
      </header>
      <input
        ref={input}
        hidden
        type="file"
        accept="audio/*,.mp3,.wav,.flac,.m4a,.ogg"
        onChange={async (event) => {
          const file = event.target.files?.[0];
          event.target.value = "";
          if (!file || locked) return;
          pause();
          setError("");
          setUploading(true);
          try {
            await upload(file);
          } catch (reason) {
            setError(
              reason instanceof Error
                ? reason.message
                : "This song could not be imported.",
            );
          } finally {
            setUploading(false);
          }
        }}
      />
      {!track ? (
        <button
          type="button"
          className={styles.upload}
          disabled={locked}
          onClick={() => input.current?.click()}
        >
          <span aria-hidden="true">♪</span>
          <strong>
            {uploading ? "Importing song…" : "Choose an audio file"}
          </strong>
          <small>MP3, WAV, FLAC and other audio formats</small>
        </button>
      ) : (
        <>
          <audio
            key={track.id}
            ref={audio}
            src={mediaUrl(`/lab/tracks/${encodeURIComponent(track.id)}/audio`)}
            preload="metadata"
            onLoadedMetadata={(event) => {
              event.currentTarget.currentTime = rangeRef.current.start;
            }}
            onPlay={() => setPlaying(true)}
            onPause={() => setPlaying(false)}
            onTimeUpdate={(event) => updatePlayback(event.currentTarget)}
            onEnded={() => setPlaying(false)}
            onError={() =>
              setError("This song is unavailable. Try choosing it again.")
            }
          />
          <div className={styles.waveTools}>
            <span className={styles.legend}><i /> Preview position</span>
            <div className={styles.zoomTools}>
              <div className={styles.zoomControl} role="group" aria-label="Waveform zoom controls">
                <button type="button" className={styles.zoomButton} aria-label="Zoom out waveform"
                  title="Zoom out" disabled={locked || zoomLevel <= 0.001} onClick={() => zoom(zoomLevel - 1)}>
                  <EditorIcon name="minus" size={14} />
                </button>
                <input type="range" aria-label="Waveform zoom" min={0}
                  max={maxZoom} step={0.05}
                  aria-valuetext={`${(duration / span).toFixed(1)} times zoom, ${span.toFixed(2)} seconds visible`}
                  title="Waveform zoom" disabled={locked} value={zoomLevel}
                  onChange={(event) => zoom(Number(event.target.value))} />
                <button type="button" className={styles.zoomButton} aria-label="Zoom in waveform"
                  title="Zoom in" disabled={locked || zoomLevel >= maxZoom - 0.001} onClick={() => zoom(zoomLevel + 1)}>
                  <EditorIcon name="plus" size={14} />
                </button>
              </div>
              <button type="button" className={styles.quiet} aria-label="Fit selected section"
                disabled={locked} onClick={() => frameView(length * 1.2, (range.start + range.end) / 2)}>
                Fit section
              </button>
              <button type="button" className={styles.quiet} aria-label="Fit song waveform"
                disabled={locked} onClick={() => setView({ start: 0, end: duration })}>
                Fit song
              </button>
            </div>
          </div>
          <div className={styles.ruler} aria-hidden="true">
            {Array.from({ length: 6 }, (_, index) => (
              <span key={index}>{span < 10 ? seconds(view.start + (span * index) / 5) : clock(view.start + (span * index) / 5)}</span>
            ))}
          </div>
          <div className={styles.scrubLane} role="group" aria-label="Scrub section preview"
            onPointerDown={(event) => begin(event, "seek")} {...pointerHandlers}>
            <div className={styles.previewRange} style={{ left: `${left}%`, width: `${right - left}%` }} />
            {head >= view.start && head <= view.end && (
              <button type="button" role="slider" aria-label="Song playhead" data-playback-space
                aria-valuemin={0} aria-valuemax={duration} aria-valuenow={head}
                aria-valuetext={seconds(head)} className={styles.playhead}
                style={{ left: `${percent(head)}%` }} disabled={locked}
                title={`Preview position ${seconds(head)} — drag to listen within the section`}
                onPointerDown={(event) => begin(event, "seek")}
                onKeyDown={(event) => keyboard(event, "seek")} {...pointerHandlers}>
                <span aria-hidden="true" />
              </button>
            )}
          </div>
          <div
            ref={waveform}
            className={styles.waveform}
            data-dragging={dragging || undefined}
            data-compact-handles={right - left < 10 || undefined}
            tabIndex={locked ? -1 : 0}
            role="group"
            aria-label="Song waveform"
            data-playback-space
            onPointerDown={(event) => begin(event, "seek")}
            {...pointerHandlers}
          >
            <svg
              viewBox="0 0 1000 100"
              preserveAspectRatio="none"
              aria-hidden="true"
            >
              <path
                d={wave}
                fill="none"
                stroke="currentColor"
                strokeWidth="1"
              />
            </svg>
            {!peaks.length && (
              <p className={styles.waveStatus}>
                {status || "Preparing waveform…"}
              </p>
            )}
            <div
              className={styles.shade}
              style={{ left: 0, width: `${left}%` }}
            />
            <div
              className={styles.shade}
              style={{ left: `${right}%`, right: 0 }}
            />
            {right > left && (
              <button
                type="button"
                role="slider"
                aria-label="Move selected section"
                data-playback-space
                aria-valuemin={0}
                aria-valuemax={Math.max(0, duration - length)}
                aria-valuenow={range.start}
                aria-valuetext={`${seconds(range.start)} to ${seconds(range.end)}`}
                className={styles.selection}
                style={{ left: `${left}%`, width: `${right - left}%` }}
                disabled={locked}
                onPointerDown={(event) => begin(event, "move")}
                onKeyDown={(event) => keyboard(event, "move")}
                {...pointerHandlers}
                title="Drag to move the section; click to preview here"
              />
            )}
            {range.start >= view.start && range.start <= view.end && (
              <button
                type="button"
                role="slider"
                aria-label="Section start"
                title={`Trim start · ${seconds(range.start)}`}
                data-playback-space
                aria-valuemin={Math.max(0, range.end - MAX_MUSIC_PASSAGE_SECONDS)}
                aria-valuemax={range.end - 0.1}
                aria-valuenow={range.start}
                aria-valuetext={seconds(range.start)}
                className={`${styles.handle} ${styles.startHandle}`}
                style={{ left: `${percent(range.start)}%` }}
                disabled={locked}
                onPointerDown={(event) => begin(event, "start")}
                onKeyDown={(event) => keyboard(event, "start")}
                {...pointerHandlers}
              >
                <span aria-hidden="true" />
              </button>
            )}
            {range.end >= view.start && range.end <= view.end && (
              <button
                type="button"
                role="slider"
                aria-label="Section end"
                title={`Trim end · ${seconds(range.end)}`}
                data-playback-space
                aria-valuemin={range.start + 0.1}
                aria-valuemax={Math.min(duration, range.start + MAX_MUSIC_PASSAGE_SECONDS)}
                aria-valuenow={range.end}
                aria-valuetext={seconds(range.end)}
                className={`${styles.handle} ${styles.endHandle}`}
                style={{ left: `${percent(range.end)}%` }}
                disabled={locked}
                onPointerDown={(event) => begin(event, "end")}
                onKeyDown={(event) => keyboard(event, "end")}
                {...pointerHandlers}
              >
                <span aria-hidden="true" />
              </button>
            )}
            {head >= view.start && head <= view.end && (
              <div className={styles.playheadLine} aria-hidden="true" style={{ left: `${percent(head)}%` }} />
            )}
            {[range.start, range.end].map((time, index) => time >= view.start && time <= view.end && (
              <div key={index} className={styles.trimLine} aria-hidden="true"
                style={{ left: `${percent(time)}%` }} />
            ))}
          </div>
            <input className={styles.scrollWave} type="range" aria-label="Scroll waveform"
              title="Scroll through the song" style={{ "--visible-share": `${Math.max(4, span / duration * 100)}%` } as CSSProperties}
              min={0} max={Math.max(0, duration - span)} step={0.01}
              disabled={locked || span >= duration - 0.01} value={view.start}
              onChange={(event) => {
                const start = Number(event.target.value);
                setView({ start, end: start + span });
              }} />
          <div className={styles.precision}>
            {(["start", "end"] as const).map((edge) => (
              <label key={edge}>
                <span>
                  {edge === "start" ? "Start" : "End"} · seconds
                  <button type="button" className={styles.quiet}
                    aria-label={`Show section ${edge}`} title={`Center waveform on section ${edge}`}
                    disabled={locked} onClick={() => {
                      frameView(span, range[edge]);
                    }}><EditorIcon name="fit" size={13} /></button>
                </span>
                <input type="number" aria-label={`Section ${edge} seconds`}
                  min={edge === "start" ? Math.max(0, range.end - MAX_MUSIC_PASSAGE_SECONDS) : range.start + 0.1}
                  max={edge === "start" ? range.end - 0.1 : Math.min(duration, range.start + MAX_MUSIC_PASSAGE_SECONDS)}
                  step={0.01} disabled={locked} value={round(range[edge])}
                  onChange={(event) => {
                    const value = event.target.valueAsNumber;
                    if (!Number.isFinite(value)) return;
                    const next = { ...range, [edge]: clamp(round(value),
                      edge === "start" ? Math.max(0, range.end - MAX_MUSIC_PASSAGE_SECONDS) : range.start + 0.1,
                      edge === "start" ? range.end - 0.1 : Math.min(duration, range.start + MAX_MUSIC_PASSAGE_SECONDS)) };
                    setSelection(next);
                  }} />
              </label>
            ))}
            <div className={styles.duration}><span>Duration</span><strong>{seconds(length)}</strong></div>
          </div>
          <div className={styles.audition}>
            <button
              type="button"
              className={styles.play}
              disabled={locked}
              onClick={() => void play()}
              aria-label={playing ? "Pause section" : "Play section"}
              aria-keyshortcuts="Space"
              title={playing ? "Pause (Space)" : "Play section (Space)"}
            >
              <EditorIcon name={playing ? "pause" : "play"} />
              {playing ? "Pause" : "Play section"}
            </button>
            <button type="button" className={styles.restart} aria-label="Restart section preview"
              title="Return preview to section start" disabled={locked}
              onClick={() => { scrub(range.start); reveal(range.start); }}><EditorIcon name="start" /></button>
            <span>
              Position {seconds(head)}
            </span>
            <button type="button" className={styles.quiet} disabled={locked}
              title="Expand the selected section; apply it when you are ready"
              onClick={() => {
                const length = Math.min(duration, MAX_MUSIC_PASSAGE_SECONDS);
                const start = Math.min(range.start, duration - length);
                setSelection({ start, end: start + length });
                setView({ start: 0, end: duration });
              }}>
              {duration <= MAX_MUSIC_PASSAGE_SECONDS ? "Use full song" : `Use ${MAX_MUSIC_PASSAGE_SECONDS / 60} minutes`}
            </button>
          </div>
          <p className={styles.hint}>
            Drag either edge to trim, or the selection to move it. Playback continues while you adjust.
          </p>
          <p className={styles.keyboard}>Trim handles: ← / → 0.1 s · Alt 0.01 s · Shift 1 s</p>
          <details className={styles.more}>
            <summary>More options</summary>
            <div className={styles.options}>
              <label className={styles.option}>
                <span>
                  Fade in{" "}
                  <small>{fade > 0 ? `${fade.toFixed(1)} s` : "Off"}</small>
                </span>
                <input
                  type="range"
                  aria-label="Section fade in seconds"
                  min={0}
                  max={Math.min(10, length)}
                  step={0.1}
                  disabled={locked}
                  value={fade}
                  onChange={(event) => setFade(Number(event.target.value))}
                />
              </label>
            </div>
          </details>
        </>
      )}
      {uploading && track && (
        <p className={styles.message} role="status">
          Importing song…
        </p>
      )}
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {document.clips.length > 0 &&
        (Math.abs(range.start - document.passage.start) > 0.001 ||
          Math.abs(range.end - document.passage.end) > 0.001) && (
          <p className={styles.changeNotice}>
            Using a different section starts a new edit. Undo restores these
            clips.
          </p>
        )}
      <footer className={styles.footer}>
        <button
          type="button"
          className={styles.cancel}
          disabled={locked}
          onClick={() => {
            pause();
            onClose();
          }}
        >
          Cancel
        </button>
        <button
          type="button"
          className={styles.apply}
          disabled={locked || !track || length < 0.099 || length > MAX_MUSIC_PASSAGE_SECONDS + 0.001}
          onClick={() => {
            pause();
            onApply({ ...range }, Math.min(fade, length));
          }}
        >
          Use this section{track ? ` · ${seconds(length)}` : ""}
        </button>
      </footer>
    </section>
  );
}
