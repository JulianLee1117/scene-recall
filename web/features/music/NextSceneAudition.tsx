"use client";

import { useEffect, useRef, useState } from "react";
import { seconds } from "@/lib/lab";
import type { LabJob, NextSceneAdjustment } from "@/types/lab";
import EditorIcon from "@/features/lab/EditorIcon";
import type { NextSceneAuditionSelection } from "./NextScenePanel";
import NextScenePairPlayer from "./NextScenePairPlayer";
import SourceSceneEditor from "./SourceSceneEditor";
import { nextSceneBounds, nextScenePreview, sameNextSceneDraft } from "./nextScene";
import player from "./sequencePlayer.module.css";
import styles from "./nextSceneAudition.module.css";

type Props = {
  selection: NextSceneAuditionSelection;
  outputRatio: number;
  latestJob: LabJob | null;
  busy: boolean;
  suspended: boolean;
  onClose: () => void;
  onTimeChange: (time: number) => void;
  onCutChange: (cut: number) => void;
  onPrepare: (id: string, adjustment: NextSceneAdjustment) => Promise<LabJob | null>;
  onApply: (id: string, adjustment: NextSceneAdjustment) => void;
  onCancel: () => void;
};

/** One selected option owns its complete draft and the proof for its exact preview. */
export default function NextSceneAudition({ selection, outputRatio, latestJob, busy, suspended, onClose, onTimeChange, onCutChange, onPrepare, onApply, onCancel }: Props) {
  const [draft, setDraft] = useState<NextSceneAdjustment>(() => ({ source_start: selection.candidate.incoming.source_start, cut_time: selection.candidate.cut, crop: selection.candidate.incoming.crop ?? null }));
  const [view, setView] = useState(selection.initialView);
  const [prepared, setPrepared] = useState({ candidate: selection.candidate, previewJobId: selection.previewJobId });
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [playbackFailed, setPlaybackFailed] = useState(false);
  const [error, setError] = useState("");
  const mounted = useRef(true);
  const request = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const disabled = busy || starting || !!pendingId;
  const ready = prepared.candidate.preview_ready && !!prepared.candidate.preview_url && sameNextSceneDraft(prepared.candidate, draft);
  const bounds = nextSceneBounds(selection.scope, selection.candidate, draft.cut_time);
  const delta = draft.cut_time - selection.scope.current_cut;
  useEffect(() => { onCutChange(draft.cut_time); }, [draft.cut_time, onCutChange]);

  useEffect(() => {
    if (!pendingId || latestJob?.id !== pendingId || !["completed", "failed", "cancelled", "interrupted"].includes(latestJob.status)) return;
    const result = nextScenePreview(latestJob, selection.jobId);
    setPendingId(null);
    if (result?.candidate.id === selection.candidate.id && result.candidate.preview_ready && result.candidate.preview_url) {
      const candidate = result.candidate;
      setDraft({ source_start: candidate.incoming.source_start, cut_time: candidate.cut, crop: candidate.incoming.crop ?? null });
      setPrepared({ candidate, previewJobId: latestJob.id });
      setPlaybackFailed(false);
      setError("");
      setView("play");
    } else {
      setError(result?.candidate.preview_error || latestJob.error || (latestJob.status === "cancelled" ? "Preview cancelled. Your adjustments are still here." : "The preview could not be prepared. Your adjustments are still here; try again."));
    }
  }, [pendingId, latestJob, selection.jobId, selection.candidate.id]);

  async function prepare() {
    if (disabled || suspended || request.current) return;
    request.current = true;
    setStarting(true);
    setError("");
    try {
      const job = await onPrepare(selection.candidate.id, { source_start: draft.source_start, cut_time: draft.cut_time, crop: draft.crop });
      if (mounted.current) {
        if (job) setPendingId(job.id);
        else setError("The preview did not start. Your adjustments are still here; try again.");
      }
    } finally { request.current = false; if (mounted.current) setStarting(false); }
  }
  function change(next: NextSceneAdjustment) {
    if (disabled || suspended) return;
    setDraft(next);
    setError("");
  }
  function changeCut(value: number) {
    const next = nextSceneBounds(selection.scope, selection.candidate, value);
    change({ ...draft, cut_time: next.cut, source_start: Math.max(next.sourceMin, Math.min(next.sourceMax, draft.source_start)) });
  }

  return <section className={`${player.player} ${styles.audition}`} aria-label={`Option ${selection.option} scene editor`}>
    <div className={player.heading}>
      <span>Option {selection.option} · {view === "adjust" ? "Adjust scene" : "Preview with music"}</span>
      <button className={styles.close} onClick={onClose} aria-label="Back to edit" title="Back to edit; unprepared adjustments are discarded"><EditorIcon name="close" size={14} /></button>
    </div>
    {view === "adjust" ? <SourceSceneEditor
      filmId={selection.candidate.incoming.film_id}
      range={{ start: selection.candidate.incoming_authority.t_start, end: selection.candidate.incoming_authority.t_end }}
      duration={selection.scope.t2 - draft.cut_time} value={draft} outputRatio={outputRatio}
      disabled={disabled} suspended={suspended}
      onChange={(value) => change({ ...draft, ...value })}
      onTimeChange={(time) => onTimeChange(draft.cut_time + time - draft.source_start)}
    /> : <NextScenePairPlayer
      selection={{ ...selection, candidate: prepared.candidate }} suspended={suspended || disabled}
      onTimeChange={onTimeChange} onFailure={() => setPlaybackFailed(true)}
    />}
    <div className={styles.editorActions}>
      {view === "play" ? <button disabled={disabled || suspended} onClick={() => setView("adjust")}>Adjust scene</button>
        : <button disabled={disabled || suspended} onClick={() => { if (ready && !playbackFailed) setView("play"); else void prepare(); }}>Preview with music</button>}
      <button className={styles.use} disabled={disabled || suspended || !ready || playbackFailed} onClick={() => onApply(selection.candidate.id, { ...draft, ...(prepared.previewJobId ? { preview_job_id: prepared.previewJobId } : {}) })}>Use scene</button>
    </div>
    <div className={styles.scope}>
      <span>{seconds(selection.scope.t0 - selection.scope.passage_start)} → {seconds(selection.scope.t2 - selection.scope.passage_start)} in the edit · {Math.abs(delta) < 0.001 ? "Original cut timing" : `Cut ${Math.abs(delta).toFixed(2)}s ${delta > 0 ? "later" : "earlier"}`}</span>
      <p>{!ready ? "Preview with music prepares your adjustments before Use scene." : "Use scene changes only this pair. Undo restores it."} Closing or switching options discards unprepared adjustments.</p>
    </div>
    <details className={styles.timing}>
      <summary>More · cut timing</summary>
      <label>Cut after anchor <output>{seconds(draft.cut_time - selection.scope.t0)}</output>
        <input type="range" aria-label="Cut after anchor" min={bounds.cutMin} max={bounds.cutMax} step="any" value={draft.cut_time} disabled={disabled || suspended || bounds.cutMax <= bounds.cutMin} onChange={(event) => { setView("adjust"); changeCut(Number(event.target.value)); }} />
      </label>
      <p>{bounds.cutMax <= bounds.cutMin ? "This pair’s cut is fixed." : "Only the cut between these two scenes moves; music and later scenes stay in place."}</p>
      <button disabled={disabled || suspended} onClick={() => void prepare()}>Rebuild preview</button>
    </details>
    {(disabled && (starting || pendingId)) && <div className={styles.progress} role="status"><span>{latestJob?.id === pendingId ? latestJob.progress || "Preparing preview with music…" : "Starting preview…"}</span>{pendingId && <button onClick={onCancel}>Cancel preview</button>}</div>}
    {(error || (!ready && prepared.candidate.preview_error)) && <p className={player.error} role="alert">{error || prepared.candidate.preview_error}</p>}
  </section>;
}
