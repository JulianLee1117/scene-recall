"use client";

import { useEffect, useRef, useState, type CSSProperties, type PointerEvent, type KeyboardEvent } from "react";
import { LAB_API, mediaUrl, seconds } from "@/lib/lab";
import { DEFAULT_FRAMING, sourceBoundary, sourceInspectionTime, sourceScrubBounds, type SourceEndpoint, type SourceFraming, type SourceSelection, type TransitionRequest } from "./transitions";
import { framingDrag, trimAtPointer, trimLimits } from "./source-interaction";
import styles from "./transitions.module.css";
import controls from "./source-controls.module.css";

export default function SourceCard({ label, source, endpoint, disabled, aspect = "landscape", onChoose, onChange, monitor = false, inspection, onPositionChange, onDurationKnown }: {
  label: "A" | "B"; source: SourceSelection | null; disabled: boolean;
  endpoint?: SourceEndpoint;
  aspect?: TransitionRequest["output"]["aspect"];
  onChoose: () => void; onChange: (source: SourceSelection) => void;
  monitor?: boolean;
  inspection?: { film: string; time: number; token: number };
  onPositionChange?: (time: number) => void;
  onDurationKnown?: (duration: number) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [error, setError] = useState("");
  const [playing, setPlaying] = useState(false);
  const [loadingSource, setLoadingSource] = useState(false);
  const [requestedFilm, setRequestedFilm] = useState<string | null>(null);
  const [position, setPosition] = useState(0);
  const [filmDuration, setFilmDuration] = useState(Infinity);
  const [showEndpoint, setShowEndpoint] = useState(!!endpoint);
  const [endpointFailed, setEndpointFailed] = useState<string | null>(null);
  const playOperation = useRef(0);
  const inspectionPosition = useRef(true);
  const pendingSeek = useRef<{ film: string; time: number; inspection: boolean } | null>(null);
  const pendingPlay = useRef<{ film: string; time: number; token: number } | null>(null);
  const appliedInspection = useRef<{ film: string; token: number } | null>(null);
  const [playback, setPlayback] = useState<{ film: string; url: string } | null>(null);
  const [reframing, setReframing] = useState(false);
  const [picture, setPicture] = useState<{ film: string; width: number; height: number } | null>(null);
  const [dragBounds, setDragBounds] = useState<{ start: number; end: number } | null>(null);
  const trimRail = useRef<HTMLDivElement>(null);
  const trimDrag = useRef<{ pointer: number; film: string; edge: "source_start" | "source_end"; source: SourceSelection; x: number; width: number; start: number; end: number } | null>(null);
  const cropDrag = useRef<{ pointer: number; film: string; x: number; y: number; width: number; height: number; picture: { width: number; height: number }; framing: SourceFraming } | null>(null);
  const trimInspection = useRef<{ film: string; start: number; end: number; time: number } | null>(null);
  const framing = source?.framing ?? DEFAULT_FRAMING;
  const ratio = aspect === "portrait" ? 9 / 16 : aspect === "square" ? 1 : 16 / 9;
  const cutBoundary = label === "A" ? "source_end" : "source_start";
  const cutTime = source ? sourceInspectionTime(source, label === "A") : 0;
  const scrub = dragBounds ?? (source ? sourceScrubBounds(source, filmDuration) : { start: 0, end: 1 });
  const playable = !!source && playback?.film === source.film_id && Number.isFinite(source.source_start) && Number.isFinite(source.source_end) && source.source_end > source.source_start;
  const validEndpoint = !!source && !!endpoint && Number.isFinite(endpoint.time) && endpoint.time >= source.source_start - 1e-6 && endpoint.time < source.source_end + 1e-6;
  const still = validEndpoint && showEndpoint && endpointFailed !== endpoint!.url;
  const attachMedia = !!source && (requestedFilm === source.film_id || !still);
  const displayedTime = still ? endpoint!.time : position;
  const pictureStyle: CSSProperties = { objectFit: framing.fit === "fill" ? "cover" : "contain", objectPosition: `${framing.anchor_x * 100}% ${framing.anchor_y * 100}%`, transform: `scale(${framing.zoom})`, transformOrigin: `${framing.anchor_x * 100}% ${framing.anchor_y * 100}%` };
  const positionCallback = useRef(onPositionChange); positionCallback.current = onPositionChange;
  useEffect(() => { if (!disabled && Number.isFinite(displayedTime)) positionCallback.current?.(displayedTime); }, [displayedTime, disabled, source?.film_id]);
  const percent = (time: number) => Number.isFinite(time) ? Math.max(0, Math.min(100, (time - scrub.start) / (scrub.end - scrub.start) * 100)) : 0;
  function trim(edge: "source_start" | "source_end", time: number, original = source) {
    if (disabled || !source || !original || original.film_id !== source.film_id) return;
    const target = trimAtPointer(original, edge, time, filmDuration);
    if (target === null) return;
    const next = { ...source, [edge]: target };
    const inspect = edge === "source_start" ? target : Math.max(next.source_start, target - 1 / 30);
    trimInspection.current = { film: source.film_id, start: next.source_start, end: next.source_end, time: inspect };
    seek(inspect, true, true);
    onChange(next);
  }
  function startTrim(event: PointerEvent<HTMLButtonElement>, edge: "source_start" | "source_end") {
    if (disabled || !source || event.button !== 0 || !trimRail.current) return;
    event.preventDefault(); event.currentTarget.focus();
    const rect = trimRail.current.getBoundingClientRect();
    trimDrag.current = { pointer: event.pointerId, film: source.film_id, edge, source, x: event.clientX, width: rect.width, ...scrub };
    setDragBounds(scrub); event.currentTarget.setPointerCapture(event.pointerId);
  }
  function moveTrim(event: PointerEvent<HTMLButtonElement>) {
    const drag = trimDrag.current;
    if (!drag || drag.pointer !== event.pointerId || drag.film !== source?.film_id || disabled || drag.width <= 0) return;
    trim(drag.edge, drag.source[drag.edge] + (event.clientX - drag.x) / drag.width * (drag.end - drag.start), drag.source);
  }
  function endTrim(event: PointerEvent<HTMLButtonElement>) {
    if (trimDrag.current?.pointer !== event.pointerId) return;
    trimDrag.current = null; setDragBounds(null);
  }
  function trimKey(event: KeyboardEvent<HTMLButtonElement>, edge: "source_start" | "source_end") {
    if (!source || disabled) return;
    const delta = event.key === "ArrowLeft" || event.key === "ArrowDown" ? -1 : event.key === "ArrowRight" || event.key === "ArrowUp" ? 1 : 0;
    if (delta) { event.preventDefault(); trim(edge, source[edge] + delta * (event.shiftKey ? .1 : 1 / 30)); }
  }
  function pauseForFraming() {
    playOperation.current++; pendingPlay.current = null;
    setLoadingSource(false); setPlaying(false); video.current?.pause();
  }
  function startCrop(event: PointerEvent<HTMLDivElement>) {
    if (disabled || !source || !picture || picture.film !== source.film_id || event.button !== 0) return;
    event.preventDefault(); event.currentTarget.focus(); pauseForFraming();
    const rect = event.currentTarget.getBoundingClientRect();
    cropDrag.current = { pointer: event.pointerId, film: source.film_id, x: event.clientX, y: event.clientY, width: rect.width, height: rect.height, picture, framing };
    event.currentTarget.setPointerCapture(event.pointerId);
  }
  function moveCrop(event: PointerEvent<HTMLDivElement>) {
    const drag = cropDrag.current;
    if (!drag || disabled || !source || drag.film !== source.film_id || drag.pointer !== event.pointerId) return;
    onChange({ ...source, framing: framingDrag(drag.framing, drag.picture, drag, event.clientX - drag.x, event.clientY - drag.y) });
  }
  function cropKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") { setReframing(false); cropDrag.current = null; return; }
    if (disabled || !source || !picture || picture.film !== source.film_id) return;
    const dx = event.key === "ArrowLeft" ? -1 : event.key === "ArrowRight" ? 1 : 0;
    const dy = event.key === "ArrowUp" ? -1 : event.key === "ArrowDown" ? 1 : 0;
    if (dx || dy) { event.preventDefault(); pauseForFraming(); const rect = event.currentTarget.getBoundingClientRect(), step = event.shiftKey ? 10 : 2;
      onChange({ ...source, framing: framingDrag(framing, picture, rect, dx * step, dy * step) }); }
  }
  function seek(time: number, revealVideo = true, inspection = false) {
    inspectionPosition.current = inspection;
    if (revealVideo) { setShowEndpoint(false); if (source) setRequestedFilm(source.film_id); }
    pendingPlay.current = null; setLoadingSource(false); setPlaying(false);
    playOperation.current++; const node = video.current;
    if (!source || !Number.isFinite(time)) return;
    node?.pause();
    const target = Math.max(0, Math.min(node && Number.isFinite(node.duration) ? node.duration - .001 : Infinity, time));
    setPosition(target);
    if (!node || node.readyState < 1) { pendingSeek.current = { film: source.film_id, time: target, inspection }; return; }
    pendingSeek.current = null; node.currentTime = target;
  }
  function startPendingPlay() {
    const pending = pendingPlay.current, node = video.current;
    if (!pending || !node || node.readyState < 1 || pending.film !== source?.film_id || pending.token !== playOperation.current || disabled) return;
    pendingPlay.current = null; pendingSeek.current = null;
    node.currentTime = Math.max(0, Math.min(Number.isFinite(node.duration) ? node.duration - .001 : Infinity, pending.time));
    setPosition(node.currentTime); inspectionPosition.current = false;
    void node.play().then(() => { if (pending.token === playOperation.current) setLoadingSource(false); })
      .catch(() => { if (pending.token === playOperation.current) { setLoadingSource(false); setError("Source playback could not start. Check the film drive."); } });
  }
  useEffect(() => {
    setShowEndpoint(!!endpoint); setEndpointFailed(null);
    if (endpoint) { inspectionPosition.current = true; playOperation.current++; pendingPlay.current = null; setLoadingSource(false); video.current?.pause(); setPlaying(false); }
  }, [endpoint?.url, endpoint?.time]);
  useEffect(() => {
    if (!source) return;
    const abort = new AbortController(); const film = source.film_id, media = video.current; setError(""); setFilmDuration(Infinity); setPlaying(false);
    fetch(`${LAB_API}/video/${encodeURIComponent(film)}/playback`, { signal: abort.signal, cache: "no-store" })
      .then(async (response) => { if (!response.ok) throw new Error("Source preview unavailable. Check the film drive."); return response.json(); })
      .then((result: { url: string }) => { if (!abort.signal.aborted) setPlayback({ film, url: result.url }); })
      .catch((reason) => { if (!abort.signal.aborted) setError(reason.message); });
    return () => { abort.abort(); playOperation.current++; pendingPlay.current = null; media?.pause(); };
  }, [source?.film_id]);
  useEffect(() => {
    const intent = trimInspection.current;
    const time = source && intent?.film === source.film_id && intent.start === source.source_start && intent.end === source.source_end ? intent.time : cutTime;
    trimInspection.current = null;
    if (Number.isFinite(time)) seek(time, false, true);
  }, [source?.film_id, source?.source_start, source?.source_end]);
  useEffect(() => { if (disabled) { playOperation.current++; pendingPlay.current = null; setLoadingSource(false); video.current?.pause(); } }, [disabled]);
  useEffect(() => {
    if (disabled || !inspection || inspection.film !== source?.film_id || !Number.isFinite(inspection.time)) return;
    if (appliedInspection.current?.film === inspection.film && appliedInspection.current.token === inspection.token) return;
    appliedInspection.current = { film: inspection.film, token: inspection.token };
    seek(inspection.time);
  }, [inspection?.token, inspection?.film, disabled, source?.film_id]);
  useEffect(() => { trimDrag.current = null; cropDrag.current = null; setDragBounds(null); setReframing(false); }, [disabled, source?.film_id, aspect]);
  function toggle() {
    const node = video.current;
    if (!node || !source) return;
    setShowEndpoint(false);
    if (playing || loadingSource) { playOperation.current++; pendingPlay.current = null; setLoadingSource(false); node.pause(); }
    else {
      if (!Number.isFinite(source.source_start) || !Number.isFinite(source.source_end) || source.source_end <= source.source_start) return;
      const resumeTime = pendingSeek.current?.film === source.film_id ? pendingSeek.current.time : node.currentTime;
      const time = inspectionPosition.current || still || resumeTime < source.source_start || resumeTime >= source.source_end ? source.source_start : resumeTime;
      const token = ++playOperation.current;
      pendingPlay.current = { film: source.film_id, time, token }; setLoadingSource(true); setRequestedFilm(source.film_id);
      startPendingPlay();
    }
  }
  return <section className={`${styles.source} ${monitor ? controls.monitor : ""}`} aria-label={`${label === "A" ? "Outgoing" : "Incoming"} clip`}>
    {!monitor && <div className={styles.sourceHeading}><span className={styles.letter}>{label}</span><strong>{label === "A" ? "Outgoing" : "Incoming"}</strong>
      <button type="button" onClick={onChoose} disabled={disabled}>{source ? "Change clip" : "Choose clip"}</button></div>}
    {source ? <>
      <div className={styles.framingStage}><div className={styles.sourceViewport} style={{ aspectRatio: ratio, maxWidth: monitor ? `calc(var(--monitor-height, 380px) * ${ratio})` : ratio * 200 }}>
        <video key={source.film_id} ref={video} src={attachMedia && playback?.film === source.film_id ? mediaUrl(playback.url) : undefined}
          muted playsInline preload={attachMedia ? "metadata" : "none"} aria-label={`Preview clip ${label}`} aria-hidden={still}
          style={pictureStyle}
          onLoadedMetadata={(event) => { if (event.currentTarget !== video.current) return; setError(""); setRequestedFilm(source.film_id); setFilmDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : Infinity);
            setPicture({ film: source.film_id, width: event.currentTarget.videoWidth, height: event.currentTarget.videoHeight });
            if (Number.isFinite(event.currentTarget.duration)) onDurationKnown?.(event.currentTarget.duration);
            if (pendingPlay.current?.film === source.film_id) startPendingPlay();
            else { const pending = pendingSeek.current?.film === source.film_id ? pendingSeek.current : null; if (Number.isFinite(pending?.time ?? cutTime)) seek(pending?.time ?? cutTime, false, pending?.inspection ?? true); } }}
          onPlay={(event) => { if (event.currentTarget !== video.current) return; setPlaying(true); setLoadingSource(false); }} onPause={(event) => { if (event.currentTarget === video.current) setPlaying(false); }}
          onTimeUpdate={(event) => { const node = event.currentTarget; if (node !== video.current) return; setPosition(node.currentTime); if (!node.paused && Number.isFinite(source.source_end) && node.currentTime >= source.source_end) seek(cutTime, true, true); }}
          onError={(event) => { if (event.currentTarget !== video.current) return; playOperation.current++; pendingPlay.current = null; setLoadingSource(false); setError("Source preview unavailable. Try another clip or check the film drive."); }} />
        {still && <img className={styles.sourceEndpoint} src={mediaUrl(endpoint!.url)} alt={label === "A" ? "Retained last outgoing source frame" : "Retained first incoming source frame"} style={pictureStyle}
          onLoad={(event) => setPicture({ film: source.film_id, width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
          onError={() => { setEndpointFailed(endpoint!.url); setShowEndpoint(false); }} />}
        {reframing && <div className={controls.frameSurface} role="group" tabIndex={disabled ? -1 : 0} aria-label={`Drag clip ${label} to reframe. Arrow keys move the picture; Escape finishes.`}
          onPointerDown={startCrop} onPointerMove={moveCrop} onPointerUp={() => { cropDrag.current = null; }} onPointerCancel={() => { cropDrag.current = null; }} onLostPointerCapture={() => { cropDrag.current = null; }} onKeyDown={cropKey} />}
      </div></div>
      <div className={styles.sourceTransport}><button type="button" disabled={disabled || !playable || !!error} onClick={toggle}>{loadingSource ? "Cancel loading" : playing ? "Pause" : "Play clip"}</button><button type="button" className={styles.sourceCutJump} disabled={disabled || (!validEndpoint && (!playable || !!error))} title={validEndpoint ? "Show the retained native source endpoint from this render." : "Browser seeking is approximate. Render this pair for a precise retained endpoint."} onClick={() => { if (validEndpoint) { inspectionPosition.current = true; playOperation.current++; pendingPlay.current = null; setLoadingSource(false); video.current?.pause(); setPlaying(false); setEndpointFailed(null); setShowEndpoint(true); } else seek(cutTime, true, true); }}>{validEndpoint ? label === "A" ? "Inspect end" : "Inspect start" : label === "A" ? "Near end" : "Near start"}</button><span title={still ? `Retained source frame at ${endpoint!.time.toFixed(6)} seconds` : "Browser playback position"}>{seconds(displayedTime)}</span></div>
      {!monitor && <div className={controls.title}><strong title={source.title}>{source.title}</strong><span>{Number.isFinite(source.source_end - source.source_start) ? `${(source.source_end - source.source_start).toFixed(2)}s` : "—"}</span></div>}
      {!monitor && <><div className={controls.trimRail} ref={trimRail}>
        <div className={controls.selection} style={{ left: `${percent(source.source_start)}%`, right: `${100 - percent(source.source_end)}%` }} />
        <input className={controls.playhead} type="range" aria-label={`Clip ${label} source position in seconds`} min={scrub.start} max={scrub.end} step="0.01" value={Math.max(scrub.start, Math.min(scrub.end, displayedTime))} disabled={disabled || !playable || !!error} onChange={(event) => seek(Number(event.target.value))} />
        {(["source_start", "source_end"] as const).map((edge) => { const limits = trimLimits(source, edge, filmDuration); return <button type="button" role="slider" key={edge} className={controls.handle} style={{ left: `${percent(source[edge])}%` }}
          aria-label={`Clip ${label} ${edge === "source_start" ? "in" : "out"} trim`} aria-valuemin={limits.min} aria-valuemax={limits.max} aria-valuenow={source[edge]} aria-valuetext={seconds(source[edge])}
          title={`Drag to trim ${edge === "source_start" ? "in" : "out"}. Arrow keys: one frame; Shift: 0.1s.`} disabled={disabled || !Number.isFinite(source.source_start) || !Number.isFinite(source.source_end)}
          onPointerDown={(event) => startTrim(event, edge)} onPointerMove={moveTrim} onPointerUp={endTrim} onPointerCancel={endTrim} onLostPointerCapture={endTrim} onKeyDown={(event) => trimKey(event, edge)} />; })}
      </div>
      <div className={controls.trimCaption}><span>{seconds(source.source_start)}</span><span>{still && <span className={controls.savedStatus}>Saved endpoint · </span>}Drag edges to trim</span><span>{seconds(source.source_end)}</span></div></>}
      <div className={controls.frameToolbar}><button type="button" aria-pressed={reframing} disabled={disabled} onClick={() => { if (!reframing) pauseForFraming(); setReframing(!reframing); cropDrag.current = null; }}>{reframing ? "Done framing" : "Reframe"}</button>
        {reframing && (["fit", "fill"] as const).map((fit) => <button type="button" key={fit} aria-pressed={framing.fit === fit} disabled={disabled} onClick={() => onChange({ ...source, framing: { ...framing, fit } })}>{fit === "fit" ? "Fit" : "Fill"}</button>)}
      </div>
      {reframing && <><p className={controls.frameHint}>Drag the picture to position it. Use Fill or zoom in to crop closer.</p><label className={styles.slider}>Zoom <output>{framing.zoom.toFixed(2)}×</output><input aria-label={`Clip ${label} zoom`} type="range" min="1" max="2" step="0.01" value={framing.zoom} disabled={disabled} onChange={(event) => onChange({ ...source, framing: { ...framing, zoom: Number(event.target.value) } })} /></label>
        <button className={styles.textButton} type="button" disabled={disabled} onClick={() => onChange({ ...source, framing: { ...DEFAULT_FRAMING } })}>Reset framing</button></>}
      <details className={controls.precision}><summary>Precision controls</summary>
      <div className={styles.trim}>
        <label>In <input aria-label={`Clip ${label} in seconds`} type="number" min="0" max={Number.isFinite(filmDuration) ? filmDuration : undefined} step="0.01" value={Number.isFinite(source.source_start) ? Number(source.source_start.toFixed(3)) : ""} disabled={disabled}
          onChange={(event) => onChange({ ...source, source_start: event.target.value === "" ? NaN : Number(event.target.value) })} /></label>
        <label>Out <input aria-label={`Clip ${label} out seconds`} type="number" min="0" max={Number.isFinite(filmDuration) ? filmDuration : undefined} step="0.01" value={Number.isFinite(source.source_end) ? Number(source.source_end.toFixed(3)) : ""} disabled={disabled}
          onChange={(event) => onChange({ ...source, source_end: event.target.value === "" ? NaN : Number(event.target.value) })} /></label>
        <span>{Number.isFinite(source.source_end - source.source_start) ? `${(source.source_end - source.source_start).toFixed(2)}s` : "—"}</span>
      </div>
      {source.source_end > filmDuration && <p className={styles.error} role="status">This film ends at {seconds(filmDuration)}. Move the trim earlier.</p>}
      <div className={styles.cutNudge}><span>{label === "A" ? "Outgoing end" : "Incoming start"}</span>{[-.1, .1].map((offset) => {
        const adjusted = sourceBoundary(source, cutBoundary, source[cutBoundary] + offset, filmDuration);
        return <button type="button" key={offset} disabled={disabled || !adjusted} aria-label={`Move clip ${label} ${label === "A" ? "out" : "in"} ${offset < 0 ? "earlier" : "later"} by 0.1 seconds`} onClick={() => adjusted && onChange({ ...source, ...adjusted })}>{offset < 0 ? "−0.1s" : "+0.1s"}</button>;
      })}<button type="button" disabled={disabled || still || !playable || !sourceBoundary(source, cutBoundary, position, filmDuration)} onClick={() => { if (still) return; const next = sourceBoundary(source, cutBoundary, position, filmDuration); if (next) onChange({ ...source, ...next }); }}>Use playhead</button></div>
        <div className={styles.framingAxes}>{(["anchor_x", "anchor_y"] as const).map((key) => <label className={styles.slider} key={key}>{key === "anchor_x" ? "Horizontal" : "Vertical"}<output>{Math.round(framing[key] * 100)}%</output><input type="range" aria-label={`Clip ${label} ${key === "anchor_x" ? "horizontal" : "vertical"} position`} min="0" max="1" step="0.01" value={framing[key]} disabled={disabled} onChange={(event) => onChange({ ...source, framing: { ...framing, [key]: Number(event.target.value) } })} /></label>)}</div>
      </details>
      {error && <p className={styles.error} role="alert">{error}</p>}
    </> : <button className={styles.sourcePlaceholder} type="button" onClick={onChoose} disabled={disabled}><span aria-hidden="true">＋</span><span>{label === "A" ? "Start with a moment" : "Choose where it goes"}</span><small>Find footage in your library</small></button>}
  </section>;
}
