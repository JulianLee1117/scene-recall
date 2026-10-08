"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { IngestJob, LibraryFilm } from "@/types/api";
import type { Acquisition } from "./types";
import AddFilmForm from "./AddFilmForm";
import AcquisitionReview from "./AcquisitionReview";
import { acquisitionHint, canCancel, cancellationNotice, filmPathKey, formatBytes, formatEta, independentJobs, isActive, isCancelling, preparationProgress, stateLabel, waitingLabel } from "./model";
import { useAcquisitionQueue } from "./useAcquisitionQueue";
import styles from "./acquisition.module.css";

interface Props {
  onLibraryChange: () => Promise<unknown>;
  jobs?: IngestJob[];
  films?: LibraryFilm[];
  onItemsChange?: (items: Acquisition[]) => void;
  onRefresh?: () => Promise<void>;
}

export default function AcquisitionPanel({ onLibraryChange, jobs = [], films = [], onItemsChange, onRefresh }: Props) {
  const queue = useAcquisitionQueue(onLibraryChange);
  const [adding, setAdding] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
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
  const running = manualJobs.filter((job) => job.status === "running");
  const waiting = manualJobs.filter((job) => job.status === "queued")
    .sort((a, b) => (a.queue_position ?? Infinity) - (b.queue_position ?? Infinity));
  const activeCount = queue.items.filter(isActive).length + manualJobs.length;
  const review = queue.items.find((item) => item.id === reviewId && item.status === "needs_review" && !isCancelling(item) && item.review);
  const displayedNotice = notice ?? (cancellationId ? cancellationNotice(queue.items.find((item) => item.id === cancellationId)) : null);
  const downloader = queue.status?.downloader;

  useEffect(() => { onItemsChange?.(queue.items); }, [onItemsChange, queue.items]);
  useEffect(() => { if (reviewId && !review) closeReview(); }, [closeReview, review, reviewId]);

  function renderJob(job: IngestJob) {
    const film = films.find((film) => filmPathKey(film.path) === filmPathKey(job.path));
    const title = film?.title || job.filename.replace(/\.[^.]+$/, "");
    return (
      <article className={`film-row ${job.status === "running" ? "film-row--running" : "film-row--queued"} ${styles.row}`} key={job.job_id}>
        <div className="film-row-copy">
          <div className={styles.rowHeading}><h3>{title}</h3><span className={styles.state}>{job.status === "running" ? "Preparing for search" : waitingLabel(job)}</span></div>
          {job.status === "running" && <p className={styles.note}>{preparationProgress(job.progress, job.log)}</p>}
          {job.progress && <details className={styles.details}><summary>Details</summary><p>{job.progress}</p></details>}
        </div>
      </article>
    );
  }

  function renderAcquisition(item: Acquisition) {
    const job = jobs.find((job) => job.job_id === item.ingest_job_id);
    const progress = item.progress != null && Number.isFinite(item.progress) ? Math.max(0, Math.min(1, item.progress)) : null;
    const eta = formatEta(item.eta_seconds);
    const hint = acquisitionHint(item, job);
    const label = isCancelling(item) ? stateLabel("cancelling") : item.status === "ingest_queued" ? waitingLabel(job) : stateLabel(item.status);
    return (
      <article className={`film-row ${styles.row} ${item.status === "failed" ? "film-row--failed" : isActive(item) ? "film-row--queued" : ""}`} key={item.id}>
        <div className="film-row-copy">
          <div className={styles.rowHeading}><h3>{item.title} ({item.year}){item.edition ? ` · ${item.edition}` : ""}</h3>
            <span className={styles.state}>{label}{item.status === "downloading" && progress !== null ? ` · ${Math.round(progress * 100)}%` : ""}</span></div>
          {item.status === "downloading" && <>
            <progress className={styles.progress} value={progress ?? undefined} max={1} aria-label={`${item.title} download progress`} />
            <p className="film-meta">{item.bytes_done != null ? `${formatBytes(item.bytes_done)} / ` : ""}{formatBytes(item.bytes_total)}{eta ? ` · ${eta}` : ""}</p>
          </>}
          {hint && <p className={styles.note}>{hint}</p>}
          {item.error && <p className="film-row-error">{item.error}</p>}
          {(item.message || job?.progress || item.film_path) && <details className={styles.details}>
            <summary>Details</summary>
            {item.film_path && <p>{item.film_path}</p>}
            {item.message && <p>{item.message}</p>}
            {job?.progress && <p>{job.progress}</p>}
            {item.status === "downloading" && item.download_rate != null && <p>{formatBytes(item.download_rate)}/s</p>}
          </details>}
        </div>
        <div className={styles.rowActions}>
          {item.status === "needs_review" && item.review && !isCancelling(item) && <button type="button" className="films-button films-button--secondary" disabled={Boolean(queue.busy)}
            onClick={(event) => { reviewTrigger.current = event.currentTarget; setReviewId(item.id); }}>Review</button>}
          {["failed", "cancelled"].includes(item.status) && !isCancelling(item) && <button type="button" className="films-button films-button--secondary" disabled={Boolean(queue.busy)}
            onClick={() => { setNotice(null); setCancellationId(null); void queue.execute(item.id, `/${encodeURIComponent(item.id)}/retry`, { revision: item.revision }); }}>Try again</button>}
          {["failed", "cancelled"].includes(item.status) && !isCancelling(item) && <button type="button" className="films-button films-button--quiet" disabled={Boolean(queue.busy)}
            title="Forget this acquisition and free its release for a fresh download."
            onClick={() => { setNotice(null); setCancellationId(null); void queue.execute(item.id, `/${encodeURIComponent(item.id)}/dismiss`, { revision: item.revision }); }}>Dismiss</button>}
          {canCancel(item) && <button type="button" className="films-button films-button--quiet" disabled={Boolean(queue.busy)}
            title="Stop this acquisition and remove its downloaded files. Imported films and library assets are kept."
            onClick={() => {
              setNotice(null); setCancellationId(null);
              void queue.execute(item.id, `/${encodeURIComponent(item.id)}/cancel`, { revision: item.revision }).then((accepted) => {
                if (accepted) setCancellationId(item.id);
              });
            }}>Cancel</button>}
        </div>
      </article>
    );
  }

  return (
    <section className="films-section" aria-labelledby="acquisition-heading">
      <div className="films-section-header">
        <div><h2 ref={queueHeading} tabIndex={-1} id="acquisition-heading">Film queue{activeCount > 0 ? ` · ${activeCount} active` : ""}</h2>
          <p>Add a film here. It becomes searchable automatically.</p></div>
        <div className={styles.actions}>
          <button type="button" className="films-button films-button--quiet" disabled={queue.loading || refreshing || Boolean(queue.busy)}
            onClick={() => {
              setRefreshing(true);
              void Promise.allSettled([queue.refresh(), onRefresh?.()]).finally(() => setRefreshing(false));
            }}>{refreshing ? "Refreshing…" : "Refresh"}</button>
          <button type="button" className="films-button films-button--primary" aria-expanded={adding} aria-controls="acquisition-add-form"
            disabled={Boolean(queue.busy)} onClick={() => { setAdding(!adding); setNotice(null); setCancellationId(null); }}>{adding ? "Close" : "Add films"}</button>
        </div>
      </div>

      {queue.error && !review && !adding && <p className="films-message films-message--error" role="alert">{queue.error}</p>}
      {displayedNotice && <p className={styles.notice} role="status">{displayedNotice}</p>}
      {queue.status && (!downloader?.available || !queue.status.monitor.running) && (
        <div className={styles.connection} role="status">
          {!downloader?.configured ? "Download setup is needed before adding films online." : !downloader.available ? "The downloader is unavailable. Check Download settings below." : "Downloads are waiting for the queue monitor to start. See Download settings below."}
        </div>
      )}

      {adding && <AddFilmForm status={queue.status} busy={Boolean(queue.busy)} requestError={queue.error} onClearRequestError={queue.clearMutationError} onQueue={async (path, body) => {
        const success = await queue.execute("add", path, body);
        if (success) {
          setAdding(false); setNotice("Film added to the queue. You can leave this page while it prepares.");
          window.requestAnimationFrame(() => {
            queueHeading.current?.focus({ preventScroll: true });
            queueHeading.current?.scrollIntoView({ block: "nearest" });
          });
        }
        return success;
      }} />}

      <div className={styles.queue}>
        {queue.loading && manualJobs.length === 0 ? <p className="films-empty">Loading film queue…</p>
          : visible.length === 0 && manualJobs.length === 0 ? <p className="films-empty">No films in progress. Search for a film or add a file below.</p>
          : <>
            <div className="films-list">{running.map(renderJob)}{visible.map(renderAcquisition)}</div>
            {waiting.length > 0 && <details className={styles.waiting} open={running.length + visible.length === 0 ? true : undefined}>
              <summary>{waiting.length} {running.length + visible.length > 0 ? "other " : ""}film{waiting.length === 1 ? "" : "s"} waiting</summary>
              <div className="films-list">{waiting.map(renderJob)}</div>
            </details>}
          </>}
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

