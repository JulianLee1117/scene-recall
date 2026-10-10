"use client";

import { useEffect, useRef, useState } from "react";
import { mediaUrl, seconds } from "@/lib/lab";
import { isPlaybackSpace } from "@/lib/playbackShortcut";
import EditorIcon from "@/features/lab/EditorIcon";
import type { NextSceneAuditionSelection } from "./NextScenePanel";
import playerStyles from "./sequencePlayer.module.css";
import styles from "./nextSceneAudition.module.css";

type Props = {
  selection: NextSceneAuditionSelection;
  suspended: boolean;
  onFailure: () => void;
  onTimeChange: (time: number) => void;
};

/** One rendered candidate includes both sources and the correctly offset song. */
export default function NextScenePairPlayer({ selection, suspended, onFailure, onTimeChange }: Props) {
  const video = useRef<HTMLVideoElement>(null);
  const alive = useRef(true);
  const playEpoch = useRef(0);
  const needsAutoplay = useRef(true);
  const latest = useRef({ suspended, onTimeChange });
  latest.current = { suspended, onTimeChange };
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(selection.scope.t2 - selection.scope.t0);
  const [buffering, setBuffering] = useState(true);
  const [muted, setMuted] = useState(false);
  const [error, setError] = useState("");

  function pause() { playEpoch.current += 1; video.current?.pause(); }

  async function play() {
    const node = video.current;
    if (!node || latest.current.suspended) return;
    setError("");
    if (node.ended || node.currentTime >= node.duration - 0.01) node.currentTime = 0;
    const epoch = ++playEpoch.current;
    try {
      await node.play();
      if (!alive.current || latest.current.suspended || epoch !== playEpoch.current) node.pause();
    }
    catch (reason) {
      if (!alive.current || epoch !== playEpoch.current) return;
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setError("Press Play to start this preview. If it cannot load, prepare the preview again.");
    }
  }

  useEffect(() => {
    alive.current = true;
    const node = video.current;
    if (!node) return;
    const keyboard = (event: KeyboardEvent) => {
      if (latest.current.suspended || document.querySelector("dialog[open]") || !isPlaybackSpace(event)) return;
      event.preventDefault();
      if (!event.repeat) {
        if (node.paused) void play();
        else pause();
      }
    };
    const visibility = () => { if (document.hidden) pause(); };
    window.addEventListener("keydown", keyboard);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      alive.current = false;
      playEpoch.current += 1;
      node.pause();
      window.removeEventListener("keydown", keyboard);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, []);
  useEffect(() => {
    const node = video.current;
    if (!node) return;
    node.currentTime = 0;
    setPosition(0);
    setError("");
    setBuffering(true);
    latest.current.onTimeChange(selection.scope.t0);
    node.parentElement?.focus({ preventScroll: true });
    needsAutoplay.current = true;
  }, [selection.candidate.preview_url]);
  useEffect(() => {
    if (suspended) pause();
    else if (needsAutoplay.current) { needsAutoplay.current = false; void play(); }
  }, [suspended, selection.candidate.preview_url]);

  function seek(time: number) {
    const node = video.current;
    if (!node || suspended) return;
    node.currentTime = Math.max(0, Math.min(duration, time));
    setPosition(node.currentTime);
    latest.current.onTimeChange(selection.scope.t0 + node.currentTime);
  }


  return (
    <>
      <div className={styles.monitor} tabIndex={-1} data-playback-space>
        <video
          ref={video}
          src={selection.candidate.preview_url ? mediaUrl(selection.candidate.preview_url) : undefined}
          playsInline muted={muted} preload="auto"
          aria-label="Anchor and next scene with music"
          onLoadedMetadata={() => {
            if (video.current && Number.isFinite(video.current.duration)) setDuration(video.current.duration);
          }}
          onTimeUpdate={(event) => {
            setPosition(event.currentTarget.currentTime);
            latest.current.onTimeChange(selection.scope.t0 + event.currentTarget.currentTime);
          }}
          onPlaying={() => { setPlaying(true); setBuffering(false); }}
          onPause={() => setPlaying(false)}
          onCanPlay={() => setBuffering(false)}
          onWaiting={() => setBuffering(true)}
          onEnded={() => { setPlaying(false); setBuffering(false); }}
          onError={() => { setPlaying(false); setBuffering(false); onFailure(); setError("This preview could not load. Rebuild preview to render it again."); }}
        />
        {buffering && <div className={playerStyles.buffering} role="status"><i aria-hidden="true" /> Loading preview…</div>}
      </div>
      <div className={playerStyles.transport} role="group" aria-label="Alternative playback controls">
        <div className={playerStyles.playbackButtons}>
          <button disabled={suspended} onClick={() => seek(0)} aria-label="Restart alternative" title="Restart alternative"><EditorIcon name="start" /></button>
          <button
            className={playerStyles.play} disabled={suspended}
            aria-label={playing ? "Pause alternative" : "Play alternative"}
            aria-keyshortcuts="Space" title="Play / pause (Space)"
            onClick={() => playing ? pause() : void play()}
          ><EditorIcon name={playing ? "pause" : "play"} size={18} /></button>
        </div>
        <input type="range" aria-label="Alternative playhead" min={0} max={duration} step={1 / 24} value={Math.min(duration, position)} disabled={suspended} onChange={(event) => seek(Number(event.target.value))} />
        <span className={playerStyles.time}>{seconds(position)}<span>/ {seconds(duration)}</span></span>
        <button className={playerStyles.mute} disabled={suspended} aria-label={muted ? "Unmute alternative" : "Mute alternative"} aria-pressed={muted} onClick={() => setMuted((value) => !value)}><EditorIcon name={muted ? "muted" : "volume"} /></button>
      </div>
      {error && <p className={playerStyles.error} role="alert">{error}</p>}
    </>
  );
}
