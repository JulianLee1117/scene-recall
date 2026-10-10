"use client";

import { useState } from "react";
import { mediaUrl, seconds } from "@/lib/lab";
import { handoffReceipt, type TransitionJob } from "./transitions";
import { BRIDGE_PROMPTS, BRIDGE_DIRECTIONS, bridgeSourceTimingProblem, buildBridgePrompt, type BridgeDirectionOptions } from "./bridges";
import BridgeImports from "./BridgeImports";
import GenerationPanel from "./GenerationPanel";
import type { GenerationJob } from "./generation";
import styles from "./transitions.module.css";
import ai from "./ai-handoff.module.css";

function downloadJson(value: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function AIHandoff({ job, active, sourcePairChanged = false }: { job: TransitionJob | null; active: boolean; sourcePairChanged?: boolean }) {
  const [method, setMethod] = useState<"generate" | "import">("generate");
  const [timingSource, setTimingSource] = useState<GenerationJob | null>(null);
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState<string>(() => buildBridgePrompt(BRIDGE_PROMPTS[0].id, "", { energy: "restrained", direction: "auto" }));
  const [starterId, setStarterId] = useState<string>(BRIDGE_PROMPTS[0].id);
  const [energy, setEnergy] = useState<NonNullable<BridgeDirectionOptions["energy"]>>("restrained");
  const [direction, setDirection] = useState<NonNullable<BridgeDirectionOptions["direction"]>>("auto");
  const [motionNotes, setMotionNotes] = useState("");
  const [anchor, setAnchor] = useState("");
  const signature = JSON.stringify([starterId, energy, direction, motionNotes, anchor]);
  const [applied, setApplied] = useState(signature);
  const [custom, setCustom] = useState(false);
  const starter = BRIDGE_PROMPTS.find((item) => item.id === starterId)!;
  const timingProblem = bridgeSourceTimingProblem(job?.request.retime);
  function applyDirection() {
    setPrompt(buildBridgePrompt(starterId, motionNotes, { energy, direction, anchor }));
    setApplied(signature); setCustom(false);
  }
  return <section className={`${styles.ai} ${ai.flow}`} aria-label="AI transition handoff">
    <div className={styles.sectionTitle}><h2>AI transition direction</h2><span>Two frames → one move</span></div>
    <p className={ai.intro}>Shape a bridge between these endpoints, then inspect how it enters and leaves the original clips.</p>
    {job?.result && <p className={ai.sourceReceipt}><strong>{sourcePairChanged ? "Saved pair · differs from working clips" : "Prepared source pair"}</strong><span>{job.source_titles?.outgoing || "Clip A"} {seconds(job.request.outgoing.source_start)}–{seconds(job.request.outgoing.source_end)} → {job.source_titles?.incoming || "Clip B"} {seconds(job.request.incoming.source_start)}–{seconds(job.request.incoming.source_end)}</span></p>}
    {job?.result ? <div className={ai.endpoints}>{(["a", "b"] as const).map((key) => <a key={key} href={mediaUrl(key === "a" ? job.result!.frame_a_url : job.result!.frame_b_url)} download={"transition-" + key + ".jpg"}>
      <img src={mediaUrl(key === "a" ? job.result!.frame_a_url : job.result!.frame_b_url)} alt={key === "a" ? "Last outgoing source frame" : "First incoming source frame"} />
      <span>{key === "a" ? "A · departure" : "B · arrival"}<small>Native frame ↓</small></span></a>)}</div>
      : <p className={styles.aiEmpty}>Render a source pair to prepare its endpoint frames.</p>}
    {timingProblem && <p className={ai.prerequisite} role="status">{timingProblem} Native frames and the handoff receipt remain available.</p>}
    <section className={ai.direction} aria-label="Bridge direction">
      <div className={ai.sectionHeading}><h3>Direction</h3><span>{starter.group === "experimental" ? "Experimental direction" : "Prompt guidance"}</span></div>
      <div className={ai.controls}>
        <label>Technique<select aria-label="AI bridge technique" value={starterId} onChange={(e) => {
          const next = BRIDGE_PROMPTS.find((item) => item.id === e.target.value)!;
          setStarterId(next.id); if (!(next.directions as readonly string[]).includes(direction)) setDirection("auto");
        }}>{(["cinematic", "experimental"] as const).map((group) => <optgroup key={group} label={group === "cinematic" ? "Cinematic" : "Experimental"}>{BRIDGE_PROMPTS.filter((item) => item.group === group).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</optgroup>)}</select></label>
        <label>Energy<select aria-label="AI bridge energy" value={energy} onChange={(e) => setEnergy(e.target.value as typeof energy)}><option value="restrained">Restrained</option><option value="balanced">Balanced</option><option value="bold">Bold</option></select></label>
        <label>Travel<select aria-label="AI bridge travel direction" value={direction} onChange={(e) => setDirection(e.target.value as typeof direction)}>{BRIDGE_DIRECTIONS.filter((item) => (starter.directions as readonly string[]).includes(item.id)).map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
      </div>
      <p className={ai.hint}>{starter.fit}</p>
      <details className={ai.details}><summary>Source motion &amp; subject anchor</summary>
        <label>Observed motion<textarea rows={2} maxLength={2000} value={motionNotes} onChange={(e) => setMotionNotes(e.target.value)} aria-label="Source motion notes" placeholder="A moves right past a column. B continues right, more slowly." /></label>
        <label>Subject anchor<input maxLength={240} value={anchor} onChange={(e) => setAnchor(e.target.value)} aria-label="AI bridge subject anchor" placeholder="Keep the face near the upper center" /></label>
        <p className={ai.hint}>Stills do not establish motion. Describe the movement you observed in each clip.</p>
      </details>
      <div className={ai.apply}><button type="button" className={styles.secondary} onClick={applyDirection}>Apply direction to prompt</button>
        <span role="status">{signature !== applied ? "Direction changed · apply to update prompt" : custom ? "Custom prompt · kept until you apply direction" : "Direction applied"}</span></div>
      <details className={ai.details}><summary>Edit prompt <span>{prompt.length.toLocaleString()} characters{custom ? " · customized" : ""}</span></summary>
        <label>Prompt<textarea rows={6} maxLength={20000} value={prompt} onChange={(e) => { setPrompt(e.target.value); setCustom(true); }} aria-label="AI transition prompt" /></label>
        <p className={ai.hint}>Applying direction replaces this text. Technique and control changes leave it intact until you apply them.</p>
      </details>
      <p className={ai.check}><strong>Watch for:</strong> {starter.check}</p>
    </section>
    <div className={ai.methods} role="group" aria-label="Create the AI bridge">
      <button id="ai-generate-tab" type="button" aria-pressed={method === "generate"} aria-controls="ai-generate-panel" onClick={() => setMethod("generate")}>Generate here<span>Runway connection</span></button>
      <button id="ai-import-tab" type="button" aria-pressed={method === "import"} aria-controls="ai-import-panel" onClick={() => setMethod("import")}>Bring a result<span>Higgsfield or another tool</span></button>
    </div>
    <div id="ai-generate-panel" role="region" aria-labelledby="ai-generate-tab" hidden={method !== "generate"}>
      <GenerationPanel job={job} active={active && method === "generate"} prompt={prompt} prerequisiteShown={!!timingProblem}
        onAdjustTiming={(generation) => { setTimingSource({ ...generation }); setMethod("import"); }} />
    </div>
    <div id="ai-import-panel" role="region" aria-labelledby="ai-import-tab" hidden={method !== "import"}>
      {!timingSource && <div className={ai.handoff}>
        <p>Use the native endpoint downloads above in a tool with first-and-last-frame support. Bring its video back to trim and audition both joins.</p>
        <label>Provider / model notes<input value={model} maxLength={200} aria-label="AI provider and model" onChange={(e) => setModel(e.target.value)} placeholder="e.g. Higgsfield · Seedance 2.5" /></label>
        <div className={ai.links}><button type="button" className={styles.secondary} disabled={!job?.result || !prompt.trim()}
          onClick={() => job && downloadJson(handoffReceipt(job, model.trim() || "Unspecified external generator", prompt), "transition-" + job.id + "-ai-handoff.json")}>Download prompt &amp; handoff receipt</button><a href="https://higgsfield.ai/" target="_blank" rel="noreferrer">Open Higgsfield ↗</a></div>
      </div>}
      <BridgeImports job={job} active={active && method === "import"} model={model} prompt={prompt} prerequisiteShown={!!timingProblem}
        generationSource={timingSource} onExternalFile={() => setTimingSource(null)} />
    </div>
  </section>;
}
