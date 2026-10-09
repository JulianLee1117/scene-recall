"use client";

import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import DirectionIcon from "@/components/DirectionIcon";
import { acquisitionRequest, messageOf } from "./api";
import { formatBytes, metadataFromFields } from "./model";
import { releaseMetadata } from "./releaseMetadata";
import type { AcquisitionStatus, Release } from "./types";
import styles from "./addFilmForm.module.css";

type Source = "search" | "magnet" | "torrent" | "downloaded";
interface Props {
  status: AcquisitionStatus | null;
  busy: boolean;
  requestError?: string | null;
  onClearRequestError?: () => void;
  onQueue: (path: string, body: object | FormData) => Promise<boolean>;
  downloadedFiles?: ReactNode;
  initialSource?: "downloaded";
}

function ReleaseDetails({ release }: { release: Release }) {
  return <details className={styles.releaseDetails}><summary>Release filename</summary><p>{release.title}</p></details>;
}

function ReleaseSummary({ release }: { release: Release }) {
  const hint = releaseMetadata(release.title);
  return <>
    <strong>{hint.title ? `${hint.title} (${hint.year})` : release.title}</strong>
    {hint.quality.length > 0 && <span className={styles.quality}>{hint.quality.join(" · ")}</span>}
    <small>{formatBytes(release.size)} · {release.seeders == null ? "Seeders unknown" : `${release.seeders} seeders`} · {release.indexer}</small>
  </>;
}

