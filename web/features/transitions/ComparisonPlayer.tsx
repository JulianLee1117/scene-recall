"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { mediaUrl } from "@/lib/lab";
import { comparisonBounds, phaseToMedia, recipeTitle, seamCenter, speedTitle, type TransitionJob } from "./transitions";
import { waitForMedia } from "./transport";
import styles from "./transitions.module.css";

export interface AuditionAudio { url: string; name: string; seamTime: number; duration: number; volume: number; }

function VideoPane({ job, label, register, onReady, onWaiting, onFailure }: { job: TransitionJob; label: string;
  register: (id: string, node: HTMLVideoElement | null) => void; onReady: (id: string, align?: boolean) => void;
  onWaiting: () => void; onFailure: () => void;
}) {
  const ref = useRef<HTMLVideoElement>(null);
  const [presented, setPresented] = useState<number | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const node = ref.current; register(job.id, node);
    if (node && node.readyState >= 2) onReady(job.id, true);
    let callback = 0, alive = true;
    if (node && typeof node.requestVideoFrameCallback === "function") {
      const observe: VideoFrameRequestCallback = (_now, metadata) => {
        if (!alive) return;
        setPresented(Math.round(metadata.mediaTime * job.result!.fps));
        callback = node.requestVideoFrameCallback(observe);
      };
      callback = node.requestVideoFrameCallback(observe);
    }
    return () => { alive = false; if (callback) node?.cancelVideoFrameCallback(callback); node?.pause(); register(job.id, null); };
  }, [job.id, job.result, register, onReady]);
  return <div className={styles.renderPlayer}>
    <div className={styles.playerLabel}><span>{label}<small className={styles.playerSpeed}>{speedTitle(job.request.retime)}</small></span><small>{presented === null ? "Preview" : `Frame ${presented + 1}`}</small></div>
    <video ref={ref} src={mediaUrl(job.result!.preview_url)} muted playsInline preload="metadata"
      aria-label={`${label}: ${recipeTitle(job.request.recipe.id)}`} onLoadedData={() => onReady(job.id, true)}
      onSeeked={() => onReady(job.id)} onCanPlay={() => onReady(job.id)}
      onWaiting={onWaiting} onStalled={onWaiting}
      onError={() => { onFailure(); setError("This render could not be loaded. Check that the API is running."); }} />
    {error && <p role="alert" className={styles.error}>{error}</p>}
  </div>;
}

