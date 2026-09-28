"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { formatBytes } from "./model";
import type { Acquisition, SubtitleDecision } from "./types";
import styles from "./acquisition.module.css";

interface Props {
  item: Acquisition;
  busy: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (body: { revision: number; video_path: string; subtitle_decision: SubtitleDecision | null }) => Promise<boolean>;
}

export default function AcquisitionReview({ item, busy, error, onClose, onSubmit }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [video, setVideo] = useState(item.review?.selected_video || (item.review?.videos.length === 1 ? item.review.videos[0].relative_path : ""));
  const [subtitle, setSubtitle] = useState<SubtitleDecision>({ action: "auto" });
  const [validation, setValidation] = useState<string | null>(null);
  const review = item.review;
  const changedVideo = Boolean(review?.selected_video && video !== review.selected_video);
  const needsVideoCheck = !review?.selected_video || changedVideo;
  const subtitles = needsVideoCheck ? [] : review?.subtitles ?? [];

  useEffect(() => {
    const dialog = dialogRef.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    if (!video) { setValidation("Choose the main film to add."); return; }
    setValidation(null);
    await onSubmit({ revision: item.revision, video_path: video, subtitle_decision: subtitle });
  }

  return (
    <dialog ref={dialogRef} className="film-review-dialog" aria-labelledby="acquisition-review-title" aria-describedby="acquisition-review-description"
      aria-busy={busy} onCancel={(event) => { if (busy) event.preventDefault(); }}
      onClose={(event) => {
        // Strict Mode reopens the dialog after effect cleanup. Its queued close
        // event must not dismiss the review that is already open again.
        if (!busy && !event.currentTarget.open) onClose();
      }}>
      <form className="film-review-form" onSubmit={(event) => void submit(event)}>
        <div className="film-review-heading"><div><p className="film-review-eyebrow">Review downloaded film</p><h2 id="acquisition-review-title">{item.title} ({item.year})</h2></div>
          <button type="button" className="film-review-close" disabled={busy} onClick={onClose} aria-label="Close downloaded film review">×</button>
        </div>
        <p id="acquisition-review-description" className={styles.note}>{needsVideoCheck ? "Choose the main film. We’ll check its subtitles next." : "Confirm the main film. Subtitles are checked automatically unless you choose a track or skip them."}</p>
        <fieldset className="film-review-subtitles" disabled={busy}>
          <legend>Main film</legend>
          <div className="film-review-subtitle-options">{review?.videos.map((candidate) => (
            <label className="film-review-subtitle-option" key={candidate.relative_path}>
              <input type="radio" name="acquisition-video" value={candidate.relative_path} checked={video === candidate.relative_path}
                onChange={() => { setVideo(candidate.relative_path); setSubtitle({ action: "auto" }); }} required />
              <span><strong>{candidate.name}</strong><small>{candidate.relative_path} · {formatBytes(candidate.size)}</small></span>
            </label>
          ))}</div>
        </fieldset>
        {needsVideoCheck && <p className={styles.note}>You’ll return to the queue while we check. If a subtitle choice is needed, Review will appear again.</p>}
        {subtitles.length > 0 && <fieldset className="film-review-subtitles" disabled={busy}>
          <legend>English subtitles</legend><p>We check English, timestamps and dialogue coverage before using a track.</p>
          <div className="film-review-subtitle-options">
            <label className="film-review-subtitle-option"><input type="radio" name="acquisition-subtitle" value="auto" required checked={subtitle.action === "auto"}
              onChange={() => setSubtitle({ action: "auto" })} /><span><strong>Automatic</strong><small>Use a validated English track when there’s one clear choice. Otherwise, use embedded subtitles or transcribe the dialogue.</small></span></label>
            {subtitles.map((candidate) => (
            <label className="film-review-subtitle-option" key={candidate.relative_path}>
              <input type="radio" name="acquisition-subtitle" value={candidate.relative_path} required checked={subtitle.action === "use" && subtitle.relative_path === candidate.relative_path}
                onChange={() => setSubtitle({ action: "use", relative_path: candidate.relative_path })} />
              <span><strong>{candidate.relative_path}</strong><small>{candidate.excerpt}</small>{candidate.validation && <small>{candidate.validation}</small>}</span>
            </label>
          ))}
            <label className="film-review-subtitle-option"><input type="radio" name="acquisition-subtitle" value="skip" required checked={subtitle.action === "skip"}
              onChange={() => setSubtitle({ action: "skip" })} /><span><strong>None of these</strong><small>We can use embedded subtitles or transcribe the film’s dialogue.</small></span></label>
          </div>
        </fieldset>}
        {(validation || error) && <p className="film-review-error" role="alert">{validation || error}</p>}
        <div className="film-review-actions"><button type="button" className="films-button films-button--quiet" disabled={busy} onClick={onClose}>Back</button>
          <button type="submit" className="films-button films-button--primary" disabled={busy || !review?.videos.length}>{busy ? "Saving…" : needsVideoCheck ? "Check subtitles" : "Add film"}</button>
        </div>
      </form>
    </dialog>
  );
}
