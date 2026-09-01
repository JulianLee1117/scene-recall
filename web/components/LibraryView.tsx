"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react";
import type {
  ImportFilmRequest,
  ImportFilmResponse,
  IncomingFilm,
  IngestJob,
  IngestResponse,
  LibraryFilm,
  SubtitleImportDecision,
} from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
const JOB_POLL_INTERVAL_MS = 2500;
const WINDOWS_RESERVED_NAMES = new Set([
  "CON",
  "PRN",
  "AUX",
  "NUL",
  ...Array.from({ length: 9 }, (_, index) => `COM${index + 1}`),
  ...Array.from({ length: 9 }, (_, index) => `LPT${index + 1}`),
]);

function isActiveJob(job: IngestJob): boolean {
  return job.status === "queued" || job.status === "running";
}

function pathKey(path: string): string {
  return path.replaceAll("\\", "/").toLowerCase();
}

function formatElapsed(startedAt: number | null, now: number): string {
  if (startedAt === null) return "";
  const seconds = Math.max(0, Math.floor(now / 1000 - startedAt));
  const minutes = Math.floor(seconds / 60);
  return minutes > 0
    ? `${minutes}m ${seconds % 60}s elapsed`
    : `${seconds}s elapsed`;
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

async function apiError(response: Response, fallback: string): Promise<Error> {
  try {
    const body = (await response.json()) as {
      detail?: string;
      message?: string;
    };
    const message = body.detail ?? body.message;
    if (typeof message === "string" && message.trim()) {
      return new Error(message);
    }
  } catch {
    // The status-based fallback also covers non-JSON responses.
  }
  return new Error(`${fallback} (${response.status})`);
}

async function getJson<T>(path: string, fallback: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (!response.ok) throw await apiError(response, fallback);
  return (await response.json()) as T;
}

function filenameExtension(filename: string): string {
  const dot = filename.lastIndexOf(".");
  return dot > 0 ? filename.slice(dot) : "";
}

function cleanFilenamePart(value: string): string {
  const cleaned = value
    .trim()
    .replaceAll(":", " - ")
    .replace(/[<>"/\\|?*\u0000-\u001f]/g, " ")
    .replace(/\s+/g, " ")
    .replace(/^[. ]+|[. ]+$/g, "");
  return WINDOWS_RESERVED_NAMES.has(cleaned.toUpperCase())
    ? `_${cleaned}`
    : cleaned;
}

function destinationFilename(
  candidate: IncomingFilm,
  title: string,
  year: string,
  edition: string,
): string {
  const cleanTitle = cleanFilenamePart(title) || "Untitled";
  const cleanEdition = cleanFilenamePart(edition);
  const extension =
    filenameExtension(candidate.suggested_filename) ||
    filenameExtension(candidate.filename);
  const yearSuffix = year.trim() ? ` (${year.trim()})` : "";
  const editionSuffix = cleanEdition ? ` [${cleanEdition}]` : "";
  return `${cleanTitle}${yearSuffix}${editionSuffix}${extension.toLowerCase()}`;
}

export default function LibraryView() {
  const [films, setFilms] = useState<LibraryFilm[]>([]);
  const [incoming, setIncoming] = useState<IncomingFilm[]>([]);
  const [jobs, setJobs] = useState<IngestJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [rescanning, setRescanning] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pendingIngestPaths, setPendingIngestPaths] = useState<Set<string>>(
    () => new Set(),
  );
  const [ingestErrors, setIngestErrors] = useState<Record<string, string>>({});
  const [now, setNow] = useState(Date.now());

  const [selectedCandidate, setSelectedCandidate] =
    useState<IncomingFilm | null>(null);
  const [title, setTitle] = useState("");
  const [year, setYear] = useState("");
  const [edition, setEdition] = useState("");
  const [confirmedFinished, setConfirmedFinished] = useState(false);
  const [subtitleDecision, setSubtitleDecision] =
    useState<SubtitleImportDecision | null>(null);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);

  const jobsRef = useRef<IngestJob[]>([]);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleInputRef = useRef<HTMLInputElement>(null);
  const yearInputRef = useRef<HTMLInputElement>(null);
  const confirmationRef = useRef<HTMLInputElement>(null);
  const firstSubtitleRef = useRef<HTMLInputElement>(null);

  const fetchLibrary = useCallback(async () => {
    const data = await getJson<LibraryFilm[]>(
      "/library",
      "Could not load the film library",
    );
    setFilms(data);
    return data;
  }, []);

  const fetchIncoming = useCallback(async () => {
    const data = await getJson<IncomingFilm[]>(
      "/incoming",
      "Could not scan the incoming folder",
    );
    setIncoming(data);
    return data;
  }, []);

  const fetchJobs = useCallback(async () => {
    const data = await getJson<IngestJob[]>(
      "/ingest/jobs",
      "Could not load ingestion status",
    );
    jobsRef.current = data;
    setJobs(data);
    return data;
  }, []);

  const mergeJob = useCallback((job: IngestJob) => {
    const merge = (previous: IngestJob[]) => [
      ...previous.filter((existing) => existing.job_id !== job.job_id),
      job,
    ];
    jobsRef.current = merge(jobsRef.current);
    setJobs(merge);
  }, []);

  const refreshCatalog = useCallback(async () => {
    const results = await Promise.allSettled([fetchIncoming(), fetchLibrary()]);
    const failure = results.find(
      (result): result is PromiseRejectedResult => result.status === "rejected",
    );
    if (failure) throw failure.reason;
  }, [fetchIncoming, fetchLibrary]);

  useEffect(() => {
    let cancelled = false;

    void Promise.allSettled([refreshCatalog(), fetchJobs()]).then((results) => {
      if (cancelled) return;
      const failure = results.find(
        (result): result is PromiseRejectedResult =>
          result.status === "rejected",
      );
      if (failure) {
        setPageError(
          errorMessage(failure.reason, "Could not connect to scene-recall"),
        );
      }
      setLoading(false);
    });

    return () => {
      cancelled = true;
    };
  }, [fetchJobs, refreshCatalog]);

  const hasActiveJobs = jobs.some(isActiveJob);
  const hasRunningJob = jobs.some((job) => job.status === "running");

  useEffect(() => {
    if (!hasActiveJobs) return;

    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      const previousJobs = jobsRef.current;
      try {
        const nextJobs = await fetchJobs();
        if (cancelled) return;

        const nextById = new Map(nextJobs.map((job) => [job.job_id, job]));
        const jobCompleted = previousJobs.some((job) => {
          if (!isActiveJob(job)) return false;
          const next = nextById.get(job.job_id);
          return next === undefined || !isActiveJob(next);
        });

        if (jobCompleted) {
          try {
            await refreshCatalog();
            if (!cancelled) setPageError(null);
          } catch (error) {
            if (!cancelled) {
              setPageError(
                errorMessage(error, "Ingestion finished, but Films did not refresh"),
              );
            }
          }
        }
      } catch {
        // A brief API outage should not discard known progress; try again.
      }

      if (!cancelled && jobsRef.current.some(isActiveJob)) {
        timer = window.setTimeout(poll, JOB_POLL_INTERVAL_MS);
      }
    };

    timer = window.setTimeout(poll, JOB_POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [fetchJobs, hasActiveJobs, refreshCatalog]);

  useEffect(() => {
    if (!hasRunningJob) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasRunningJob]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;

    if (selectedCandidate && !dialog.open) {
      dialog.showModal();
      window.requestAnimationFrame(() => titleInputRef.current?.focus());
    } else if (!selectedCandidate && dialog.open) {
      dialog.close();
    }
  }, [selectedCandidate]);

  const jobsByPath = useMemo(() => {
    const result = new Map<string, IngestJob>();
    for (const job of jobs) result.set(pathKey(job.path), job);
    return result;
  }, [jobs]);

  const indexedCount = films.filter((film) => {
    const job = jobsByPath.get(pathKey(film.path));
    return film.status === "indexed" || job?.status === "done";
  }).length;

  const activeCount = jobs.filter(isActiveJob).length;

  const handleRescan = useCallback(async () => {
    setRescanning(true);
    setPageError(null);
    try {
      await fetchIncoming();
    } catch (error) {
      setPageError(errorMessage(error, "Could not scan the incoming folder"));
    } finally {
      setRescanning(false);
    }
  }, [fetchIncoming]);

  const openReview = useCallback((candidate: IncomingFilm) => {
    setSelectedCandidate(candidate);
    setTitle(candidate.suggested_title);
    setYear(candidate.suggested_year?.toString() ?? "");
    setEdition(candidate.suggested_edition ?? "");
    setConfirmedFinished(false);
    setSubtitleDecision(null);
    setImportError(null);
  }, []);

  const closeReview = useCallback(() => {
    if (importing) return;
    dialogRef.current?.close();
    setSelectedCandidate(null);
    setImportError(null);
  }, [importing]);

  const handleImport = useCallback(
    async (ingest: boolean) => {
      if (!selectedCandidate || importing) return;

      const cleanTitle = title.trim();
      if (!cleanTitle) {
        setImportError("Enter a title for this film.");
        titleInputRef.current?.focus();
        return;
      }
      if (!cleanFilenamePart(cleanTitle)) {
        setImportError("The title needs at least one filename-safe character.");
        titleInputRef.current?.focus();
        return;
      }
      if (edition.trim() && !cleanFilenamePart(edition)) {
        setImportError("The edition needs at least one filename-safe character.");
        return;
      }

      const parsedYear = Number(year);
      if (
        !/^\d{4}$/.test(year.trim()) ||
        !Number.isSafeInteger(parsedYear) ||
        parsedYear < 1888 ||
        parsedYear > 2100
      ) {
        setImportError("Enter a release year between 1888 and 2100.");
        yearInputRef.current?.focus();
        return;
      }

      if (selectedCandidate.subtitle_review_candidates === undefined) {
        setImportError(
          "Film intake is finishing an update. Try again shortly.",
        );
        return;
      }

      if (
        selectedCandidate.subtitle_review_candidates.length > 0 &&
        subtitleDecision === null
      ) {
        setImportError("Choose an English subtitle, or choose none.");
        firstSubtitleRef.current?.focus();
        return;
      }

      if (!confirmedFinished) {
        setImportError(
          "Confirm that torrenting and seeding are finished before moving this item.",
        );
        confirmationRef.current?.focus();
        return;
      }

      const request: ImportFilmRequest = {
        relative_path: selectedCandidate.relative_path,
        title: cleanTitle,
        year: parsedYear,
        edition: edition.trim() || null,
        ingest,
        confirm_finished: true,
        subtitle_decision: subtitleDecision,
      };

      setImporting(true);
      setImportError(null);
      setNotice(null);
      try {
        const response = await fetch(`${API_URL}/films/import`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(request),
        });
        if (!response.ok) {
          throw await apiError(response, "Could not add this film");
        }

        let result: ImportFilmResponse | null = null;
        try {
          result = (await response.json()) as ImportFilmResponse;
        } catch {
          // A successful move still counts even if its response cannot be read.
        }
        if (result?.job) mergeJob(result.job);

        const importedTitle = cleanTitle;
        dialogRef.current?.close();
        setSelectedCandidate(null);
        setNotice(
          ingest
            ? `${importedTitle} was added and queued for ingestion.`
            : `${importedTitle} was added to the library.`,
        );

        const refreshResults = await Promise.allSettled([
          refreshCatalog(),
          fetchJobs(),
        ]);
        const refreshFailure = refreshResults.find(
          (result): result is PromiseRejectedResult =>
            result.status === "rejected",
        );
        if (refreshFailure) {
          setPageError(
            errorMessage(
              refreshFailure.reason,
              "The film was added, but Films did not refresh",
            ),
          );
        } else {
          setPageError(null);
        }
      } catch (error) {
        setImportError(errorMessage(error, "Could not add this film"));
      } finally {
        setImporting(false);
      }
    },
    [
      confirmedFinished,
      edition,
      fetchJobs,
      importing,
      mergeJob,
      refreshCatalog,
      selectedCandidate,
      subtitleDecision,
      title,
      year,
    ],
  );

  const handleImportSubmit = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      void handleImport(true);
    },
    [handleImport],
  );

  const handleIngest = useCallback(
    async (film: LibraryFilm) => {
      const key = pathKey(film.path);
      setPendingIngestPaths((previous) => new Set(previous).add(key));
      setIngestErrors((previous) => {
        const next = { ...previous };
        delete next[key];
        return next;
      });
      setNotice(null);

      try {
        const response = await fetch(`${API_URL}/ingest`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ path: film.path }),
        });
        if (!response.ok) {
          throw await apiError(response, "Could not queue ingestion");
        }

        let queuedJob: IngestResponse | null = null;
        try {
          queuedJob = (await response.json()) as IngestResponse;
        } catch {
          // The accepted POST remains successful; the status refresh below can recover.
        }
        if (queuedJob) mergeJob(queuedJob);
        setNotice(`${film.title || film.filename} was queued for ingestion.`);

        try {
          await fetchJobs();
          setPageError(null);
        } catch {
          setPageError(
            "Ingestion was queued, but live status is temporarily unavailable.",
          );
        }
      } catch (error) {
        setIngestErrors((previous) => ({
          ...previous,
          [key]: errorMessage(error, "Could not queue ingestion"),
        }));
      } finally {
        setPendingIngestPaths((previous) => {
          const next = new Set(previous);
          next.delete(key);
          return next;
        });
      }
    },
    [fetchJobs, mergeJob],
  );

  const preview = selectedCandidate
    ? destinationFilename(selectedCandidate, title, year, edition)
    : "";
  const subtitleReviewCandidates =
    selectedCandidate?.subtitle_review_candidates ?? [];

  return (
    <div className="films-page">
      <header className="films-header">
        <div>
          <h1>Films</h1>
          <p>
            {films.length} in library · {indexedCount} searchable
            {activeCount > 0
              ? ` · ${activeCount} ingest${activeCount === 1 ? "" : "s"} active`
              : ""}
          </p>
        </div>
      </header>

      <div className="films-messages" aria-live="polite">
        {pageError && (
          <p className="films-message films-message--error" role="alert">
            {pageError}
          </p>
        )}
        {notice && <p className="films-message films-message--success">{notice}</p>}
      </div>

      <section className="films-section" aria-labelledby="incoming-heading">
        <div className="films-section-header">
          <div>
            <h2 id="incoming-heading">Ready to add</h2>
            <p>
              Files detected in incoming. Review them after torrenting and seeding
              finish.
            </p>
          </div>
          <button
            type="button"
            className="films-button films-button--quiet"
            onClick={() => void handleRescan()}
            disabled={loading || rescanning}
          >
            {rescanning ? "Scanning…" : "Rescan"}
          </button>
        </div>

        {loading ? (
          <p className="films-empty">Scanning incoming…</p>
        ) : incoming.length === 0 ? (
          <p className="films-empty">Nothing is waiting to be added.</p>
        ) : (
          <div className="films-list">
            {incoming.map((candidate) => (
              <article className="film-row film-row--incoming" key={candidate.relative_path}>
                <div className="film-row-copy">
                  <h3>{candidate.suggested_title || candidate.filename}</h3>
                  <p className="film-filename" title={candidate.filename}>
                    {candidate.filename}
                  </p>
                  <p className="film-meta">
                    {candidate.size_gb} GB
                    {candidate.extra_video_count > 0 && (
                      <>
                        <span aria-hidden="true"> · </span>
                        Main movie selected · {candidate.extra_video_count} other video
                        {candidate.extra_video_count === 1 ? "" : "s"} ignored
                      </>
                    )}
                  </p>
                </div>
                <button
                  type="button"
                  className="films-button films-button--secondary"
                  onClick={() => openReview(candidate)}
                >
                  Review &amp; add
                </button>
              </article>
            ))}
          </div>
        )}
      </section>

      <section className="films-section" aria-labelledby="library-heading">
        <div className="films-section-header">
          <div>
            <h2 id="library-heading">Library</h2>
            <p>Films are ingested one at a time to keep search responsive.</p>
          </div>
        </div>

        {loading ? (
          <p className="films-empty">Loading library…</p>
        ) : films.length === 0 ? (
          <p className="films-empty">No films have been added yet.</p>
        ) : (
          <div className="films-list">
            {films.map((film) => {
              const key = pathKey(film.path);
              const job = jobsByPath.get(key);
              const isPending = pendingIngestPaths.has(key);
              const isQueued = job?.status === "queued" || isPending;
              const isRunning = job?.status === "running";
              const isFailed = job?.status === "error";
              const isIndexed =
                !isQueued &&
                !isRunning &&
                !isFailed &&
                (film.status === "indexed" || job?.status === "done");
              const status = isQueued
                ? "queued"
                : isRunning
                  ? "running"
                  : isFailed
                    ? "failed"
                    : isIndexed
                      ? "indexed"
                      : "ready";
              const statusLabel = isPending
                ? "Starting…"
                : status === "queued"
                  ? job?.queue_position != null
                    ? `Queued · ${job.queue_position}`
                    : "Queued"
                  : status === "running"
                    ? "Ingesting…"
                    : status === "failed"
                      ? "Failed"
                      : status === "indexed"
                        ? "Indexed"
                        : "Ready";
              const canIngest = status === "ready" || status === "failed";
              const actionError = ingestErrors[key];

              return (
                <article className={`film-row film-row--${status}`} key={film.path}>
                  <div className="film-row-copy">
                    <h3>{film.title || film.filename}</h3>
                    <p className="film-filename" title={film.filename}>
                      {film.filename}
                    </p>
                    <p className="film-meta">
                      {film.size_gb} GB
                      {isQueued && !isPending && (
                        <>
                          <span aria-hidden="true"> · </span>
                          {job?.queue_position === 1
                            ? "Next in queue"
                            : job?.queue_position != null
                              ? `Queue position ${job.queue_position}`
                              : "Waiting to ingest"}
                        </>
                      )}
                      {isRunning && job && (
                        <>
                          <span aria-hidden="true"> · </span>
                          {formatElapsed(job.started_at, now)}
                        </>
                      )}
                    </p>
                    {isRunning && job?.progress && (
                      <p className="film-progress-copy" title={job.progress}>
                        {job.progress}
                      </p>
                    )}
                    {isFailed && job?.error && (
                      <p className="film-row-error" title={job.error}>
                        {job.error}
                      </p>
                    )}
                    {actionError && (
                      <p className="film-row-error" role="alert">
                        {actionError}
                      </p>
                    )}
                  </div>

                  {isRunning && (
                    <span className="film-progress-track" aria-hidden="true">
                      <span />
                    </span>
                  )}

                  <span
                    className={`film-status film-status--${status}`}
                    role="status"
                    aria-live="polite"
                  >
                    {statusLabel}
                  </span>

                  {canIngest && (
                    <button
                      type="button"
                      className="films-button films-button--secondary"
                      onClick={() => void handleIngest(film)}
                      disabled={isPending}
                    >
                      {isFailed ? "Retry" : "Ingest"}
                    </button>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </section>

      <dialog
        ref={dialogRef}
        className="film-review-dialog"
        aria-labelledby="film-review-title"
        aria-describedby="film-review-description"
        aria-busy={importing}
        onCancel={(event) => {
          if (importing) event.preventDefault();
        }}
        onClose={() => {
          if (!importing) setSelectedCandidate(null);
        }}
        onClick={(event) => {
          if (event.target === event.currentTarget) closeReview();
        }}
      >
        {selectedCandidate && (
          <form className="film-review-form" onSubmit={handleImportSubmit}>
            <div className="film-review-heading">
              <div>
                <p className="film-review-eyebrow">Review incoming film</p>
                <h2 id="film-review-title">Name it for your library</h2>
              </div>
              <button
                type="button"
                className="film-review-close"
                onClick={closeReview}
                disabled={importing}
                aria-label="Close review"
              >
                ×
              </button>
            </div>

            <p id="film-review-description" className="film-review-source">
              Moving <strong>{selectedCandidate.filename}</strong> from incoming.
            </p>

            <div className="film-review-fields">
              <label className="film-review-field film-review-field--title">
                <span>Title</span>
                <input
                  ref={titleInputRef}
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  disabled={importing}
                  required
                  autoComplete="off"
                />
              </label>
              <label className="film-review-field film-review-field--year">
                <span>Year</span>
                <input
                  ref={yearInputRef}
                  value={year}
                  onChange={(event) => setYear(event.target.value)}
                  disabled={importing}
                  required
                  inputMode="numeric"
                  pattern="[0-9]{4}"
                  maxLength={4}
                  min={1888}
                  max={2100}
                  placeholder="1999"
                  autoComplete="off"
                />
              </label>
              <label className="film-review-field film-review-field--edition">
                <span>Edition <small>optional</small></span>
                <input
                  value={edition}
                  onChange={(event) => setEdition(event.target.value)}
                  disabled={importing}
                  placeholder="Director’s Cut"
                  autoComplete="off"
                />
              </label>
            </div>

            <div className="film-review-preview">
              <span>Filename</span>
              <code>{preview}</code>
            </div>

            {selectedCandidate.extra_video_count > 0 && (
              <p className="film-review-note">
                Main movie selected · {selectedCandidate.extra_video_count} other video
                {selectedCandidate.extra_video_count === 1 ? "" : "s"} ignored
              </p>
            )}

            {subtitleReviewCandidates.length > 0 && (
              <fieldset className="film-review-subtitles">
                <legend>Subtitles</legend>
                <p>Which track is English?</p>
                <div className="film-review-subtitle-options">
                  {subtitleReviewCandidates.map(
                    (candidate, index) => (
                      <label
                        className="film-review-subtitle-option"
                        key={candidate.relative_path}
                      >
                        <input
                          ref={index === 0 ? firstSubtitleRef : undefined}
                          type="radio"
                          name="subtitle-decision"
                          checked={
                            subtitleDecision?.action === "use_as_english" &&
                            subtitleDecision.relative_path === candidate.relative_path
                          }
                          onChange={() =>
                            setSubtitleDecision({
                              action: "use_as_english",
                              relative_path: candidate.relative_path,
                            })
                          }
                          disabled={importing}
                        />
                        <span>
                          <strong>{candidate.filename}</strong>
                          <small>{candidate.excerpt}</small>
                        </span>
                      </label>
                    ),
                  )}
                  <label className="film-review-subtitle-option film-review-subtitle-option--skip">
                    <input
                      type="radio"
                      name="subtitle-decision"
                      checked={subtitleDecision?.action === "skip"}
                      onChange={() => setSubtitleDecision({ action: "skip" })}
                      disabled={importing}
                    />
                    <span>
                      <strong>None of these</strong>
                    </span>
                  </label>
                </div>
              </fieldset>
            )}

            <label className="film-review-confirmation">
              <input
                ref={confirmationRef}
                type="checkbox"
                checked={confirmedFinished}
                onChange={(event) => setConfirmedFinished(event.target.checked)}
                disabled={importing}
                required
              />
              <span>Torrenting and seeding are finished. This item can be moved.</span>
            </label>

            {importError && (
              <p className="film-review-error" role="alert">
                {importError}
              </p>
            )}

            <div className="film-review-actions">
              <button
                type="button"
                className="films-button films-button--secondary"
                onClick={() => void handleImport(false)}
                disabled={importing}
              >
                {importing ? "Adding…" : "Add only"}
              </button>
              <button
                type="submit"
                className="films-button films-button--primary"
                disabled={importing}
              >
                {importing ? "Adding…" : "Add & ingest"}
              </button>
            </div>
          </form>
        )}
      </dialog>
    </div>
  );
}
