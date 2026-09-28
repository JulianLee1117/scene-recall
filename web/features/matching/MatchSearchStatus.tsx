"use client";

import { useEffect, useState } from "react";
import type { MatchSearchJob } from "@/types/matching";
import styles from "./matchSearch.module.css";

export default function MatchSearchStatus({ job, busy }: { job: MatchSearchJob | null; busy: boolean; count: number }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!busy) return;
    const timer = setTimeout(() => setNow(Date.now() / 1000), 1000);
    return () => clearTimeout(timer);
  }, [busy, now]);
  if (!job && !busy) return null;

  const elapsed = job?.created_at != null && (busy || job.finished_at != null)
    ? Math.max(0, Math.floor((job.finished_at ?? now) - job.created_at)) : null;
  const duration = elapsed == null ? null : `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`;
  const stopping = busy && job?.cancel_requested;
  const state = stopping ? "stopping" : job?.status ?? "starting";
  let title = "Starting search…";
  let detail = "";
  if (stopping) {
    title = "Stopping search…";
  } else if (job?.status === "queued") {
    title = "Waiting to start";
    detail = "Other library or editing tasks may need to finish first.";
  } else if (job?.status === "running") {
    title = "Searching for match cuts";
    detail = job.progress || "Comparing source footage…";
  } else if (job?.status === "completed") {
    title = "Search complete";
  } else if (job?.status === "cancelled") {
    title = "Search cancelled";
  } else if (job?.status === "failed" || job?.status === "interrupted") {
    title = job.status === "failed" ? "Search failed" : "Search interrupted";
  }

  return <section className={styles.searchStatus} data-active={busy} data-state={state} aria-label="Match search status">
    <span className={styles.statusIcon} data-active={busy && state !== "queued"} data-state={state} aria-hidden="true" />
    <div className={styles.statusContent} role="status" aria-live="polite" aria-atomic="true">
      <strong>{title}</strong>{detail && <span className={styles.statusDetail}>{detail}</span>}
    </div>
    {duration && <span className={styles.elapsed} aria-label={`${job?.status === "queued" ? "Waiting" : "Elapsed"} ${duration}`}>{job?.status === "queued" ? "Waiting" : "Elapsed"} {duration}</span>}
  </section>;
}
