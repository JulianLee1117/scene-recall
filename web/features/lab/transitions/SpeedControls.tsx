import { DEFAULT_RETIME, retimedFrames, type TransitionRetime, type TransitionSource } from "./transitions";
import styles from "./transitions.module.css";

const modes = [
  { id: "off", name: "Off", description: "Original source speed. Shared across every effect." },
  { id: "rush", name: "Rush", description: "Accelerate into the cut, then ease back out on B." },
  { id: "slow-hit", name: "Slow hit", description: "Slow into the cut, then recover on B." },
  { id: "pulse", name: "Pulse", description: "A brief speed burst on each side of the cut." },
] as const;

export default function SpeedControls({ value, a, b, onChange }: {
  value: TransitionRetime; a: TransitionSource | null; b: TransitionSource | null; onChange: (value: TransitionRetime) => void;
}) {
  const active = value.mode !== "off";
  const durations = [a, b].map((source) => source ? source.source_end - source.source_start : null);
  const maxSpan = Math.max(.1, Math.min(2, ...durations.filter((duration): duration is number => duration !== null && Number.isFinite(duration) && duration > 0)));
  const validTimes = durations.every((duration) => duration !== null && Number.isFinite(duration) && duration >= .2 - 1e-6 && duration <= 12 + 1e-6 && (!active || value.span <= duration + 1e-8));
  function mode(next: TransitionRetime["mode"]) {
    if (next === value.mode) return;
    onChange(next === "off" ? { ...DEFAULT_RETIME } : { ...value, mode: next, speed: next === "slow-hit" ? .5 : 2, span: Math.min(value.span, maxSpan) });
  }
  return <section className={styles.speedControls} aria-label="Source speed controls">
    <div className={styles.speedHeading}><h3>Speed</h3><span>{active ? "Baked into render" : "Original timing"}</span></div>
    <div className={styles.segmented} role="group" aria-label="Speed shape">{modes.map((item) => <button key={item.id} type="button" aria-pressed={value.mode === item.id} onClick={() => mode(item.id)}>{item.name}</button>)}</div>
    <p>{modes.find((item) => item.id === value.mode)?.description}</p>
    {active && <>
      <label className={styles.slider}>{value.mode === "pulse" ? "Peak speed" : "Edge speed"}<output>{Number(value.speed.toFixed(2))}×</output>
        <input type="range" aria-label={value.mode === "pulse" ? "Peak source speed" : "Edge source speed"} min={value.mode === "slow-hit" ? .25 : 1} max={value.mode === "slow-hit" ? 1 : 4} step="0.05" value={value.speed} onChange={(event) => onChange({ ...value, speed: Number(event.target.value) })} />
      </label>
      <label className={styles.slider}>Ramp window<output>{value.span.toFixed(2)}s per clip</output><input type="range" aria-label="Ramp window in source seconds" min="0.1" max={maxSpan} step="0.01" value={Math.min(maxSpan, value.span)} onChange={(event) => onChange({ ...value, span: Number(event.target.value) })} /></label>
      <details className={styles.speedDetails}><summary>Curve &amp; smoothing</summary><div>
        <label className={styles.selectLabel}>Curve<select aria-label="Speed curve" value={value.curve} onChange={(event) => onChange({ ...value, curve: event.target.value as TransitionRetime["curve"] })}><option value="smooth">Smooth</option><option value="snappy">Snappy</option></select></label>
        <label className={styles.selectLabel}>Smoothing<select aria-label="Speed frame interpolation" value={value.interpolation} onChange={(event) => onChange({ ...value, interpolation: event.target.value as TransitionRetime["interpolation"] })}><option value="nearest">Frame sampling</option><option value="blend">Frame blending</option><option value="flow">Optical flow</option></select></label>
        <p>{value.interpolation === "flow" ? "Optical flow estimates motion within each clip. Slower to render; fast motion can warp." : value.interpolation === "blend" ? "Blends neighboring source frames; moving edges can leave a soft trail." : "Uses captured source frames. Slow motion can repeat frames."}</p>
      </div></details>
      {validTimes && <div className={styles.speedDurations} aria-label="Estimated clip durations after speed change">{durations.map((duration, index) => <span key={index}>{index === 0 ? "A" : "B"} <b>{duration!.toFixed(2)}s</b> → <b>{(retimedFrames(duration!, value) / 30).toFixed(2)}s</b></span>)}</div>}
      <p className={styles.speedNote}>The window is measured in source seconds. Selected footage stays fixed; render to see the new timing.</p>
      {value.mode !== "pulse" && <p className={styles.speedNote}>Long overlaps can hide the edge-speed peak. Try a longer source ramp or shorter transition.</p>}
    </>}
  </section>;
}
