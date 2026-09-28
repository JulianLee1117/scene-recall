"use client";

import { useEffect, useRef, useState } from "react";
import type { LibraryStorageResponse, LibraryStorageSnapshot } from "@/types/api";
import styles from "./libraryStorage.module.css";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
const POLL_INTERVAL_MS = 2500;

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB", "PB"];
  const scale = Math.min(units.length - 1, Math.floor(Math.log10(bytes) / 3));
  const value = bytes / 1000 ** scale;
  return `${value.toLocaleString(undefined, { maximumFractionDigits: scale > 0 && value < 10 ? 1 : 0 })} ${units[scale]}`;
}

interface StorageState {
  status: "loading" | LibraryStorageResponse["status"];
  snapshot: LibraryStorageSnapshot | null;
  error: string | null;
}

export default function LibraryStorage({ refreshKey = 0 }: { refreshKey?: number }) {
  const [refresh, setRefresh] = useState(0);
  const previousRequest = useRef({ refreshKey, refresh });
  const [state, setState] = useState<StorageState>({ status: "loading", snapshot: null, error: null });

  useEffect(() => {
    const force = previousRequest.current.refreshKey !== refreshKey || previousRequest.current.refresh !== refresh;
    previousRequest.current = { refreshKey, refresh };
    const controller = new AbortController();
    let stopped = false;
    let timer: number | undefined;
    setState((current) => ({ ...current, status: "loading", error: null }));

    const read = async (refreshScan: boolean) => {
      try {
        const response = await fetch(`${API_URL}/library/storage${refreshScan ? "?refresh=true" : ""}`, {
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`Storage measurement unavailable (${response.status}).`);
        const data: LibraryStorageResponse = await response.json();
        if (stopped || controller.signal.aborted) return;
        setState((current) => ({
          status: data.status,
          snapshot: data.snapshot ?? current.snapshot,
          error: data.status === "error" ? data.error || "Could not measure storage." : null,
        }));
        if (data.status === "scanning") timer = window.setTimeout(() => void read(false), POLL_INTERVAL_MS);
      } catch (reason) {
        if (stopped || controller.signal.aborted) return;
        setState((current) => ({
          ...current,
          status: "error",
          error: reason instanceof Error ? reason.message : "Could not measure storage.",
        }));
      }
    };
    void read(force);
    return () => {
      stopped = true;
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [refreshKey, refresh]);

  const { snapshot } = state;
  const busy = state.status === "loading" || state.status === "scanning";
  const films = snapshot?.categories.find((category) => category.id === "source_films");
  const supporting = snapshot?.categories.filter((category) => category.id !== "source_films") ?? [];
  const supportingBytes = supporting.reduce((total, category) => total + category.bytes, 0);
  const supportingIncomplete = supporting.some((category) => category.incomplete);
  const supportingDetail = supporting.filter((category) => category.bytes > 0 || category.incomplete)
    .map((category) => `${category.label}: ${category.incomplete ? "at least " : ""}${formatBytes(category.bytes)}`).join("\n");
  const measurementDetail = snapshot ? [
    "Combined file sizes; shared files counted once",
    `Measured ${new Date(snapshot.measured_at).toLocaleString()}`,
    `${snapshot.file_count.toLocaleString()} files · decimal storage units`,
    ...snapshot.issues.map((issue) => `${issue.path}: ${issue.reason}`),
    ...(snapshot.excluded.length ? [`Excludes: ${snapshot.excluded.join(", ")}`] : []),
  ].join("\n") : "";
  const status = state.error
    ? snapshot ? "Update unavailable" : "Storage unavailable"
    : busy ? snapshot ? "Updating…" : "Measuring storage…"
    : snapshot?.incomplete ? "Some locations unavailable" : "";

  return (
    <section className={styles.storage} aria-label="Library storage" aria-busy={busy}>
      <div className={styles.summary}>
        {snapshot ? (
          <>
            <strong title={measurementDetail} aria-label={`${snapshot.incomplete ? "At least " : ""}${formatBytes(snapshot.total_bytes)} storage. ${measurementDetail}`}>
              {snapshot.incomplete ? "At least " : ""}{formatBytes(snapshot.total_bytes)} <span>storage</span>
            </strong>
            {snapshot.volumes.map((volume) => {
              const label = volume.label.replace(/[\\/]$/, "") || volume.label;
              const detail = [
                `${label} ${volume.incomplete ? "at least " : ""}${formatBytes(volume.bytes)} in library files`,
                volume.free_bytes === null ? "Free space unavailable" : `${formatBytes(volume.free_bytes)} free`,
                ...(volume.total_capacity_bytes === null ? [] : [`${formatBytes(volume.total_capacity_bytes)} drive capacity`]),
              ].join(" · ");
              return <span className={styles.volume} key={volume.id} title={detail} aria-label={detail}>
                <span>{label}</span> {volume.incomplete ? "≥ " : ""}{formatBytes(volume.bytes)}
              </span>;
            })}
          </>
        ) : <span className={styles.pending} role="status" title={state.error ?? undefined}>{status}</span>}
        <button className={styles.refresh} type="button" disabled={busy} aria-label="Refresh storage measurement" title="Refresh storage measurement" onClick={() => setRefresh((current) => current + 1)}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.6-1L20 9M4 15l2.3 3A7 7 0 0 0 17.9 17" />
          </svg>
        </button>
      </div>
      {snapshot && <div className={styles.breakdown}>
        {films && <span title="Original films and their subtitle files">Films {films.incomplete ? "≥ " : ""}{formatBytes(films.bytes)}</span>}
        <span title={supportingDetail || "No supporting files measured"} aria-label={`Supporting files ${supportingIncomplete ? "at least " : ""}${formatBytes(supportingBytes)}. ${supportingDetail}`}>
          Supporting {supportingIncomplete ? "≥ " : ""}{formatBytes(supportingBytes)}
        </span>
        {status && <span className={styles.status} role="status" title={state.error || measurementDetail}>{status}</span>}
      </div>}
    </section>
  );
}
