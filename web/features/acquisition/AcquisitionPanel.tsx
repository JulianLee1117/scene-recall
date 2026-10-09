"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { IngestJob, LibraryFilm } from "@/types/api";
import type { Acquisition } from "./types";
import AcquisitionReview from "./AcquisitionReview";
import { acquisitionHint, canCancel, cancellationNotice, filmPathKey, formatBytes, formatEta, independentJobs, isActive, isCancelling, preparationProgress, stateLabel, waitingLabel } from "./model";
import type { useAcquisitionQueue } from "./useAcquisitionQueue";
import styles from "./acquisition.module.css";

interface Props {
  queue: ReturnType<typeof useAcquisitionQueue>;
  jobs?: IngestJob[];
  films?: LibraryFilm[];
  showError?: boolean;
}

export default function AcquisitionPanel({ queue, jobs = [], films = [], showError = true }: Props) {
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [cancellationId, setCancellationId] = useState<string | null>(null);
  const reviewTrigger = useRef<HTMLButtonElement | null>(null);
  const queueHeading = useRef<HTMLHeadingElement | null>(null);
  const closeReview = useCallback(() => {
    setReviewId(null);
    window.requestAnimationFrame(() => {
      const target = reviewTrigger.current?.isConnected ? reviewTrigger.current : queueHeading.current;
      target?.focus({ preventScroll: true });
    });
  }, []);
  const visible = queue.items.filter((item) => item.status !== "ready");
  const manualJobs = independentJobs(jobs, queue.items);
  const rows = [
    ...visible.map((item) => ({ item, job: jobs.find((job) => job.job_id === item.ingest_job_id) })),
    ...manualJobs.map((job) => ({ item: null, job })),
  ].sort((a, b) => {
    const rank = (row: typeof a) => {
      if (row.item && (row.item.status === "needs_review" || row.item.status === "failed" || row.item.error)) return 0;
      if (row.item?.status === "cancelled") return 3;
      if (row.item?.status === "queued" || row.item?.status === "ingest_queued" || (!row.item && row.job?.status === "queued")) return 2;
      return 1;
    };
    return rank(a) - rank(b) || (rank(a) === 2 ? (a.job?.queue_position ?? Infinity) - (b.job?.queue_position ?? Infinity) : 0);
  });
  const review = queue.items.find((item) => item.id === reviewId && item.status === "needs_review" && !isCancelling(item) && item.review);
  const displayedNotice = cancellationId ? cancellationNotice(queue.items.find((item) => item.id === cancellationId)) : null;
  const downloader = queue.status?.downloader;

  useEffect(() => { if (reviewId && !review) closeReview(); }, [closeReview, review, reviewId]);

  function renderJob(job: IngestJob) {
    const film = films.find((film) => filmPathKey(film.path) === filmPathKey(job.path));
    const title = film?.title || job.filename.replace(/\.[^.]+$/, "");
    return (
      <article className={`film-row ${job.status === "running" ? "film-row--running" : "film-row--queued"} ${styles.row}`} key={job.job_id}>
        <div className="film-row-copy">
          <details className={styles.itemDetails}>
            <summary><h3 className={styles.rowHeading}><span className={styles.rowTitle}>{title}</span><span className={styles.state}>{job.status === "running" ? "Preparing for search" : waitingLabel(job)}</span></h3></summary>
            <div className={styles.detailsBody}>
              {job.status === "running" && <p className={styles.note}>{preparationProgress(job.progress, job.log)}</p>}
              <p>{job.progress || job.filename}</p>
            </div>
          </details>
        </div>
      </article>
    );
  }

  function renderAcquisition(item: Acquisition) {
    const job = jobs.find((job) => job.job_id === item.ingest_job_id);
    const progress = item.progress != null && Number.isFinite(item.progress) ? Math.max(0, Math.min(1, item.progress)) : null;
    const eta = formatEta(item.eta_seconds);
    const hint = acquisitionHint(item, job);
    const showHintCollapsed = item.status === "downloading" && Boolean(hint);
    const label = isCancelling(item) ? stateLabel("cancelling") : item.status === "ingest_queued" ? waitingLabel(job) : stateLabel(item.status);
    return (
      <article className={`film-row ${styles.row} ${item.status === "failed" ? "film-row--failed" : isActive(item) ? "film-row--queued" : ""}`} key={item.id}>
        <div className="film-row-copy">
          <details className={styles.itemDetails}>
            <summary><h3 className={styles.rowHeading}><span className={styles.rowTitle}>{item.title} ({item.year}){item.edition ? ` · ${item.edition}` : ""}</span>
              <span className={styles.state}>{label}{item.status === "downloading" && progress !== null ? ` · ${Math.round(progress * 100)}%` : ""}</span></h3></summary>
            <div className={styles.detailsBody}>
              {item.status === "downloading" && <>
                <progress className={styles.progress} value={progress ?? undefined} max={1} aria-label={`${item.title} download progress`} />
                <p className="film-meta">{item.bytes_done != null ? `${formatBytes(item.bytes_done)} / ` : ""}{formatBytes(item.bytes_total)}{eta ? ` · ${eta}` : ""}</p>
              </>}
              {hint && !showHintCollapsed && <p className={styles.note}>{hint}</p>}
              {item.film_path && <p>{item.film_path}</p>}
              {item.message && <p>{item.message}</p>}
              {job?.progress && <p>{job.progress}</p>}
              {item.status === "downloading" && item.download_rate != null && <p>{formatBytes(item.download_rate)}/s</p>}
            </div>
          </details>
          {showHintCollapsed && <p className={styles.note}>{hint}</p>}
          {item.error && <p className="film-row-error">{item.error}</p>}
        </div>
        <div className={styles.rowActions}>
          {item.status === "needs_review" && item.review && !isCancelling(item) && <button type="button" className="films-button films-button--secondary" disabled={Boolean(queue.busy)}
            onClick={(event) => { reviewTrigger.current = event.currentTarget; setReviewId(item.id); }}>Review</button>}
          {["failed", "cancelled"].includes(item.status) && !isCancelling(item) && <button type="button" className="films-button films-button--secondary" disabled={Boolean(queue.busy)}
            onClick={() => { setCancellationId(null); void queue.execute(item.id, `/${encodeURIComponent(item.id)}/retry`, { revision: item.revision }); }}>Try again</button>}
          {["failed", "cancelled"].includes(item.status) && !isCancelling(item) && <button type="button" className="films-button films-button--quiet" disabled={Boolean(queue.busy)}
            title="Forget this acquisition and free its release for a fresh download."
            onClick={() => { setCancellationId(null); void queue.execute(item.id, `/${encodeURIComponent(item.id)}/dismiss`, { revision: item.revision }); }}>Dismiss</button>}
          {canCancel(item) && <button type="button" className="films-button films-button--quiet" disabled={Boolean(queue.busy)}
            title="Stop this acquisition and remove its downloaded files. Imported films and library assets are kept."
            onClick={() => {
              setCancellationId(null);
              void queue.execute(item.id, `/${encodeURIComponent(item.id)}/cancel`, { revision: item.revision }).then((accepted) => {
                if (accepted) setCancellationId(item.id);
              });
            }}>Cancel</button>}
        </div>
      </article>
    );
  }

  return (
    <section aria-labelledby="acquisition-heading">
      <h2 className={styles.srOnly} ref={queueHeading} tabIndex={-1} id="acquisition-heading">Film queue</h2>

      {queue.error && !review && showError && <p className="films-message films-message--error" role="alert">{queue.error}</p>}
      {displayedNotice && <p className={styles.notice} role="status">{displayedNotice}</p>}
      {queue.status && (!downloader?.available || !queue.status.monitor.running) && (
        <div className={styles.connection} role="status">
          {!downloader?.configured ? "Download setup is needed before adding films online." : !downloader.available ? "The downloader is unavailable. Check Download settings below." : "Downloads are waiting for the queue monitor to start. See Download settings below."}
        </div>
      )}

      <div className={styles.queue}>
        {queue.loading && manualJobs.length === 0 ? <p className="films-empty">Loading film queue…</p>
          : rows.length === 0 ? <p className="films-empty">No films in progress. Use Add films to get started.</p>
          : <div className="films-list">{rows.map(({ item, job }) => item ? renderAcquisition(item) : renderJob(job!))}</div>}
      </div>

      <details className={styles.setup}>
        <summary>Download settings</summary>
        {queue.status && <p>Downloader: {queue.status.downloader.available ? "connected" : "unavailable"} · Search: {queue.status.search.configured ? "configured" : "not configured"} · Monitor: {queue.status.monitor.running ? "running" : "stopped"}</p>}
        <details><summary>Connection instructions</summary>
          <p>Enable qBittorrent’s Web UI on this computer and set <code>QBITTORRENT_URL</code>, <code>QBITTORRENT_USERNAME</code>, and <code>QBITTORRENT_PASSWORD</code> in the server’s <code>.env</code>.</p>
          <p>For release search, connect indexers in Prowlarr and set <code>PROWLARR_URL</code> and <code>PROWLARR_API_KEY</code>. Credentials stay on the server.</p>
          <p>Start downloads with <code>uv run python -m pipeline.acquisition.worker</code>. Keep <code>uv run python -m pipeline.lab.worker</code> running to prepare films for search.</p>
        </details>
      </details>

      {review && <AcquisitionReview key={`${review.id}:${review.revision}`} item={review} busy={Boolean(queue.busy)} error={queue.error}
        onClose={closeReview} onSubmit={async (body) => {
          const success = await queue.execute(review.id, `/${encodeURIComponent(review.id)}/review`, body);
          if (success) closeReview();
          return success;
        }} />}
    </section>
  );
}

