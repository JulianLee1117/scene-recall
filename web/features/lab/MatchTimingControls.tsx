"use client";

import { useState } from "react";
import { labRequest, seconds } from "@/lib/lab";
import type { MatchCandidate } from "@/types/lab";
import { stepFrame, type MatchFrames } from "./matchFrames";
import EditorIcon from "./EditorIcon";
import styles from "./lab.module.css";
import cut from "./matchWorkspace.module.css";

export default function MatchTimingControls({ candidate, disabled, onPreview, onCancel }: {
  candidate: MatchCandidate;
  disabled: boolean;
  onPreview: (outgoing: number, incoming: number) => void;
  onCancel: () => void;
}) {
  const [outgoing, setOutgoing] = useState(candidate.outgoing.reference_time ?? candidate.outgoing.source_end);
  const [incoming, setIncoming] = useState(candidate.incoming.reference_time ?? candidate.incoming.source_start);
  const [stepping, setStepping] = useState(false);
  const [error, setError] = useState("");
  async function step(role: "outgoing" | "incoming", direction: -1 | 1) {
    const clip = candidate[role];
    if (!clip.unit_id || stepping) return;
    setStepping(true);
    setError("");
    try {
      const frames = await labRequest<MatchFrames>(`/matching/frames?unit_id=${encodeURIComponent(clip.unit_id)}&time=${role === "outgoing" ? outgoing : incoming}`);
      const next = stepFrame(frames.frames, role === "outgoing" ? outgoing : incoming, direction);
      if (next) (role === "outgoing" ? setOutgoing : setIncoming)(next.time);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Could not read the source frames."); }
    finally { setStepping(false); }
  }
  return <fieldset className={cut.timing} disabled={disabled || stepping}>
    <legend>Adjust timing</legend>
    <div className={cut.timingRows}>
      {(["outgoing", "incoming"] as const).map((role) => <label key={role}>
        <span>{role === "outgoing" ? "A · last frame" : "B · first frame"}</span>
        <div>
          <button type="button" disabled={!candidate[role].unit_id || disabled || stepping} aria-label={`Previous ${role} frame`} onClick={() => void step(role, -1)}><EditorIcon name="previous" /></button>
          <input aria-label={`${role === "outgoing" ? "Outgoing" : "Incoming"} source time in seconds`} type="number" min={0} step="any" value={Number((role === "outgoing" ? outgoing : incoming).toFixed(6))} onChange={(event) => (role === "outgoing" ? setOutgoing : setIncoming)(Number(event.target.value))} />
          <button type="button" disabled={!candidate[role].unit_id || disabled || stepping} aria-label={`Next ${role} frame`} onClick={() => void step(role, 1)}><EditorIcon name="next" /></button>
        </div>
        <small>{seconds(role === "outgoing" ? outgoing : incoming)} in source film</small>
      </label>)}
    </div>
    <p>Frame buttons use the source film’s actual timing. Preview before keeping your changes.</p>
    <div className={cut.actions}>
      <button className={styles.secondary} disabled={!Number.isFinite(outgoing) || !Number.isFinite(incoming) || disabled || stepping} onClick={() => onPreview(outgoing, incoming)}>Preview timing</button>
      <button className={styles.ghost} onClick={onCancel}>Cancel</button>
    </div>
    {error && <p role="alert" className={styles.error}>{error}</p>}
  </fieldset>;
}
