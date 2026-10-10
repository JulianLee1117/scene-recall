"use client";

import { mediaUrl, seconds } from "@/lib/lab";
import type { LabClip, MusicDirection, MusicSlot } from "@/types/lab";
import styles from "./shotExplanation.module.css";

export type ShotNeighbor = { slot: MusicSlot; clip: LabClip | null };
type Props = {
  slot: MusicSlot;
  clip: LabClip | null;
  direction: MusicDirection | null;
  query: string;
  previous?: ShotNeighbor | null;
  next?: ShotNeighbor | null;
  films: Record<string, { title: string }>;
};

const facets: Record<string, string> = { all: "Overall", scene: "Scene", words: "Dialogue & text", look: "Visual", mood: "Mood", composition: "Framing" };

/** Display saved editorial notes and retrieval evidence; never infer a rationale. */
export default function ShotExplanation({ slot, clip, direction, query, previous, next, films }: Props) {
  const evidence = slot.search_evidence;
  const requirements = [...new Set([
    ...(slot.resolved_search?.unverified_requirements ?? []),
    ...(direction?.search_plan?.unverified_requirements ?? []),
  ])];
  const neighbors = [
    { label: "Previous", value: previous },
    { label: "This shot", value: { slot, clip } },
    { label: "Next", value: next },
  ];
  return <div className={styles.explanation}>
    <div className={styles.sequence} aria-label="Current sequence context">
      {neighbors.map(({ label, value }) => {
        const frame = value?.slot.search_evidence?.matched_frame_index;
        const frameIndex = typeof frame === "number" && Number.isInteger(frame) && frame >= 0 ? frame : 0;
        const title = value?.clip ? films[value.clip.film_id]?.title || "Film title unavailable" : value ? "Unfilled scene" : "—";
        return <figure key={`${label}-${value?.clip?.id ?? "empty"}`}>
          <span>{label}</span>
          <div className={styles.thumbnail}>
            {value?.clip?.unit_id ? <img
              src={mediaUrl(`/media/keyframe/${encodeURIComponent(value.clip.unit_id)}/${frameIndex}`)}
              alt="" loading="lazy" draggable={false}
              onError={(event) => { event.currentTarget.style.visibility = "hidden"; }}
            /> : <span>{value ? "No scene" : label === "Previous" ? "Start" : "End"}</span>}
          </div>
          <figcaption title={title}>{title}</figcaption>
          {value && <small>{seconds(value.slot.start)}–{seconds(value.slot.end)}</small>}
        </figure>;
      })}
    </div>
    <p className={styles.note}>Current neighbors · thumbnails are indexed samples, not exact cut frames.</p>
    <section aria-label="Planned intention">
      <h3>Planned intention</h3>
      <dl>
        {query && <><dt>Search intent</dt><dd>{query}</dd></>}
        {direction?.purpose && <><dt>Role in the edit</dt><dd>{direction.purpose}</dd></>}
        {direction?.music_cue && <><dt>Music cue</dt><dd>{direction.music_cue}</dd></>}
        {direction?.timing_note && <><dt>Timing note</dt><dd>{direction.timing_note}</dd></>}
      </dl>
      {!query && !direction?.purpose && !direction?.music_cue && !direction?.timing_note && <p>No direction was saved for this shot.</p>}
      {slot.needs_direction && <p className={styles.note}>The direction is marked for review after edits.</p>}
    </section>
    <section aria-label="Recorded selection">
      <h3>Recorded selection</h3>
      <p>{slot.reason || "No selection explanation was saved for this shot."}</p>
      <p className={styles.note}>This note was saved when the scene was chosen; later edits may change its neighbors.</p>
    </section>
    <section aria-label="Source evidence">
      <h3>Source evidence</h3>
      {clip && <p className={styles.sourceRange}>Source {seconds(clip.source_start)}–{seconds(clip.source_end)} · {(clip.source_end - clip.source_start).toFixed(2)}s</p>}
      {clip?.title && <dl><dt>Saved clip description</dt><dd>{clip.title}</dd></dl>}
      {evidence ? <>
        <dl>
          {evidence.matched_text && <><dt>Matched indexed text{evidence.matched_text_view ? ` · ${evidence.matched_text_view}` : ""}</dt><dd>{evidence.matched_text}</dd></>}
          {typeof evidence.matched_frame_timestamp === "number" && <><dt>Matched sample</dt><dd>{seconds(evidence.matched_frame_timestamp)} in the source</dd></>}
          <dt>Search result</dt><dd>Rank {evidence.rank}</dd>
        </dl>
        {!!evidence.matches?.length && <ul>{evidence.matches.map((match, index) => {
          const detail = match.evidence && typeof match.evidence === "object" ? match.evidence as Record<string, unknown> : null;
          return <li key={index}>{typeof match.facet === "string" ? facets[match.facet] || match.facet : "Search clue"}
            {typeof detail?.text === "string" && detail.text ? ` · ${detail.text}` : ""}
            {typeof detail?.timestamp === "number" ? ` · sample ${seconds(detail.timestamp)}` : ""}
          </li>;
        })}</ul>}
        <p className={styles.note}>Indexed text and sampled frames support the search match. They do not verify every frame or movement in the placed trim.</p>
      </> : <p>No search evidence is attached to this placement.</p>}
      {requirements.length > 0 && <><h4>Check by watching</h4><ul>{requirements.map((requirement) => <li key={requirement}>{requirement}</li>)}</ul></>}
    </section>
  </div>;
}
