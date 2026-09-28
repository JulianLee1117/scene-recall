"use client";

import { useEffect, useState, type ReactNode } from "react";
import type { LabJob, LabProject, WorkerStatus } from "@/types/lab";
import { labRequest } from "@/lib/lab";
import EditorPopover from "./EditorPopover";
import TimingPlanDetails from "./TimingPlanDetails";
import FootageInspectionDetails from "./FootageInspectionDetails";
import { timingPlanForJob } from "./timingPlan";
import { elapsedLabel, isJobActive, jobCounts, jobElapsed, jobName, jobStateLabel, jobSteps, jobSummary, queuedWorkerSummary } from "./jobProgress";
import styles from "./jobStatus.module.css";

export default function JobStatus({
  job,
  onCancel,
  notice,
  actions,
  compact = false,
  savedProject,
}: {
  job: LabJob;
  onCancel: () => void;
  notice?: string;
  actions?: ReactNode;
  compact?: boolean;
  savedProject?: Pick<LabProject, "id" | "revision" | "document">;
}) {
  const active = isJobActive(job);
  const [now, setNow] = useState<number | null>(null);
  const [workers, setWorkers] = useState<WorkerStatus | null>(null);
  useEffect(() => {
    setWorkers(null);
    if (job.status !== "queued") return;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const status = await labRequest<WorkerStatus>("/workers", { signal: abort.signal });
        if (!abort.signal.aborted) setWorkers(status);
      } catch {
        if (!abort.signal.aborted) setWorkers(null);
      } finally {
        if (!abort.signal.aborted) timer = setTimeout(refresh, 5000);
      }
    }
    void refresh();
    return () => { abort.abort(); clearTimeout(timer); };
  }, [job.id, job.status]);
  useEffect(() => {
    setNow(Date.now());
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [job.id, active]);
  const elapsed = now === null && active ? null : jobElapsed(job, now ?? 0);
  const steps = jobSteps(job);
  const queueSummary = queuedWorkerSummary(job, workers);
  const summary = queueSummary ?? jobSummary(job, notice);
  const state = jobStateLabel(job);
  const failure = job.status === "failed" || job.status === "interrupted";
  const timing = timingPlanForJob(job, savedProject);

  return <section className={`${styles.status} ${compact ? styles.compact : ""}`} aria-label="Task progress" data-state={failure ? "failed" : active ? "active" : "done"}>
    <span className={`${styles.dot} ${active ? styles.active : ""}`} aria-hidden="true" />
    <div className={styles.summary}>
      <div className={styles.heading}><strong>{jobName(job)}</strong><span>{state}</span>
        {timing?.final_end_frames && <span title="Shots in the final footage fit">{timing.final_end_frames.length} {timing.final_end_frames.length === 1 ? "shot" : "shots"}</span>}
      </div>
      <p role="status" title={summary}>{summary}</p>
    </div>
    <div className={styles.actions}>
      {elapsed !== null && <span className={styles.elapsed} title={job.status === "queued" ? "Time in queue" : "Elapsed time"}>{elapsedLabel(elapsed)}{job.status === "queued" ? " waiting" : ""}</span>}
      <EditorPopover key={job.id} title="Task details" label={failure ? "Error details" : "Details"} triggerLabel={failure ? "View error details" : "View task details"} triggerClassName={styles.button} width={450}>
        <div className={styles.details}>
          <dl className={styles.facts}>
            <div><dt>Task</dt><dd>{jobName(job)}</dd></div>
            <div><dt>Status</dt><dd>{state}</dd></div>
            {job.worker_role && <div><dt>Worker</dt><dd>{job.worker_role === "editor" ? "Editor" : "Library / GPU"}</dd></div>}
            {elapsed !== null && <div><dt>{job.status === "queued" ? "In queue" : "Elapsed"}</dt><dd>{elapsedLabel(elapsed)}</dd></div>}
            <div><dt>Started from</dt><dd>Saved version {job.base_revision}</dd></div>
            {jobCounts(job).map(({ label, count }) => <div key={label}><dt>{label}</dt><dd>{count}</dd></div>)}
            {job.result?.timing_mode === "source-aware" && <div><dt>Timing</dt><dd>Planned with the available footage</dd></div>}
            {job.result?.timing_mode === "fixed" && <div><dt>Timing</dt><dd>Existing cuts preserved</dd></div>}
            {job.result?.timing_mode === "measured-assembly" && <div><dt>Timing</dt><dd>Cut to measured beats and accents</dd></div>}
          </dl>
          {queueSummary?.includes("offline") && <p className={styles.outcome}>Start both workers with <code>uv run python -m pipeline.lab.worker --reload</code>.</p>}
          {!active && <p className={failure ? styles.error : styles.outcome}>{summary}</p>}
          {job.status === "interrupted" && job.error && <p className={styles.error}>{job.error}</p>}
          {job.result?.applied === false && typeof job.result.reason === "string" && <p className={styles.outcome}>{job.result.reason}</p>}
          {timing && <TimingPlanDetails plan={timing} />}
          {job.result?.footage_inspection != null && <FootageInspectionDetails value={job.result.footage_inspection} />}
          {steps.length > 0 ? <>
            <strong className={styles.stepsTitle}>Recorded steps</strong>
            <ol className={styles.steps}>{steps.map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}</ol>
          </> : <p className={styles.empty}>{active ? "The worker has not reported a step yet." : "No recorded steps are available for this task."}</p>}
          <small className={styles.identity}>Task {job.id}</small>
        </div>
      </EditorPopover>
      {active && <button type="button" className={styles.button} onClick={onCancel} disabled={job.cancel_requested}>{job.cancel_requested ? "Stopping…" : "Cancel"}</button>}
      {actions}
    </div>
  </section>;
}
