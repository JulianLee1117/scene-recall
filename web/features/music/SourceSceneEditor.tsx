"use client";

import { useEffect, useRef, useState, type CSSProperties, type PointerEvent } from "react";
import { mediaUrl, seconds } from "@/lib/lab";
import { isPlaybackSpace } from "@/lib/playbackShortcut";
import type { Crop } from "@/types/lab";
import EditorIcon from "@/features/lab/EditorIcon";
import { nextSceneCropGeometry, nextSceneCropZoom, nextScenePanCrop, nextSceneZoomCrop } from "./nextScene";
import player from "./sequencePlayer.module.css";
import styles from "./nextSceneAudition.module.css";

export type SourceSceneValue = { source_start: number; crop: Crop | null };

type Props = {
  filmId: string;
  range: { start: number; end: number };
  duration: number;
  value: SourceSceneValue;
  outputRatio: number;
  disabled: boolean;
  readOnly?: boolean;
  suspended: boolean;
  onChange: (value: SourceSceneValue) => void;
  onTimeChange: (time: number) => void;
  onReadyChange?: (ready: boolean) => void;
};

/** Local, muted source adjustments. Only Preview with music contacts the server. */
export default function SourceSceneEditor(props: Props) {
  const { filmId, range, duration, value: draft, outputRatio, disabled, suspended, onChange } = props;
  const controlsDisabled = disabled || suspended || !!props.readOnly;
  const video = useRef<HTMLVideoElement>(null);
  const monitor = useRef<HTMLDivElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const rail = useRef<HTMLDivElement>(null);
  const latest = useRef(props);
  latest.current = props;
  const wanted = useRef(false);
  const playEpoch = useRef(0);
  const alive = useRef(true);
  const pendingSeek = useRef<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [ready, setReady] = useState(false);
  const [position, setPosition] = useState(0);
  const [sourceRatio, setSourceRatio] = useState<number | null>(null);
  const [error, setError] = useState("");
  const gesture = useRef<{ kind: "moment" | "crop"; pointer: number; x: number; y: number; draft: SourceSceneValue; width: number; height: number; target: HTMLElement } | null>(null);
  const bounds = { sourceMin: range.start, sourceMax: Math.max(range.start, range.end - duration) };
  const span = range.end - range.start;
  const geometry = nextSceneCropGeometry(draft.crop, sourceRatio ?? outputRatio, outputRatio);
  const zoom = nextSceneCropZoom(draft.crop, sourceRatio ?? outputRatio, outputRatio);
  const zoomLimit = useRef(4);
  zoomLimit.current = Math.max(zoomLimit.current, zoom, sourceRatio ? 2 * Math.max(sourceRatio / outputRatio, outputRatio / sourceRatio) : 4);
  const canPan = !!sourceRatio && !!draft.crop && (draft.crop.width < 1 || draft.crop.height < 1);

  function pause() { playEpoch.current += 1; wanted.current = false; video.current?.pause(); }
  function markReady(value: boolean) { setReady(value); latest.current.onReadyChange?.(value); }
  function checkReady(node: HTMLVideoElement) {
    if (node.videoWidth > 0 && node.videoHeight > 0) setSourceRatio(node.videoWidth / node.videoHeight);
    const current = latest.current;
    const start = current.value.source_start;
    const end = start + current.duration;
    const fits = Number.isFinite(node.duration) && start >= 0 && end <= node.duration + 1e-6;
    if (!fits && node.readyState >= 1) setError("This moment extends beyond the available source video.");
    const matchesSeek = pendingSeek.current === null || Math.abs(node.currentTime - pendingSeek.current) < 0.002;
    const valid = fits && !node.seeking && node.readyState >= 2 && matchesSeek && node.currentTime >= start - 0.002 && node.currentTime <= end + 0.002;
    if (valid) pendingSeek.current = null;
    markReady(valid);
  }
  function seek(time: number) {
    const node = video.current;
    if (!node || node.readyState < 1) return;
    const current = latest.current;
    const length = current.duration;
    const next = Math.max(current.value.source_start, Math.min(current.value.source_start + length - 0.001, time));
    pendingSeek.current = next;
    markReady(false);
    if (Math.abs(node.currentTime - next) < 1e-6 && !node.seeking) checkReady(node);
    else node.currentTime = next;
    setPosition(node.currentTime - current.value.source_start);
  }
  async function play() {
    const node = video.current;
    const current = latest.current;
    if (!node || current.disabled || current.suspended || node.readyState < 1 || current.value.source_start + current.duration > node.duration + 1e-6) return;
    const end = current.value.source_start + current.duration;
    if (node.currentTime < current.value.source_start || node.currentTime >= end - 0.01) seek(current.value.source_start);
    wanted.current = true;
    const epoch = ++playEpoch.current;
    setError("");
    try {
      await node.play();
      if (!alive.current || playEpoch.current !== epoch || latest.current.disabled || latest.current.suspended) node.pause();
    }
    catch (reason) {
      if (!alive.current || playEpoch.current !== epoch) return;
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      wanted.current = false;
      setError("The source could not play. Try Play again.");
    }
  }
  useEffect(() => {
    alive.current = true;
    const node = video.current;
    if (node && node.readyState >= 1) checkReady(node);
    monitor.current?.focus({ preventScroll: true });
    const keyboard = (event: KeyboardEvent) => {
      const dialog = document.querySelector("dialog[open]");
      if (latest.current.disabled || latest.current.suspended || (dialog && !dialog.contains(node)) || !isPlaybackSpace(event)) return;
      event.preventDefault();
      if (!event.repeat) { if (wanted.current) pause(); else void play(); }
    };
    const visibility = () => { if (document.hidden) pause(); };
    const timer = window.setInterval(() => {
      const current = latest.current;
      if (node && wanted.current && node.currentTime >= current.value.source_start + current.duration - 0.015) {
        pause();
        seek(current.value.source_start + current.duration);
      }
    }, 16);
    window.addEventListener("keydown", keyboard);
    document.addEventListener("visibilitychange", visibility);
    return () => { alive.current = false; playEpoch.current += 1; wanted.current = false; node?.pause(); window.clearInterval(timer); window.removeEventListener("keydown", keyboard); document.removeEventListener("visibilitychange", visibility); };
  }, []);
  useEffect(() => { if (disabled || suspended) pause(); }, [disabled, suspended]);
  useEffect(() => { pause(); markReady(false); setError(""); seek(draft.source_start); setPosition(0); }, [draft.source_start, duration]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      const active = gesture.current;
      if (event.key !== "Escape" || !active) return;
      event.preventDefault();
      gesture.current = null;
      if (active.target.hasPointerCapture(active.pointer)) active.target.releasePointerCapture(active.pointer);
      latest.current.onChange(active.draft);
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, []);

  function changeSource(time: number) {
    if (controlsDisabled) return;
    pause();
    onChange({ ...draft, source_start: Math.max(bounds.sourceMin, Math.min(bounds.sourceMax, time)) });
  }
  function startGesture(event: PointerEvent<HTMLElement>, kind: "moment" | "crop") {
    if (controlsDisabled || event.button !== 0 || (kind === "crop" && !canPan)) return;
    const rect = (kind === "moment" ? rail.current : viewport.current)?.getBoundingClientRect();
    if (!rect?.width || !rect.height) return;
    event.preventDefault();
    pause();
    event.currentTarget.focus({ preventScroll: true });
    event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = { kind, pointer: event.pointerId, x: event.clientX, y: event.clientY, draft, width: rect.width, height: rect.height, target: event.currentTarget };
  }
  function moveGesture(event: PointerEvent<HTMLElement>) {
    const active = gesture.current;
    if (!active || active.pointer !== event.pointerId || controlsDisabled) return;
    if (active.kind === "moment") {
      const shift = Math.round((event.clientX - active.x) / active.width * span * 24) / 24;
      changeSource(active.draft.source_start + shift);
    } else if (active.draft.crop) {
      onChange({ ...draft, crop: nextScenePanCrop(active.draft.crop,
        -(event.clientX - active.x) / active.width * active.draft.crop.width,
        -(event.clientY - active.y) / active.height * active.draft.crop.height) });
    }
  }
  function endGesture(event: PointerEvent<HTMLElement>) {
    if (gesture.current?.pointer !== event.pointerId) return;
    gesture.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }

  return <div className={`${player.player} ${styles.sourceEditor}`}>
    <div ref={monitor} className={`${player.monitor} ${styles.rawMonitor}`} tabIndex={-1} data-playback-space>
      <div className={player.frame} style={{ "--preview-ratio": outputRatio, aspectRatio: outputRatio } as CSSProperties}>
        <div ref={viewport} className={`${player.cropViewport} ${styles.cropViewport}`} style={{ width: `${geometry.width * 100}%`, height: `${geometry.height * 100}%` }}
          tabIndex={canPan && !controlsDisabled ? 0 : -1} role="group" aria-label="Reposition cropped scene with drag or arrow keys"
          data-playback-space data-can-pan={canPan && !controlsDisabled}
          onPointerDown={(event) => startGesture(event, "crop")} onPointerMove={moveGesture} onPointerUp={endGesture} onPointerCancel={endGesture} onLostPointerCapture={() => { gesture.current = null; }}
          onKeyDown={(event) => {
            if (!canPan || !draft.crop || controlsDisabled || !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
            event.preventDefault();
            const step = event.shiftKey ? 0.05 : 0.01;
            onChange({ ...draft, crop: nextScenePanCrop(draft.crop, event.key === "ArrowLeft" ? step : event.key === "ArrowRight" ? -step : 0, event.key === "ArrowUp" ? step : event.key === "ArrowDown" ? -step : 0) });
          }}>
          <video ref={video} src={mediaUrl(`/video/${encodeURIComponent(filmId)}`)} muted playsInline preload="auto" aria-label="Incoming scene, muted source preview"
            style={{ width: `${100 / geometry.crop.width}%`, height: `${100 / geometry.crop.height}%`, left: `${-geometry.crop.x / geometry.crop.width * 100}%`, top: `${-geometry.crop.y / geometry.crop.height * 100}%` }}
            onLoadedMetadata={(event) => { const node = event.currentTarget; if (node.videoWidth && node.videoHeight) setSourceRatio(node.videoWidth / node.videoHeight); seek(latest.current.value.source_start); }}
            onSeeking={() => markReady(false)}
            onSeeked={(event) => checkReady(event.currentTarget)}
            onCanPlay={(event) => checkReady(event.currentTarget)}
            onWaiting={() => markReady(false)}
            onTimeUpdate={(event) => { const offset = Math.max(0, Math.min(duration, event.currentTarget.currentTime - draft.source_start)); setPosition(offset); latest.current.onTimeChange(draft.source_start + offset); }}
            onPlaying={(event) => { if (!wanted.current || latest.current.disabled || latest.current.suspended) event.currentTarget.pause(); else { setPlaying(true); checkReady(event.currentTarget); } }} onPause={() => setPlaying(false)}
            onError={() => { pause(); markReady(false); setError("This source could not load. Return to playback or choose another option."); }} />
        </div>
        {!ready && !error && <div className={`${player.buffering} ${styles.sourceLoading}`} role="status">{sourceRatio ? "Seeking selected moment…" : "Loading source…"}</div>}
      </div>
    </div>
    <div className={player.transport} role="group" aria-label="Source playback controls">
      <div className={player.playbackButtons}>
        <button disabled={disabled || suspended || !sourceRatio} onClick={() => seek(draft.source_start)} aria-label="Restart selected moment"><EditorIcon name="start" /></button>
        <button className={player.play} disabled={disabled || suspended || !sourceRatio} aria-label={playing ? "Pause source" : "Play selected moment"} title="Play / pause (Space)" aria-keyshortcuts="Space" onClick={() => playing ? pause() : void play()}><EditorIcon name={playing ? "pause" : "play"} /></button>
      </div>
      <input type="range" aria-label="Selected moment playhead" min={0} max={duration} step={1 / 24} value={Math.min(duration, position)} disabled={disabled || suspended || !sourceRatio} onChange={(event) => seek(draft.source_start + Number(event.target.value))} />
      <span className={player.time}>{seconds(position)}<span>/ {seconds(duration)} · muted</span></span>
    </div>
    <div className={styles.adjustControls}>
      <div className={styles.controlHeading}><strong>Moment</strong><span>{seconds(draft.source_start)} – {seconds(draft.source_start + duration)} in source</span></div>
      <div ref={rail} className={styles.momentRail}>
        <div className={styles.momentWindow} style={{ left: `${(draft.source_start - range.start) / span * 100}%`, width: `${duration / span * 100}%` }}
          role="slider" tabIndex={controlsDisabled ? -1 : 0} aria-label="Selected moment start" aria-valuemin={bounds.sourceMin} aria-valuemax={bounds.sourceMax} aria-valuenow={draft.source_start} aria-valuetext={`${seconds(draft.source_start)}, ${seconds(duration)} long`} aria-disabled={controlsDisabled || bounds.sourceMax <= bounds.sourceMin} data-playback-space
          onPointerDown={(event) => startGesture(event, "moment")} onPointerMove={moveGesture} onPointerUp={endGesture} onPointerCancel={endGesture} onLostPointerCapture={() => { gesture.current = null; }}
          onKeyDown={(event) => {
            if (controlsDisabled) return;
            const step = (event.shiftKey ? 10 : 1) / 24;
            const value = event.key === "ArrowLeft" || event.key === "ArrowDown" ? draft.source_start - step : event.key === "ArrowRight" || event.key === "ArrowUp" ? draft.source_start + step : event.key === "Home" ? bounds.sourceMin : event.key === "End" ? bounds.sourceMax : null;
            if (value !== null) { event.preventDefault(); changeSource(value); }
          }}><span>{duration.toFixed(2)}s</span></div>
      </div>
      <div className={styles.railLabels}><span>{seconds(range.start)}</span><span>{props.readOnly ? "Source moment · read-only" : bounds.sourceMax > bounds.sourceMin ? "Drag the window · Arrow keys move one frame" : "This moment fills the offered shot"}</span><span>{seconds(range.end)}</span></div>
      <div className={styles.controlHeading}><strong>Crop &amp; zoom</strong><button disabled={controlsDisabled || !draft.crop} onClick={() => onChange({ ...draft, crop: null })}>Reset full frame</button></div>
      <label className={styles.zoom}>Zoom<input type="range" aria-label="Scene zoom" min={1} max={zoomLimit.current} step={0.01} value={zoom} disabled={controlsDisabled || !sourceRatio} onChange={(event) => sourceRatio && onChange({ ...draft, crop: nextSceneZoomCrop(Number(event.target.value), sourceRatio, outputRatio, draft.crop) })} /><output>{zoom.toFixed(2)}×</output></label>
      <p className={styles.help}>{props.readOnly ? "Framing is read-only." : canPan ? "Drag the picture to reposition. Arrow keys work when the picture is focused." : "Zoom in to reposition."} Black bars show the actual output framing.</p>
      {error && <p className={player.error} role="alert">{error}</p>}
    </div>
  </div>;
}
