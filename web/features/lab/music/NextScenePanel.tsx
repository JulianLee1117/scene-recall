"use client";

import { useEffect, useState, type FormEvent } from "react";
import { mediaUrl, seconds } from "@/lib/lab";
import type {
  LabJob, NextSceneCandidate, NextSceneOptions, NextSceneScope,
} from "@/types/lab";
import EditorIcon from "@/features/lab/kit/EditorIcon";
import { nextScenePair, nextScenePreview, nextSceneResult } from "./nextScene";
import styles from "./nextScene.module.css";

export type NextSceneAuditionSelection = {
  candidate: NextSceneCandidate;
  scope: NextSceneScope;
  option: number;
  jobId: string;
  revision: number;
  previewJobId?: string;
  initialView: "play" | "adjust";
};
type Prepared = { candidate: NextSceneCandidate; jobId: string };
type Props = {
  pair: ReturnType<typeof nextScenePair>;
  job: LabJob | null;
  latestJob: LabJob | null;
  fresh: boolean;
  disabled: boolean;
  films: Record<string, { title: string }>;
  passageStart: number;
  onFind: (options: NextSceneOptions) => void;
  onAudition: (selection: NextSceneAuditionSelection) => void;
  onStopAudition: () => void;
};

export default function NextScenePanel({
  pair, job, latestJob, fresh, disabled, films, passageStart,
  onFind, onAudition, onStopAudition,
}: Props) {
  const [intent, setIntent] = useState("");
  const [flexible, setFlexible] = useState(false);
  const [inspectFrames, setInspectFrames] = useState(false);
  const [prepared, setPrepared] = useState<Record<string, Prepared>>({});
  const result = nextSceneResult(job);
  const scopeMatches = result?.scope.anchor_slot_id === pair.anchorSlot?.id &&
    result?.scope.next_slot_id === pair.nextSlot?.id;
  const candidates = scopeMatches ? result?.candidates.slice(0, 3) ?? [] : [];
  const busyHere = disabled && (latestJob?.kind === "next-scene" || latestJob?.kind === "next-scene-preview");

  useEffect(() => setPrepared({}), [job?.id]);
  useEffect(() => {
    if (!job || !latestJob) return;
    const preview = nextScenePreview(latestJob, job.id);
    if (preview) setPrepared((current) => ({
      ...current, [preview.candidate.id]: { candidate: preview.candidate, jobId: latestJob.id },
    }));
  }, [job?.id, latestJob?.id, latestJob?.status, latestJob?.result]);

  function find(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled || pair.problem || !pair.anchorSlot) return;
    onStopAudition();
    onFind({
      anchor_slot_id: pair.anchorSlot.id,
      intent: intent.trim(),
      flexible_cut: flexible && pair.canFlex,
      inspect_frames: inspectFrames,
    });
  }

  return (
    <section className={styles.panel} aria-label="Find next scene">
      {pair.anchor && pair.anchorSlot && pair.nextSlot && !pair.problem ? (
        <div className={styles.anchor}>
          <EditorIcon name="film" />
          <div>
            <strong>After {films[pair.anchor.film_id]?.title || pair.anchor.title || "this scene"}</strong>
            <span>
              {seconds(pair.anchorSlot.start - passageStart)} → {seconds(pair.nextSlot.end - passageStart)} in the edit
              {pair.next ? " · replaces the following scene" : " · fills the next placeholder"}
            </span>
          </div>
        </div>
      ) : <p className={styles.note}>{pair.problem}</p>}
      <form onSubmit={find} className={styles.form}>
        <label>
          Where should it go next? <span className={styles.optional}>Optional</span>
          <textarea
            aria-label="Direction for the next scene"
            rows={2}
            maxLength={600}
            value={intent}
            disabled={disabled || !!pair.problem}
            placeholder="Carry the isolation into a wider image…"
            onChange={(event) => setIntent(event.target.value)}
          />
        </label>
        {!pair.problem && !pair.canFlex && <p className={styles.note}>
          {pair.anchor?.locked ? "The anchor is locked, so the cut stays fixed." : "The following scene is filled, so the cut stays fixed."}
        </p>}
        <label className={styles.check}>
          <input
            type="checkbox"
            checked={flexible && pair.canFlex}
            disabled={disabled || !pair.canFlex}
            onChange={(event) => setFlexible(event.target.checked)}
          />
          Flexible cut
          <span title="Only available when the anchor is unlocked and the following placeholder is empty.">Up to 2s</span>
        </label>
        <details className={styles.more}>
          <summary>More</summary>
          <label className={styles.check}>
            <input
              type="checkbox"
              checked={inspectFrames}
              disabled={disabled || !!pair.problem}
              onChange={(event) => setInspectFrames(event.target.checked)}
            />
            Inspect sampled frames
          </label>
          <p>Experimental; requires OpenAI and adds image processing. Samples help assess appearance, but do not verify continuous motion or an adjusted trim.</p>
        </details>
        <button type="submit" className={styles.find} disabled={disabled || !!pair.problem}>
          <EditorIcon name="search" />
          {busyHere ? "Working…" : "Find next scene"}
        </button>
      </form>
      {busyHere && <p className={styles.note} role="status">{latestJob?.progress || "Preparing alternatives and previews…"}</p>}
      {!!candidates.length && !fresh && (
        <p className={styles.warning} role="status">Your edit changed. Find new alternatives before previewing or applying these scenes.</p>
      )}
      {!!candidates.length && fresh && <p className={styles.note}>Play each option with the music. Your edit changes only when you use a scene.</p>}
      {job && result && scopeMatches && candidates.length === 0 && (
        <p className={styles.note}>No suitable next scene was found. Try another direction or a broader film selection.</p>
      )}
      <div className={styles.choices}>
        {job && result && candidates.map((candidate, index) => (
          <NextSceneChoice
            key={`${job.id}-${candidate.id}`}
            candidate={candidate}
            prepared={prepared[candidate.id]}
            scope={result.scope}
            option={index + 1}
            title={candidate.film_title || films[candidate.incoming.film_id]?.title || candidate.incoming.title || "Next scene"}
            disabled={disabled || !fresh}
            onOpen={(played, initialView, previewJobId) => onAudition({
              candidate: played, scope: result.scope, option: index + 1,
              jobId: job.id, revision: job.base_revision, previewJobId, initialView,
            })}
          />
        ))}
      </div>
    </section>
  );
}

