import { seconds } from "@/lib/lab";
import { readFootageInspection } from "./footageInspection";
import styles from "./footageInspection.module.css";

const STATUS = { completed: "Complete", partial: "Partial coverage", unavailable: "Unavailable", "not-needed": "Not needed" };

export default function FootageInspectionDetails({ value }: { value: unknown }) {
  const inspection = readFootageInspection(value);
  if (!inspection) return null;
  const coverage = inspection.inspected_count !== null && inspection.eligible_count !== null;
  return <section className={styles.section} aria-label="Footage inspection">
    <div className={styles.heading}><strong>Footage inspection</strong><span>{STATUS[inspection.status]}</span></div>
    <p className={styles.coverage}>{inspection.status === "not-needed" && inspection.eligible_count === 0
      ? "No clips were flagged for inspection."
      : coverage ? <>{inspection.inspected_count} of {inspection.eligible_count} flagged clips inspected
        {inspection.changed_count !== null && <> · {inspection.changed_count} changed</>}</>
      : "Inspection coverage was not recorded."}</p>
    <p className={styles.limit}>Sampled evidence, not continuous playback.</p>
    {inspection.warning && <p className={styles.warning}>{inspection.warning}</p>}
    {inspection.targets.length > 0 && <details className={styles.disclosure}>
      <summary>Findings by clip · {inspection.targets.length}</summary>
      <div className={styles.targets}>{inspection.targets.map((target, index) => <details className={styles.disclosure} key={`${target.slot_id}-${index}`}>
        <summary>Clip {target.position} · {target.hint === "action_timing" ? "Action timing" : "Visual fit"} · {target.status === "unavailable" ? "Unavailable" : target.changed === true ? "Changed" : target.changed === false ? "Kept" : "Reviewed"}</summary>
        {target.reason && <p className={styles.reason}>{target.reason}</p>}
        {target.observations.map((observation, i) => <div className={styles.observation} key={`${observation.artifact_id}-${i}`}>
          {target.observations.length > 1 && <small>Sample window {i + 1}</small>}
          {observation.summary && <p>{observation.summary}</p>}
          {observation.uncertainty && <p className={styles.uncertainty}>Uncertainty: {observation.uncertainty}</p>}
          {observation.events.length > 0 && <ul className={styles.events}>{observation.events.map((event, index) => <li key={index}>
            <span>Source {seconds(event.start)} – {seconds(event.end)}</span>
            <dl>
              {event.before && <div><dt>Before</dt><dd>{event.before}</dd></div>}
              {event.completion && <div><dt>Completion</dt><dd>{event.completion}</dd></div>}
              {event.after && <div><dt>After</dt><dd>{event.after}</dd></div>}
            </dl>
          </li>)}</ul>}
          <small className={styles.identity}>Evidence {observation.artifact_id} · Source {observation.unit_id}</small>
        </div>)}
      </details>)}</div>
    </details>}
    {(inspection.window_count !== null || inspection.cache_hits !== null) && <small className={styles.windows}>
      {inspection.window_count !== null && <>{inspection.window_count} sample windows</>}
      {inspection.window_count !== null && inspection.cache_hits !== null && " · "}
      {inspection.cache_hits !== null && <>{inspection.cache_hits} reused from cache</>}
    </small>}
  </section>;
}