export default function AddFilmForm({ status, busy, requestError, onClearRequestError, onQueue, downloadedFiles, initialSource }: Props) {
  const hasDownloadedFiles = downloadedFiles != null;
  const [source, setSource] = useState<Source>(() => initialSource === "downloaded" && hasDownloadedFiles
    ? "downloaded" : status?.search.available ? "search" : "magnet");
  const [title, setTitle] = useState("");
  const [year, setYear] = useState("");
  const [edition, setEdition] = useState("");
  const [magnet, setMagnet] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Release[]>([]);
  const [selected, setSelected] = useState<Release | null>(null);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [searchMessage, setSearchMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const fileRef = useRef<HTMLInputElement>(null);
  const titleRef = useRef<HTMLInputElement>(null);
  const queryRef = useRef<HTMLInputElement>(null);
  const magnetRef = useRef<HTMLInputElement>(null);
  const downloadedRef = useRef<HTMLButtonElement>(null);
  const resultsRef = useRef<HTMLFieldSetElement>(null);
  const edited = useRef({ title: false, year: false });
  const submitting = useRef(false);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; controllerRef.current?.abort(); };
  }, []);

  useEffect(() => {
    if (source === "search") queryRef.current?.focus({ preventScroll: true });
    if (source === "magnet") magnetRef.current?.focus({ preventScroll: true });
    if (source === "torrent") fileRef.current?.focus({ preventScroll: true });
    if (source === "downloaded") downloadedRef.current?.focus({ preventScroll: true });
  }, [source]);

  useEffect(() => {
    if (source === "search" && selected) titleRef.current?.focus({ preventScroll: true });
  }, [source, selected]);

  useEffect(() => {
    if (source === "search" && !selected && results.length > 0) resultsRef.current?.focus({ preventScroll: true });
  }, [source, selected, results]);

  function clearError() {
    setError(null);
    onClearRequestError?.();
  }

  function changeQuery(value: string) {
    controllerRef.current?.abort();
    clearError();
    setQuery(value); setSelected(null); setResults([]); setSearched(false); setSearching(false); setSearchMessage(null);
    setTitle(""); setYear(""); setEdition(""); edited.current = { title: false, year: false };
  }

  function chooseRelease(release: Release) {
    const hint = releaseMetadata(release.title);
    if (!edited.current.title) setTitle(hint.title);
    if (!edited.current.year) setYear(hint.year);
    setSelected(release); clearError();
  }

  async function search(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || searching || !status?.search.available || query.trim().length < 2) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    clearError();
    setSearching(true); setSelected(null); setResults([]); setSearchMessage(null); setSearched(false);
    try {
      const response = await acquisitionRequest<{ results: Release[]; message?: string }>(
        `/search?q=${encodeURIComponent(query.trim())}`, { signal: controller.signal },
      );
      if (!mounted.current || controller.signal.aborted) return;
      setResults(response.results); setSearchMessage(response.message || null); setSearched(true);
    } catch (problem) {
      if (mounted.current && !controller.signal.aborted) setError(messageOf(problem));
    } finally {
      if (mounted.current && !controller.signal.aborted) setSearching(false);
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || submitting.current || source === "downloaded") return;
    clearError();
    try {
      const metadata = metadataFromFields(title, year, edition);
      let path: string;
      let body: object | FormData;
      if (source === "search") {
        if (!selected) throw new Error("Choose a release from the search results.");
        path = "/release"; body = { ...metadata, release_id: selected.id };
      } else if (source === "torrent") {
        if (!file) throw new Error("Choose a .torrent file.");
        if (!file.name.toLowerCase().endsWith(".torrent")) throw new Error("Choose a file ending in .torrent.");
        if (file.size > 2 * 1024 * 1024) throw new Error("Torrent files must be 2 MB or smaller.");
        path = "/torrent";
        const form = new FormData();
        form.set("file", file); form.set("title", metadata.title); form.set("year", String(metadata.year));
        if (metadata.edition) form.set("edition", metadata.edition);
        body = form;
      } else {
        if (!magnet.trim().startsWith("magnet:?")) throw new Error("Paste a magnet link starting with magnet:?.");
        path = "/magnet"; body = { ...metadata, magnet: magnet.trim() };
      }
      submitting.current = true;
      const queued = await onQueue(path, body);
      if (!mounted.current || !queued) return;
      setTitle(""); setYear(""); setEdition(""); setMagnet(""); setSelected(null); setFile(null);
      edited.current = { title: false, year: false };
      if (fileRef.current) fileRef.current.value = "";
    } catch (problem) {
      if (mounted.current) setError(messageOf(problem));
    } finally { submitting.current = false; }
  }

  const showConfirmation = source !== "downloaded" && (source !== "search" || selected !== null);
  const downloadAvailable = status?.downloader.configured;
  const connectionNote = !status || source === "downloaded" ? null
    : !status.downloader.configured ? "Set up downloads in Queue → Download settings before adding films online."
    : !status.downloader.available ? "The downloader is unavailable. Check Queue → Download settings."
    : !status.monitor.running ? "Downloads are queued until the monitor starts. See Queue → Download settings."
    : null;
  const visibleError = error || requestError;
  return (
    <div className={styles.addForm} id="acquisition-add-form">
      <div className={`${styles.sourceChoices} ${hasDownloadedFiles ? styles.hasDownloadedFiles : ""}`} role="group" aria-label="Film source">
        {([
          ["search", "Search releases"], ["magnet", "Magnet link"], ["torrent", "Torrent file"],
          ...(hasDownloadedFiles ? [["downloaded", "Downloaded files"]] : []),
        ] as [Source, string][]).map(([value, label]) => (
          <button key={value} ref={value === "downloaded" ? downloadedRef : undefined} type="button" aria-pressed={source === value} disabled={busy}
            className={`films-button ${source === value ? "films-button--secondary" : "films-button--quiet"}`}
            onClick={() => { controllerRef.current?.abort(); setSearching(false); setSource(value); clearError(); }}>{label}</button>
        ))}
      </div>

      {source === "downloaded" && downloadedFiles}
      {connectionNote && <p className={styles.note} role="status">{connectionNote}</p>}

      {source === "search" && <div className={styles.search}>
        <form onSubmit={(event) => void search(event)} className={styles.searchForm}>
          <label className="film-review-field">
            <span>Search movie releases</span>
            <input ref={queryRef} value={query} onChange={(event) => changeQuery(event.target.value)} placeholder="e.g. Challengers 2024"
              minLength={2} maxLength={200} disabled={busy} required autoComplete="off" />
          </label>
          <button className="films-button films-button--secondary" disabled={busy || searching || !status?.search.available || query.trim().length < 2}>
            {searching ? "Searching…" : "Search"}
          </button>
        </form>
        {!status?.search.available && <p className={styles.note}>{status?.search.message || "Search is unavailable. You can add a magnet link or torrent file."}</p>}
        {searching && <p className={styles.note} role="status">Finding releases…</p>}
        {searchMessage && <p className={styles.note} role="status">{searchMessage}</p>}
        {searched && results.length === 0 && <p className={styles.note} role="status">The configured search returned no releases. Try the title and release year, or paste a magnet link.</p>}
        {results.length > 0 && !selected && <fieldset ref={resultsRef} tabIndex={-1} className={styles.releases}>
          <legend>Choose a release <span>· {results.length} result{results.length === 1 ? "" : "s"}</span></legend>
          <div className={styles.releaseList}>
            {results.map((release) => <div className={styles.release} key={release.id}>
              <button type="button" className={styles.releaseChoice} disabled={busy}
                aria-label={`Choose ${release.title}`} data-release-id={release.id} onClick={() => chooseRelease(release)}>
                <span className={styles.releaseCopy}><ReleaseSummary release={release} /></span>
                <span className={styles.choose}>Choose <DirectionIcon name="arrow-right" size={14} /></span>
              </button>
              <ReleaseDetails release={release} />
            </div>)}
          </div>
        </fieldset>}
      </div>}

      {showConfirmation && <form onSubmit={(event) => void submit(event)} className={styles.queueForm}>
        {source === "magnet" && <label className="film-review-field">
          <span>Magnet link</span>
          <input ref={magnetRef} value={magnet} onChange={(event) => { setMagnet(event.target.value); clearError(); }} placeholder="magnet:?xt=urn:btih:…"
            required maxLength={16384} disabled={busy} autoComplete="off" spellCheck={false} />
        </label>}
        {source === "torrent" && <label className="film-review-field">
          <span>Torrent file</span>
          <input ref={fileRef} type="file" accept=".torrent,application/x-bittorrent" required disabled={busy}
            onChange={(event) => { setFile(event.target.files?.[0] || null); clearError(); }} />
        </label>}
        {source === "search" && selected && <div className={styles.selected}>
          <div className={styles.selectedHeader}><span>Selected release</span><button type="button" className="films-button films-button--quiet" disabled={busy}
            onClick={() => { setSelected(null); clearError(); }}>Change release</button></div>
          <div className={styles.releaseCopy}><ReleaseSummary release={selected} /></div>
          <ReleaseDetails release={selected} />
        </div>}
        <fieldset className={styles.confirmation}>
          <legend>Confirm library name</legend>
          <div className={styles.metadataFields}>
            <label className="film-review-field"><span>Film title</span>
              <input ref={titleRef} value={title} onChange={(event) => { edited.current.title = true; setTitle(event.target.value); clearError(); }} required maxLength={180} disabled={busy} autoComplete="off" />
            </label>
            <label className="film-review-field"><span>Year</span>
              <input value={year} onChange={(event) => { edited.current.year = true; setYear(event.target.value); clearError(); }} required inputMode="numeric" pattern="[0-9]{4}"
                maxLength={4} disabled={busy} autoComplete="off" />
            </label>
          </div>
          <details className={styles.edition}>
            <summary>Add edition <span>optional</span></summary>
            <label className="film-review-field"><span>Edition</span>
              <input value={edition} onChange={(event) => { setEdition(event.target.value); clearError(); }} placeholder="Director’s Cut" maxLength={80} disabled={busy} autoComplete="off" />
            </label>
          </details>
        </fieldset>
        {visibleError && <p className="films-message films-message--error" role="alert">{visibleError}</p>}
        <div className={styles.submit}>
          <p className={styles.note}>Downloads and checks files, then joins the ingestion queue.</p>
          <button type="submit" className="films-button films-button--primary" disabled={busy || !downloadAvailable}>
            {busy ? "Adding…" : "Queue film"}
          </button>
        </div>
      </form>}
      {!showConfirmation && visibleError && <p className="films-message films-message--error" role="alert">{visibleError}</p>}
    </div>
  );
}
