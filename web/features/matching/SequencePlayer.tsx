"use client";

import { useEffect, useRef, useState } from "react";
import { seconds } from "@/lib/lab";
import { frameAspect, OUTPUT_ASPECT, placement, playbackUrl, type ContentBox, type Crop, type OutputFormat } from "@/lib/matchCuts";
import MatchIcon from "./MatchIcon";
import styles from "./matchCuts.module.css";

export interface Segment {
  key: string;
  filmId: string;
  start: number;
  end: number;
  crop: Crop | null;
  aspect: number;              // content picture aspect
  contentBox: ContentBox;
  label?: string;
}

const sources = new Map<string, Promise<string>>();
function sourceFor(filmId: string): Promise<string> {
  if (!sources.has(filmId)) {
    sources.set(filmId, playbackUrl(filmId).catch((reason) => { sources.delete(filmId); throw reason; }));
  }
  return sources.get(filmId)!;
}

/** Seek a video element to a source instant (loading its film first when needed). */
function seek(video: HTMLVideoElement, src: string, time: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      video.removeEventListener("seeked", done);
      video.removeEventListener("error", fail);
      video.removeEventListener("loadedmetadata", loaded);
    };
    const done = () => { cleanup(); resolve(); };
    const fail = () => { cleanup(); reject(new Error("This film could not be played in the browser.")); };
    const loaded = () => { video.currentTime = time; };
    video.addEventListener("seeked", done);
    video.addEventListener("error", fail);
    if (video.getAttribute("src") !== src) {
      video.addEventListener("loadedmetadata", loaded);
      video.src = src;
      video.load();
    } else if (video.readyState >= 2 && Math.abs(video.currentTime - time) < 0.005) {
      cleanup();
      resolve();
    } else {
      video.currentTime = time;
    }
  });
}

type FrameCallback = (now: number, metadata: { mediaTime: number }) => void;
type FrameVideo = HTMLVideoElement & { requestVideoFrameCallback?: (callback: FrameCallback) => number };

/**
 * Plays source windows back to back in the browser, each through its crop, by
 * alternating two video elements: while one plays, the other is already
 * seeked to the next window's first frame, so the cut lands on time.
 */
