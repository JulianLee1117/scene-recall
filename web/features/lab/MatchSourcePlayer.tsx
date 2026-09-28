"use client";

import { useEffect, useRef, useState } from "react";
import { labRequest, mediaUrl, seconds } from "@/lib/lab";
import type { Crop, LabClip } from "@/types/lab";
import { frameAt, frameContains, referenceAt, stepFrame, type MatchFrames } from "./matchFrames";
import EditorIcon from "./EditorIcon";
import styles from "./lab.module.css";
import cut from "./matchWorkspace.module.css";

export default function MatchSourcePlayer({ clip, disabled, timing, subjectPoint, subjectReady, onTiming, onSubjectPoint, onChange, loadFrames, maxHeight = 500 }: {
  clip: LabClip;
  disabled: boolean;
  timing: "nearby" | "fixed";
  subjectPoint: { x: number; y: number } | null;
  subjectReady: boolean;
  onTiming: (timing: "nearby" | "fixed") => void;
  onSubjectPoint: (point: { x: number; y: number } | null) => void;
  onChange: (patch: Partial<LabClip>) => void;
  loadFrames?: (unitId: string, time: number, signal?: AbortSignal) => Promise<MatchFrames>;
  maxHeight?: number;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [time, setTime] = useState(clip.reference_time ?? clip.source_start);
  const [frames, setFrames] = useState<MatchFrames | null>(null);
  const [playing, setPlaying] = useState(false);
  const [ready, setReady] = useState(false);
  const [ratio, setRatio] = useState(16 / 9);
  const [loadingFrames, setLoadingFrames] = useState(false);
  const [error, setError] = useState("");
  const [selectionMode, setSelectionMode] = useState<"subject" | "area" | null>(null);
  const [selection, setSelection] = useState<Crop | null>(null);
  const anchor = useRef<{ x: number; y: number } | null>(null);
  const sequence = useRef(0);
  const clipId = useRef(clip.id);
  clipId.current = clip.id;
  const start = frames?.t_start ?? clip.source_start;
  const end = frames?.t_end ?? clip.source_end;
  const reference = clip.reference_time ?? clip.source_start;
  const readonly = disabled || clip.locked;
  const fetchFrames = (unitId: string, at: number, signal?: AbortSignal) => loadFrames
    ? loadFrames(unitId, at, signal)
    : labRequest<MatchFrames>(`/matching/frames?unit_id=${encodeURIComponent(unitId)}&time=${at}`, { signal });

  useEffect(() => {
    const controller = new AbortController();
    setFrames(null);
    setReady(false);
    setError("");
    setSelectionMode(null);
    setTime(clip.reference_time ?? clip.source_start);
    if (clip.unit_id) {
      setLoadingFrames(true);
      fetchFrames(clip.unit_id, clip.reference_time ?? clip.source_start, controller.signal)
        .then(setFrames)
        .catch((reason) => { if (!controller.signal.aborted) setError(reason.message); })
        .finally(() => { if (!controller.signal.aborted) setLoadingFrames(false); });
    }
    return () => { controller.abort(); sequence.current++; };
  }, [clip.id, clip.unit_id]); // A timing edit keeps the player and its full-shot bounds intact.

  useEffect(() => {
    const player = video.current;
    if (!player || typeof player.requestVideoFrameCallback !== "function") return;
    let id = 0;
    const inspect: VideoFrameRequestCallback = (_now, metadata) => {
      if (!player.paused && metadata.mediaTime >= end - 0.001) {
        player.pause();
        player.currentTime = start;
      }
      id = player.requestVideoFrameCallback(inspect);
    };
    id = player.requestVideoFrameCallback(inspect);
    return () => player.cancelVideoFrameCallback(id);
  }, [clip.id, start, end]);

  async function mark(value: number, direction?: -1 | 1): Promise<boolean> {
    if (readonly || !clip.unit_id) return false;
    video.current?.pause();
    const request = ++sequence.current;
    const sourceId = clip.id;
    setLoadingFrames(true);
    setError("");
    try {
      const result = await fetchFrames(clip.unit_id, value);
      if (request !== sequence.current || clipId.current !== sourceId) return false;
      setFrames(result);
      const frame = direction ? stepFrame(result.frames, value, direction) : frameAt(result.frames, value);
      if (!frame || !video.current) return false;
      const player = video.current;
      const target = frame.time + Math.min(0.001, (frame.end - frame.time) / 4);
      if (Math.abs(player.currentTime - target) > 0.000001 || player.seeking) {
        await new Promise<void>((resolve, reject) => {
          const cleanup = () => { player.removeEventListener("seeked", completed); player.removeEventListener("error", failed); clearTimeout(timeout); };
          const completed = () => { cleanup(); resolve(); };
          const failed = () => { cleanup(); reject(new Error("The selected frame could not load. Try another moment.")); };
          const timeout = setTimeout(failed, 5000);
          player.addEventListener("seeked", completed, { once: true });
          player.addEventListener("error", failed, { once: true });
          player.currentTime = target;
        });
      }
      if (request !== sequence.current || clipId.current !== sourceId) return false;
      setTime(frame.time);
      const changedFrame = !frameContains(frame, clip.reference_time ?? clip.source_start);
      if (changedFrame) onSubjectPoint(null);
      onChange({ ...referenceAt(clip, frame.time, result), ...(changedFrame ? { region: null } : {}) });
      return true;
    } catch (reason) {
      if (request === sequence.current) setError(reason instanceof Error ? reason.message : "Could not read source frame timing.");
      return false;
    } finally {
      if (request === sequence.current) setLoadingFrames(false);
    }
  }

  async function beginSelection(mode: "subject" | "area") {
    const player = video.current;
    if (!player || loadingFrames) return;
    player.pause();
    // Prompts belong to the frame actually displayed after seeking, including
    // when playback moved beyond the previously saved matching moment.
    if (await mark(player.currentTime)) {
      setSelection(null);
      setSelectionMode(mode);
    }
  }

  function togglePlay() {
    const player = video.current;
    if (!player) return;
    if (!player.paused) player.pause();
    else {
      if (player.currentTime >= end - 0.001 || player.currentTime < start) player.currentTime = start;
      void player.play().catch(() => setError("This scene could not play. Check that its original film is available."));
    }
  }

  function point(event: React.PointerEvent<HTMLDivElement>) {
    const bounds = event.currentTarget.getBoundingClientRect();
    return { x: Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)), y: Math.max(0, Math.min(1, (event.clientY - bounds.top) / bounds.height)) };
  }
  const span = Math.max(0.001, end - start);
  const rangeStart = Math.max(start, reference - (timing === "nearby" ? 1 : 0));
  const rangeEnd = Math.min(end, reference + (timing === "nearby" ? 1 : 0));

  return (
    <section aria-label="Scene A source player" className={cut.source}>
      <div className={cut.sourceImage} style={{ aspectRatio: ratio, maxWidth: `${maxHeight * ratio}px` }}>
        <video ref={video} key={clip.id} src={mediaUrl(`/video/${encodeURIComponent(clip.film_id)}`)} muted playsInline preload="metadata" aria-label="Scene A" onClick={togglePlay}
          onLoadedMetadata={(event) => { event.currentTarget.currentTime = reference; setRatio(event.currentTarget.videoWidth / event.currentTarget.videoHeight || 16 / 9); setTime(reference); setReady(true); }}
          onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)}
          onTimeUpdate={(event) => {
            setTime(event.currentTarget.currentTime);
            if (!event.currentTarget.paused && event.currentTarget.currentTime >= end) { event.currentTarget.pause(); event.currentTarget.currentTime = start; }
          }}
          onError={() => setError("This scene could not load. Check that the original film is available.")} />
        {selectionMode && <div className={cut.selectionSurface} aria-label={selectionMode === "subject" ? "Click a subject" : "Draw an area"}
          onPointerDown={(event) => {
            if (readonly) return;
            event.currentTarget.setPointerCapture(event.pointerId);
            anchor.current = point(event);
            if (selectionMode === "subject") { onSubjectPoint(anchor.current); onChange({ region: null }); setSelectionMode(null); }
            else setSelection({ ...anchor.current, width: 0, height: 0 });
          }}
          onPointerMove={(event) => {
            if (!anchor.current || selectionMode !== "area") return;
            const next = point(event);
            setSelection({ x: Math.min(anchor.current.x, next.x), y: Math.min(anchor.current.y, next.y), width: Math.abs(anchor.current.x - next.x), height: Math.abs(anchor.current.y - next.y) });
          }}
          onPointerUp={() => { anchor.current = null; }}
          onPointerCancel={() => { anchor.current = null; setSelection(null); }} />}
        {subjectPoint && <span className={cut.subjectPoint} style={{ left: `${subjectPoint.x * 100}%`, top: `${subjectPoint.y * 100}%` }} aria-label="Selected subject" />}
        {(selectionMode === "area" ? selection : clip.region) && <div className={cut.area} style={(() => { const box = (selectionMode === "area" ? selection : clip.region)!; return { left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.width * 100}%`, height: `${box.height * 100}%` }; })()} />}
      </div>
      <div className={cut.transport}>
        <button disabled={!ready || loadingFrames || !!selectionMode} onClick={togglePlay} aria-label={playing ? "Pause scene A" : "Play scene A"}><EditorIcon name={playing ? "pause" : "play"} /></button>
        <button disabled={!ready || readonly || loadingFrames || !clip.unit_id || !!selectionMode} onClick={() => void mark(time, -1)} aria-label="Previous source frame"><EditorIcon name="previous" /></button>
        <button disabled={!ready || readonly || loadingFrames || !clip.unit_id || !!selectionMode} onClick={() => void mark(time, 1)} aria-label="Next source frame"><EditorIcon name="next" /></button>
        <div className={cut.scrubber}>
          <span className={cut.searchRange} style={{ left: `${(rangeStart - start) / span * 100}%`, width: `${Math.max(0.3, (rangeEnd - rangeStart) / span * 100)}%` }} />
          <input type="range" aria-label="Matching moment across source shot" min={start} max={Math.max(start, end - 0.001)} step="any" value={Math.max(start, Math.min(end - 0.001, time))} disabled={!ready || readonly || !!selectionMode}
            onChange={(event) => { const next = Number(event.target.value); video.current?.pause(); if (video.current) video.current.currentTime = next; setTime(next); }}
            onPointerUp={(event) => void mark(Number(event.currentTarget.value))}
            onKeyUp={(event) => { if (["ArrowLeft", "ArrowRight", "Home", "End", "PageUp", "PageDown"].includes(event.key)) void mark(Number(event.currentTarget.value)); }} />
        </div>
        <span>{seconds(time)}</span>
      </div>
      <div className={cut.sourceTools}>
        {selectionMode ? <>
          <span>{selectionMode === "subject" ? "Click the subject to follow." : "Drag around the detail to match."}</span>
          {selectionMode === "area" && <button className={styles.secondary} disabled={!selection || selection.width < 0.03 || selection.height < 0.03} onClick={() => { onChange({ region: selection }); onSubjectPoint(null); setSelectionMode(null); }}>Use area</button>}
          <button className={styles.ghost} onClick={() => { setSelectionMode(null); setSelection(null); }}>Cancel</button>
        </> : <>
          <button className={styles.secondary} aria-pressed={timing === "fixed"} disabled={readonly || !ready || loadingFrames} onClick={async () => { if (timing === "fixed") onTiming("nearby"); else if (await mark(video.current?.currentTime ?? time)) onTiming("fixed"); }}>{timing === "fixed" ? "Pinned · Allow nearby" : "Pin this frame"}</button>
          <span>{timing === "nearby" ? "Find a cut within ±1 second" : "Use this exact frame"}</span>
          <details className={cut.subjectMenu}>
            <summary>Subject focus{subjectPoint || clip.region ? " · selected" : " · automatic"}</summary>
            <button disabled={readonly || !ready || !subjectReady || loadingFrames} onClick={() => void beginSelection("subject")}>Choose a subject</button>
            <button disabled={readonly || !ready || loadingFrames} onClick={() => void beginSelection("area")}>Select an area</button>
            {(subjectPoint || clip.region) && <button disabled={readonly} onClick={() => { onSubjectPoint(null); onChange({ region: null }); }}>Reset focus</button>}
          </details>
        </>}
      </div>
      {error && <p className={styles.error} role="alert">{error}</p>}
    </section>
  );
}
