import { seconds } from "@/lib/lab";
import { timingFrameTime, type TimingDiagnostics, type TimingPlanReceipt } from "./timingPlan";
import styles from "./jobStatus.module.css";

function fitSummary(count: number, fit: TimingDiagnostics | null) {
  return <>{count} {count === 1 ? "shot" : "shots"}{fit && <small className={styles.timingRange}>
    {fit.shortest_seconds.toFixed(2)}–{fit.longest_seconds.toFixed(2)}s · {fit.average_seconds_selected.toFixed(2)}s average
    {fit.uniform_timing ? " · even lengths" : ""}
  </small>}</>;
}

export default function TimingPlanDetails({ plan }: { plan: TimingPlanReceipt }) {
  return <section className={styles.timing} aria-label="Recorded timing plan">
    <strong className={styles.stepsTitle}>Music-led timing</strong>
    <dl className={styles.facts}>
      <div><dt>Initial plan</dt><dd>{fitSummary(plan.end_frames.length, plan.nominal_timing)}</dd></div>
      {plan.final_end_frames && <div><dt>Final footage fit</dt><dd>{fitSummary(plan.final_end_frames.length, plan.final_timing)}</dd></div>}
    </dl>
    {plan.notes.length > 0 && <details className={styles.timingDisclosure}>
      <summary>Timing notes · {plan.notes.length}</summary>
      <p className={styles.empty}>Recorded during planning. Times refer to the initial plan in the original song; footage fitting may move the final cuts.</p>
      <ol className={styles.timingNotes}>{plan.notes.map((note, i) => <li key={i}>
        <span>{seconds(timingFrameTime(plan, note.start_frame))} – {seconds(timingFrameTime(plan, note.end_frame))}</span>
        <p>{note.reason}</p>
        {note.evidence_ids.length > 0 && <small>Evidence IDs: {note.evidence_ids.join(", ")}</small>}
      </li>)}</ol>
    </details>}
    <details className={styles.timingDisclosure}>
      <summary>Timing receipt</summary>
      <dl className={styles.facts}>
        <div><dt>Passage</dt><dd>{seconds(plan.passage.start)} – {seconds(plan.passage.end)} · {plan.fps} fps</dd></div>
        <div><dt>Artifact</dt><dd>{plan.artifact_id}</dd></div>
        <div><dt>Contract</dt><dd>{plan.contract}</dd></div>
        <div><dt>Track</dt><dd>{plan.track_id}</dd></div>
        {plan.cache_reused !== null && <div><dt>Plan reuse</dt><dd>{plan.cache_reused ? "Reused saved timing plan" : "New timing plan"}</dd></div>}
        <div><dt>Initial end frames</dt><dd>{plan.end_frames.join(", ")}</dd></div>
        {plan.final_end_frames && <div><dt>Final end frames</dt><dd>{plan.final_end_frames.join(", ")}</dd></div>}
      </dl>
      <p className={styles.empty}>Frame positions are relative to the selected passage’s start.</p>
    </details>
  </section>;
}