function NextSceneChoice({ candidate, prepared, scope, option, title, disabled, onOpen }: {
  candidate: NextSceneCandidate;
  prepared?: Prepared;
  scope: NextSceneScope;
  option: number;
  title: string;
  disabled: boolean;
  onOpen: (candidate: NextSceneCandidate, view: "play" | "adjust", previewJobId?: string) => void;
}) {
  const ready = prepared?.candidate.preview_ready ? prepared : { candidate, jobId: undefined };
  const current = ready.candidate;
  const canPlay = current.preview_ready && !!current.preview_url;
  const delta = current.cut - scope.current_cut;
  const matchedFrame = candidate.search_evidence?.matched_frame_index;
  const frame = typeof matchedFrame === "number" && Number.isInteger(matchedFrame) && matchedFrame >= 0 ? matchedFrame : 0;
  const thumbnail = candidate.incoming.unit_id ? `/media/keyframe/${encodeURIComponent(candidate.incoming.unit_id)}/` : null;
  return (
    <article className={styles.choice} aria-label={`Next scene option ${option}`}>
      <div className={styles.choiceTop}>
        <button className={styles.thumbnail} disabled={disabled}
          onClick={() => onOpen(current, canPlay ? "play" : "adjust", ready.jobId)}
          aria-label={`${canPlay ? "Play" : "Adjust"} option ${option}`}>
          {thumbnail && <img src={mediaUrl(`${thumbnail}${frame}`)} alt="" draggable={false} onError={(event) => {
            const fallback = mediaUrl(`${thumbnail}0`);
            if (frame && event.currentTarget.getAttribute("src") !== fallback) event.currentTarget.src = fallback;
            else event.currentTarget.style.visibility = "hidden";
          }} />}
          <span><EditorIcon name="play" size={14} /> {option}</span>
        </button>
        <div>
          <strong>{title}</strong>
          <p>{candidate.reason || candidate.incoming.title}</p>
          <small>{Number((scope.t2 - current.cut).toFixed(2))}s next scene · {Math.abs(delta) < 0.001 ? "cut stays" : `${Math.abs(delta).toFixed(2)}s ${delta > 0 ? "later" : "earlier"} cut`}</small>
        </div>
      </div>
      <div className={styles.choiceActions}>
        <button disabled={disabled || !canPlay} onClick={() => onOpen(current, "play", ready.jobId)}>Play</button>
        <button disabled={disabled} onClick={() => onOpen(current, "adjust", ready.jobId)}>Adjust scene</button>
      </div>
      {!!candidate.unverified_requirements?.length && <details className={styles.more}>
        <summary>What to check while playing</summary>
        <p>{candidate.unverified_requirements.join(" · ")}</p>
      </details>}
      {current.preview_error && !canPlay && <p className={styles.warning}>{current.preview_error} Open Adjust scene to rebuild the preview.</p>}
    </article>
  );
}