/** Both variants follow one clock. Each clamps to its own first/last output frame. */
export default function ComparisonPlayer({ job, compare, audio, onAudioChange, active = true, hideScrubber = false, externalSeek, onTimeChange }: {
  job: TransitionJob; compare?: TransitionJob; audio: AuditionAudio | null;
  onAudioChange: (audio: AuditionAudio | null) => void; active?: boolean;
  hideScrubber?: boolean;
  externalSeek?: { time: number; token: number; jobId: string };
  onTimeChange?: (seconds: number) => void;
}) {
  const videos = useRef(new Map<string, HTMLVideoElement>());
  const music = useRef<HTMLAudioElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [ready, setReady] = useState<Record<string, boolean>>({});
  const [phase, setPhase] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [buffering, setBuffering] = useState(false);
  const [speed, setSpeed] = useState(1);
  const [loopSeam, setLoopSeam] = useState(true);
  const [error, setError] = useState("");
  const timeCallback = useRef(onTimeChange); timeCallback.current = onTimeChange;
  const appliedSeek = useRef<{ jobId: string; token: number } | null>(null);
  const phaseRef = useRef(0);
  const animation = useRef(0);
  const running = useRef(false);
  const operation = useRef(0);
  const mediaWait = useRef<AbortController | null>(null);
  const bufferPending = useRef(false);
  const context = useRef({ jobs: [job, ...(compare ? [compare] : [])], speed, loopSeam, audio });
  context.current = { jobs: [job, ...(compare ? [compare] : [])], speed, loopSeam, audio };
  const jobs = context.current.jobs;
  const bounds = comparisonBounds(jobs, loopSeam);
  const register = useCallback((id: string, node: HTMLVideoElement | null) => {
    if (node) videos.current.set(id, node); else videos.current.delete(id);
    setReady((current) => { const next = { ...current }; if (node) next[id] = node.readyState >= 2; else delete next[id]; return next; });
  }, []);
  const registerMusic = useCallback((node: HTMLAudioElement | null) => {
    if (music.current !== node) music.current?.pause();
    music.current = node;
  }, []);
  const onReady = useCallback((id: string, align = false) => {
    const node = videos.current.get(id), candidate = context.current.jobs.find((item) => item.id === id);
    if (!node || !candidate) return;
    const target = phaseToMedia(candidate, phaseRef.current) + .00001;
    if (align && !running.current && Math.abs(node.currentTime - target) > .0001) node.currentTime = target;
    setReady((current) => ({ ...current, [id]: node.readyState >= 2 }));
  }, []);

  const pause = useCallback(() => {
    operation.current++; running.current = false; cancelAnimationFrame(animation.current);
    mediaWait.current?.abort(); mediaWait.current = null; bufferPending.current = false; setBuffering(false);
    videos.current.forEach((node) => node.pause()); music.current?.pause(); setPlaying(false);
  }, []);

  function playMedia(node: HTMLMediaElement, message: string) {
    const token = operation.current;
    void node.play().catch(() => {
      // Pausing, changing files, seeking and changing tabs intentionally abort pending play promises.
      if (running.current && token === operation.current) { pause(); setError(message); }
    });
  }

  function synchronize(next: number, play: boolean, force = false) {
    const { jobs: currentJobs, speed: rate, audio: currentAudio } = context.current;
    for (const candidate of currentJobs) {
      const node = videos.current.get(candidate.id); if (!node || node.readyState === 0) continue;
      const target = phaseToMedia(candidate, next);
      const rawTime = next + seamCenter(candidate);
      const inside = rawTime >= 0 && rawTime < candidate.result!.duration - 1 / candidate.result!.fps;
      if (force || Math.abs(node.currentTime - target) > .075) node.currentTime = target + .00001;
      node.playbackRate = rate;
      if (play && inside) { if (node.paused) playMedia(node, "Playback could not start. Try Play again after the videos load."); }
      else node.pause();
    }
    const sound = music.current;
    if (sound && currentAudio && sound.readyState > 0) {
      const target = currentAudio.seamTime + next;
      if (target >= 0 && target < currentAudio.duration) {
        if (force || Math.abs(sound.currentTime - target) > .075) sound.currentTime = target;
        sound.playbackRate = rate; sound.volume = currentAudio.volume;
        if (play && sound.paused) playMedia(sound, "Music could not play. Try another audio file or press Play again.");
        else if (!play) sound.pause();
      } else { sound.pause(); sound.currentTime = Math.max(0, Math.min(Math.max(0, currentAudio.duration - .001), target)); }
    }
  }
  function seek(next: number, fullPair = false) {
    pause(); const currentBounds = comparisonBounds(context.current.jobs, fullPair ? false : context.current.loopSeam);
    phaseRef.current = Math.max(currentBounds.start, Math.min(currentBounds.end, next));
    setPhase(phaseRef.current); synchronize(phaseRef.current, false, true);
  }
  function requiredMedia(next: number): { node: HTMLMediaElement; minimum: number }[] {
    const media = context.current.jobs.flatMap((candidate) => {
      const node = videos.current.get(candidate.id);
      const raw = next + seamCenter(candidate);
      return node ? [{ node: node as HTMLMediaElement, minimum: raw >= 0 && raw < candidate.result!.duration - 1 / candidate.result!.fps ? 3 : 2 }] : [];
    });
    const track = context.current.audio;
    if (music.current && track) media.push({ node: music.current, minimum: track.seamTime + next >= 0 && track.seamTime + next < track.duration ? 3 : 2 });
    return media;
  }
  function holdForBuffering() {
    if (!running.current || bufferPending.current || !requiredMedia(phaseRef.current).some(({ node, minimum }) => node.seeking || node.readyState < minimum)) return;
    cancelAnimationFrame(animation.current);
    void begin(phaseRef.current);
  }
  async function begin(starting: number) {
      const token = ++operation.current;
      mediaWait.current?.abort(); const abort = new AbortController(); mediaWait.current = abort;
      bufferPending.current = true; setBuffering(true);
      synchronize(starting, false, true);
      try {
        await Promise.all(requiredMedia(starting).map(({ node, minimum }) => waitForMedia(node, 10000, abort.signal, minimum)));
        if (!running.current || token !== operation.current) return;
        bufferPending.current = false; setBuffering(false); mediaWait.current = null;
        synchronize(starting, true);
        const origin = performance.now(); let lastUpdate = origin;
        function tick(now: number) {
          if (!running.current || token !== operation.current) return;
          if (requiredMedia(phaseRef.current).some(({ node, minimum }) => node.seeking || node.readyState < minimum)) { void begin(phaseRef.current); return; }
          const current = context.current, limits = comparisonBounds(current.jobs, current.loopSeam);
          const next = starting + (now - origin) / 1000 * current.speed;
          if (next >= limits.end) { phaseRef.current = limits.start; setPhase(limits.start); void begin(limits.start); return; }
          synchronize(next, true); phaseRef.current = next;
          if (now - lastUpdate > 70) { setPhase(next); lastUpdate = now; }
          animation.current = requestAnimationFrame(tick);
        }
        animation.current = requestAnimationFrame(tick);
      } catch (reason) { if (token === operation.current) { pause(); setError(reason instanceof Error ? reason.message : "The preview could not play."); } }
  }
  function play() {
    if (!active) return;
    if (running.current) { pause(); return; }
    setError("");
    const currentBounds = comparisonBounds(context.current.jobs, context.current.loopSeam);
    if (phaseRef.current >= currentBounds.end - .01 || phaseRef.current < currentBounds.start) phaseRef.current = currentBounds.start;
    running.current = true; setPlaying(true);
    void begin(phaseRef.current);
  }
  function step(direction: number) {
    const current = phaseToMedia(job, phaseRef.current);
    seek((Math.round(current * job.result!.fps) + direction) / job.result!.fps - seamCenter(job));
  }
  useEffect(() => {
    pause(); const limits = comparisonBounds(context.current.jobs, context.current.loopSeam);
    phaseRef.current = limits.start; setPhase(limits.start); setError("");
    videos.current.forEach((node, id) => { const candidate = context.current.jobs.find((item) => item.id === id); if (candidate && node.readyState > 0) node.currentTime = phaseToMedia(candidate, limits.start); });
  }, [job.id, compare?.id, pause]);
  useEffect(() => { if (!active) pause(); }, [active, pause]);
  useEffect(() => { if (active) timeCallback.current?.(phaseToMedia(job, phase)); }, [phase, active, job.id]);
  useEffect(() => {
    if (externalSeek?.jobId !== job.id || !active || !Number.isFinite(externalSeek?.time)) return;
    if (appliedSeek.current?.jobId === externalSeek.jobId && appliedSeek.current.token === externalSeek.token) return;
    appliedSeek.current = { jobId: externalSeek.jobId, token: externalSeek.token };
    const target = externalSeek!.time - seamCenter(job);
    const limits = comparisonBounds(context.current.jobs, context.current.loopSeam);
    if (target < limits.start || target > limits.end) setLoopSeam(false);
    seek(target, true);
  }, [externalSeek?.token, externalSeek?.jobId, active, job.id]);
  useEffect(() => {
    const hidden = () => { if (document.hidden) pause(); };
    document.addEventListener("visibilitychange", hidden);
    return () => { document.removeEventListener("visibilitychange", hidden); operation.current++; running.current = false; mediaWait.current?.abort(); cancelAnimationFrame(animation.current); videos.current.forEach((node) => node.pause()); music.current?.pause(); };
  }, [pause]);
  const allReady = jobs.every((item) => ready[item.id]);
  const percent = bounds.end > bounds.start ? 100 * (phase - bounds.start) / (bounds.end - bounds.start) : 0;
  return <div className={styles.comparison} tabIndex={0} aria-label="Transition playback. Space to play or pause; left and right arrows to step."
    onKeyDown={(event) => {
      if ((event.target as HTMLElement).closest("input,select,textarea,button,a") || event.altKey || event.ctrlKey || event.metaKey) return;
      if (event.code === "Space") { event.preventDefault(); if (allReady) play(); }
      else if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); step(event.key === "ArrowLeft" ? -1 : 1); }
    }}>
    <div className={compare ? styles.comparePlayers : ""}>
      <VideoPane key={job.id} job={job} label={compare ? "A · Selected variant" : recipeTitle(job.request.recipe.id)} register={register} onReady={onReady} onWaiting={holdForBuffering} onFailure={pause} />
      {compare && <VideoPane key={compare.id} job={compare} label={`B · ${recipeTitle(compare.request.recipe.id)}`} register={register} onReady={onReady} onWaiting={holdForBuffering} onFailure={pause} />}
    </div>
    {(buffering || !allReady) && <p className={styles.playbackStatus} role="status">{buffering ? "Buffering… previews and music are held together." : "Loading preview…"}</p>}
    {!hideScrubber && <div className={styles.phaseTimeline}>
      <div className={styles.phaseLabels}><span>{bounds.start.toFixed(2)}s</span><button type="button" onClick={() => seek(0)}>Transition center</button><span>+{bounds.end.toFixed(2)}s</span></div>
      <div className={styles.phaseTrack}><i style={{ left: `${100 * -bounds.start / (bounds.end - bounds.start || 1)}%` }} /><span style={{ width: `${Math.max(0, Math.min(100, percent))}%` }} />
        <input aria-label="Shared seam-relative playhead" type="range" min={bounds.start} max={bounds.end} step={1 / job.result!.fps} value={Math.max(bounds.start, Math.min(bounds.end, phase))} onChange={(event) => seek(Number(event.target.value))} />
      </div>
    </div>}
    <div className={styles.playTools}>
      <button className={styles.transportPlay} type="button" disabled={!allReady || !active} onClick={play}>{playing ? "Pause" : "Play"}</button>
      <button type="button" aria-label="Previous output frame" disabled={!allReady} onClick={() => step(-1)}>‹</button><button type="button" aria-label="Next output frame" disabled={!allReady} onClick={() => step(1)}>›</button>
      <span>{phase >= 0 ? "+" : ""}{phase.toFixed(2)}s</span>
      <select aria-label="Shared playback speed" title="Preview playback only; does not change rendered source speed" value={speed} onChange={(event) => { pause(); setSpeed(Number(event.target.value)); }}><option value="1">Preview 1×</option><option value="0.5">Preview 0.5×</option><option value="0.25">Preview 0.25×</option></select>
      <button type="button" aria-pressed={loopSeam} onClick={() => { const limits = comparisonBounds(jobs, !loopSeam); setLoopSeam(!loopSeam); seek(Math.max(limits.start, Math.min(limits.end, phaseRef.current))); }}>Loop seam</button>
    </div>
    {compare && <p className={styles.hint}>One transport, aligned at each transition’s center. Shorter previews hold their boundary frames. {audio ? "One music track follows both." : "Add music below to hear the timing."}</p>}
    {error && <p role="alert" className={styles.error}>{error}</p>}
    <details className={styles.audioAudition}>
      <summary>Audition with music <span>{audio ? audio.name : "Local audio"}</span></summary>
      <input ref={input} type="file" hidden accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg" onChange={(event) => { const file = event.target.files?.[0]; if (!file) return; pause(); setError(""); onAudioChange({ url: URL.createObjectURL(file), name: file.name, duration: 0, seamTime: 0, volume: .7 }); event.target.value = ""; }} />
      <div className={styles.audioActions}><button className={styles.secondary} type="button" onClick={() => input.current?.click()}>{audio ? "Change music" : "Choose audio"}</button>{audio && <button className={styles.textButton} type="button" onClick={() => { pause(); setError(""); onAudioChange(null); }}>Remove</button>}</div>
      {audio && <><audio key={audio.url} ref={registerMusic} src={audio.url} preload="metadata" onWaiting={holdForBuffering} onStalled={holdForBuffering} onLoadedMetadata={(event) => { const current = context.current.audio, duration = event.currentTarget.duration; if (event.currentTarget === music.current && current && current.url === audio.url && Number.isFinite(duration)) onAudioChange({ ...current, duration, seamTime: Math.min(current.seamTime, duration) }); }} onError={(event) => { if (event.currentTarget === music.current) { pause(); setError("This audio format could not be played. Try an MP3 or WAV file."); } }} />
        <div className={styles.audioSettings}><label>Music time at center<input aria-label="Music timestamp at transition center" type="number" min="0" max={audio.duration || undefined} step="0.01" value={audio.seamTime} onChange={(event) => { const value = event.target.valueAsNumber; if (Number.isFinite(value)) { pause(); onAudioChange({ ...audio, seamTime: Math.max(0, Math.min(audio.duration, value)) }); } }} /><small>seconds</small></label><label>Volume<input aria-label="Audition music volume" type="range" min="0" max="1" step="0.01" value={audio.volume} onChange={(event) => onAudioChange({ ...audio, volume: Number(event.target.value) })} /></label></div>
      </>}
      <p className={styles.hint}>Music is played from your computer. It is not uploaded or included in downloaded video.</p>
    </details>
  </div>;
}
