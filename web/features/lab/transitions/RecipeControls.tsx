"use client";

import { useState } from "react";
import { beatFrames, type ControlSpec, type RecipeDefinition, type TransitionRecipe } from "./transitions";
import styles from "./transitions.module.css";

const FALLBACK: Record<string, ControlSpec> = {
  direction: { key: "direction", label: "Direction", type: "select", options: ["left", "right", "up", "down"] },
  easing: { key: "easing", label: "Motion curve", type: "select", options: ["snappy", "smooth", "linear"] },
  intensity: { key: "intensity", label: "Intensity", type: "range", min: 0, max: 1, step: .01, unit: "%" },
  softness: { key: "softness", label: "Softness", type: "range", min: .01, max: .5, step: .01, unit: "%" },
};

export default function RecipeControls({ definition, recipe, bpm, onBpmChange, onChange }: {
  definition: RecipeDefinition; recipe: TransitionRecipe; bpm: number; onBpmChange: (value: number) => void;
  onChange: (recipe: TransitionRecipe) => void;
}) {
  const [beats, setBeats] = useState(.5);
  const specs = (definition.control_specs ?? definition.controls.map((key) => FALLBACK[key]).filter(Boolean)).filter((item) => item.key !== "duration");
  const main = specs.filter((item) => !item.advanced).slice(0, 4);
  const advanced = specs.filter((item) => !main.includes(item));
  const frames = Math.round(recipe.duration * 30);
  const timing = definition.controls.includes("duration");
  const suggestedFrames = beatFrames(bpm, beats);
  const setFrames = (value: number) => { if (Number.isFinite(value)) onChange({ ...recipe, duration: Math.max(3, Math.min(60, Math.round(value))) / 30 }); };
  function control(spec: ControlSpec) {
    if (spec.key === "direction") return <fieldset className={styles.fieldset} key={spec.key}><legend>{spec.label}</legend><div className={styles.segmented}>
      {(["left", "right", "up", "down"] as const).map((direction, index) => <button key={direction} type="button" aria-label={`Move ${direction}`} aria-pressed={recipe.direction === direction} onClick={() => onChange({ ...recipe, direction })}>{["←", "→", "↑", "↓"][index]}</button>)}
    </div></fieldset>;
    if (spec.type === "select") return <label className={styles.selectLabel} key={spec.key}>{spec.label}<select aria-label={spec.label} value={String(recipe[spec.key] ?? "")} onChange={(event) => onChange({ ...recipe, [spec.key]: event.target.value })}>
      {(spec.options ?? []).map((option) => { const value = typeof option === "string" ? option : option.value; return <option key={value} value={value}>{typeof option === "string" ? option.charAt(0).toUpperCase() + option.slice(1) : option.label}</option>; })}
    </select></label>;
    const value = Number(recipe[spec.key] ?? spec.min ?? 0);
    if (spec.key === "seed") return <label className={styles.selectLabel} key={spec.key}>{spec.label}<input aria-label={spec.label} type="number" min={spec.min} max={spec.max} step={1} value={value} onChange={(event) => { const next = event.target.valueAsNumber; if (Number.isFinite(next)) onChange({ ...recipe, [spec.key]: Math.max(spec.min ?? 0, Math.min(spec.max ?? 2147483647, Math.round(next))) }); }} /></label>;
    const formatted = spec.unit === "%" ? `${Math.round(value * 100)}%` : `${Number(value.toFixed((spec.step ?? .01) >= 1 ? 0 : 2))}${spec.unit ?? ""}`;
    return <label className={styles.slider} key={spec.key}>{spec.label}<output>{formatted}</output>
      <input aria-label={spec.label} type="range" min={spec.min ?? 0} max={spec.max ?? 1} step={spec.step ?? .01} value={value} onChange={(event) => onChange({ ...recipe, [spec.key]: Number(event.target.value) })} />
    </label>;
  }
  return <div className={styles.controls}>
    <div><h3>{definition.name}</h3><p>{definition.description}</p></div>
    {timing && <div className={styles.timingControls}>
      <label className={styles.slider}>Duration <output>{frames} frames · {(frames / 30).toFixed(2)}s</output><input aria-label="Transition duration in frames" type="range" min="3" max="60" step="1" value={frames} onChange={(event) => setFrames(event.target.valueAsNumber)} /></label>
      <div className={styles.quickFrames} aria-label="Quick transition durations">{[3, 4, 6, 8, 12, 18].map((count) => <button type="button" key={count} aria-pressed={frames === count} onClick={() => setFrames(count)}>{count}f</button>)}</div>
      <details className={styles.beatControls}><summary>Time it to music</summary><div><label>BPM<input aria-label="Music BPM" type="number" min="20" max="300" step="1" value={Number.isFinite(bpm) ? bpm : ""} onChange={(event) => onBpmChange(event.target.valueAsNumber)} /></label><label>Note<select aria-label="Beat subdivision" value={beats} onChange={(event) => setBeats(Number(event.target.value))}><option value="0.25">1/16</option><option value="0.3333333333333333">1/8 triplet</option><option value="0.5">1/8</option><option value="0.75">Dotted 1/8</option><option value="1">1/4</option><option value="2">1/2</option></select></label></div>
        <button className={styles.secondary} type="button" disabled={!suggestedFrames} onClick={() => setFrames(suggestedFrames)}>Use {suggestedFrames || "—"} frames</button><p className={styles.hint}>Rounds to the nearest output frame, within 3–60 frames. Your source clips stay unchanged.</p>
      </details>
    </div>}
    {main.map(control)}
    {advanced.length > 0 && <details className={styles.advancedControls}><summary>Fine tune <span>{advanced.length}</span></summary><div>{advanced.map(control)}</div></details>}
    <button type="button" className={styles.textButton} onClick={() => onChange({ ...definition.defaults })}>Reset this transition</button>
  </div>;
}