export default function SequencePlayer({ segments, output, loop = true, autoPlay = true, compact = false }: {
  segments: Segment[];
  output: OutputFormat;
  loop?: boolean;
  autoPlay?: boolean;
  compact?: boolean;
}) {
  const videos = [useRef<FrameVideo>(null), useRef<FrameVideo>(null)];
  const generation = useRef(0);
  const [assigned, setAssigned] = useState<[number, number]>([0, 1]);
  const [visible, setVisible] = useState(0);
  const [current, setCurrent] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [muted, setMuted] = useState(true);
  const [looping, setLooping] = useState(loop);
  const [error, setError] = useState("");
  const loopRef = useRef(looping);
  loopRef.current = looping;
  const key = segments.map((segment) => `${segment.key}:${segment.start.toFixed(3)}:${segment.end.toFixed(3)}:${segment.crop?.join(",")}`).join("|");
  const outputAspect = OUTPUT_ASPECT[output];

  async function prepare(index: number, slot: number): Promise<void> {
    const segment = segments[index];
    const video = videos[slot].current;
    if (!segment || !video) return;
    setAssigned((previous) => { const next: [number, number] = [...previous]; next[slot] = index; return next; });
    await seek(video, await sourceFor(segment.filmId), segment.start);
  }

  function stop() {
    generation.current++;
    videos.forEach((ref) => ref.current?.pause());
    setPlaying(false);
  }

  async function play() {
    const run = ++generation.current;
    setError("");
    if (!segments.length) return;
    setPlaying(true);
    const ready: Promise<void>[] = [];
    try {
      ready[0] = prepare(0, 0);
      if (segments.length > 1) ready[1] = prepare(1, 1);
      await ready[0];
    } catch (reason) {
      if (run === generation.current) { setError((reason as Error).message); setPlaying(false); }
      return;
    }
    if (run !== generation.current) return;
    const start = async (index: number) => {
      const slot = index % 2;
      const video = videos[slot].current;
      if (!video) return;
      setVisible(slot);
      setCurrent(index);
      video.muted = muted;
      await video.play().catch(() => undefined);
      videos[1 - slot].current?.pause();
      const segment = segments[index];
      const watch = () => {
        if (run !== generation.current) return;
        const onFrame: FrameCallback = (_now, metadata) => {
          if (run !== generation.current) return;
          const time = metadata?.mediaTime ?? video.currentTime;
          if (time >= segment.end - 1 / 60 || video.ended) void advance(index);
          else watch();
        };
        if (video.requestVideoFrameCallback) video.requestVideoFrameCallback(onFrame);
        else requestAnimationFrame(() => onFrame(0, { mediaTime: video.currentTime }));
      };
      watch();
    };
    const advance = async (index: number) => {
      const next = index + 1;
      if (next >= segments.length) {
        videos[index % 2].current?.pause();
        if (loopRef.current) window.setTimeout(() => { if (run === generation.current) void play(); }, 450);
        else setPlaying(false);
        return;
      }
      try {
        await (ready[next] ?? prepare(next, next % 2));
      } catch (reason) {
        if (run === generation.current) { setError((reason as Error).message); setPlaying(false); }
        return;
      }
      if (run !== generation.current) return;
      await start(next);
      if (next + 1 < segments.length) ready[next + 1] = prepare(next + 1, (next + 1) % 2).catch(() => undefined);
    };
    await start(0);
  }

  useEffect(() => {
    if (autoPlay) void play();
    else void prepare(0, 0).catch((reason) => setError((reason as Error).message));
    return () => { generation.current++; videos.forEach((ref) => ref.current?.pause()); };
  }, [key]); // A new sequence restarts playback.

  useEffect(() => { videos.forEach((ref) => { if (ref.current) ref.current.muted = muted; }); }, [muted]);

  const segment = segments[current];
  return <div className={`${styles.player} ${compact ? styles.playerCompact : ""}`}>
    <div className={styles.stage} data-output={output} style={{ aspectRatio: String(outputAspect) }} onClick={() => playing ? stop() : void play()}>
      {[0, 1].map((slot) => {
        const shown = segments[assigned[slot]];
        const place = shown ? placement(shown.crop, frameAspect(shown.aspect, shown.contentBox), shown.contentBox, outputAspect) : null;
        return <video key={slot} ref={videos[slot]} muted playsInline preload="auto" aria-hidden={visible !== slot}
          className={visible === slot ? styles.stageVisible : styles.stageHidden}
          style={place ? { left: `${place.left}%`, top: `${place.top}%`, width: `${place.width}%`, height: `${place.height}%` } : undefined} />;
      })}
      {!playing && <span className={styles.stagePlay}><MatchIcon name="play" /></span>}
      {error && <p className={styles.stageError} role="alert">{error}</p>}
    </div>
    <div className={styles.transport}>
      <button type="button" className={styles.iconButton} onClick={() => playing ? stop() : void play()} aria-label={playing ? "Pause" : "Play"}>
        {playing ? <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><rect x="4" y="3" width="3" height="10" rx="0.5" /><rect x="9" y="3" width="3" height="10" rx="0.5" /></svg> : <MatchIcon name="play" />}
      </button>
      <span className={styles.transportLabel}>{segment ? <>{segment.label ?? `Clip ${current + 1}`} <time>{seconds(segment.start)}</time></> : null}</span>
      <span className={styles.transportCount}>{segments.length > 1 ? `${current + 1}/${segments.length}` : ""}</span>
      <label className={styles.toggle}><input type="checkbox" checked={looping} onChange={(event) => setLooping(event.target.checked)} /> Loop</label>
      <label className={styles.toggle}><input type="checkbox" checked={!muted} onChange={(event) => setMuted(!event.target.checked)} /> Sound</label>
    </div>
  </div>;
}
