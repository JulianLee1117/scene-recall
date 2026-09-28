"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { MATCH_API, matchSceneHref, matchSearchKey, matchingRequest, primaryMatchCue } from "@/lib/matching";
import { seconds } from "@/lib/lab";
import type { LabClip } from "@/types/lab";
import type { MatchFocus, MatchReference, MatchSearchRequest, SceneMatch, SearchCohort } from "@/types/matching";
import MatchSourcePlayer from "../lab/MatchSourcePlayer";
import LabWorkspaceHeader, { LabEmptyState } from "../lab/LabWorkspaceHeader";
import SourceBrowser from "../lab/SourceBrowser";
import type { MatchFrames } from "../lab/matchFrames";
import MatchCutPreview from "./MatchCutPreview";
import MatchSearchStatus from "./MatchSearchStatus";
import MatchReferenceEditor from "./MatchReferenceEditor";
import MatchIcon from "./MatchIcon";
import { useMatchSearch } from "./useMatchSearch";
import styles from "./matchSearch.module.css";

const FOCUS_LABELS: Record<MatchFocus, string> = { auto: "all connections", position: "position", shape: "shape", subject: "subject movement", camera: "camera movement" };
const sourceFrames = (unitId: string, time: number, signal?: AbortSignal) => matchingRequest<MatchFrames>(`/frames?${new URLSearchParams({ unit_id: unitId, time: String(time) })}`, { signal });

function ReferenceThumbnail({ reference, onOpen }: { reference: LabClip; onOpen: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const time = reference.reference_time ?? reference.source_start;
  useEffect(() => { if (video.current && video.current.readyState >= 1) video.current.currentTime = time; }, [time, reference.film_id]);
  return <button className={styles.referenceThumbnail} onClick={onOpen} aria-label="Inspect reference scene"><video ref={video} key={reference.film_id} src={`${MATCH_API}/video/${encodeURIComponent(reference.film_id)}`} muted playsInline preload="metadata" onLoadedMetadata={(event) => { event.currentTarget.currentTime = time; }} /><span><MatchIcon name="adjust" /> Adjust</span></button>;
}

export default function MatchSearch() {
  const router = useRouter();
  const params = useSearchParams();
  const unitId = params.get("unit_id") ?? "";
  const queryTime = params.get("time");
  const savedSearchId = params.get("search_id") ?? "";
  const time = queryTime !== null && Number.isFinite(Number(queryTime)) && Number(queryTime) >= 0 ? Number(queryTime) : null;
  const search = useMatchSearch();
  const [reference, setReference] = useState<LabClip | null>(null);
  const [referenceOrigin, setReferenceOrigin] = useState("");
  const [cohorts, setCohorts] = useState<SearchCohort[]>([]);
  const [cohortId, setCohortId] = useState("");
  const [loading, setLoading] = useState(true);
  const [sourceError, setSourceError] = useState("");
  const [cohortError, setCohortError] = useState("");
  const [timing, setTiming] = useState<"nearby" | "fixed">("nearby");
  const [subjectPoint, setSubjectPoint] = useState<{ x: number; y: number } | null>(null);
  const [filmIds, setFilmIds] = useState<string[]>([]);
  const [includeSourceFilm, setIncludeSourceFilm] = useState(false);
  const [showReference, setShowReference] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [requestVersion, setRequestVersion] = useState(0);
  const started = useRef("");
  const handledResume = useRef("");
  const inclusionSource = useRef("");
  const previewTrigger = useRef<HTMLButtonElement | null>(null);
  const cohort = cohorts.find((row) => row.id === cohortId);
  const library = cohort?.library?.ready ? cohort.library : null;
  const scopeFilms = library?.films ?? cohort?.films ?? [];
  const candidates = search.job?.result?.candidates ?? [];
  const selectedBase = candidates.find((candidate) => candidate.id === selectedId);
  const selected = selectedBase ? search.previews[selectedBase.id] ?? selectedBase : undefined;
  const notices = search.job?.result?.notices ?? [];

  useEffect(() => {
    const controller = new AbortController();
    setCohortError("");
    matchingRequest<{ cohorts: SearchCohort[] }>("/cohorts", { signal: controller.signal })
      .then(({ cohorts: rows }) => { setCohorts(rows); setCohortId((current) => rows.some((row) => row.id === current) ? current : rows[0]?.id ?? ""); })
      .catch((reason) => { if (!controller.signal.aborted) setCohortError(reason.message); });
    return () => controller.abort();
  }, [requestVersion]);

  useEffect(() => {
    if (savedSearchId) return;
    const controller = new AbortController();
    started.current = "";
    if (inclusionSource.current !== unitId) { setIncludeSourceFilm(false); inclusionSource.current = unitId; }
    setReference(null); setReferenceOrigin(""); setSubjectPoint(null); setSourceError(""); setSelectedId(null); setShowReference(false);
    if (!unitId) { setLoading(false); return () => controller.abort(); }
    setLoading(true);
    matchingRequest<MatchReference>(`/reference?${new URLSearchParams({ unit_id: unitId, ...(time != null ? { time: String(time) } : {}) })}`, { signal: controller.signal })
      .then(({ reference: next }) => { setReference(next); setReferenceOrigin(`${unitId}:${time ?? "default"}`); })
      .catch((reason) => { if (!controller.signal.aborted) setSourceError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [unitId, time, savedSearchId, requestVersion]);

  useEffect(() => {
    if (!savedSearchId) return;
    const key = `${savedSearchId}:${requestVersion}`;
    if (handledResume.current === key) return;
    let abandoned = false;
    setLoading(true); setSourceError(""); setSelectedId(null); setShowReference(false);
    void search.restore(savedSearchId).then((saved) => {
      if (abandoned) return;
      handledResume.current = key;
      if (!saved?.reference || !saved.request) {
        setReference(null);
        setSourceError("This saved search could not be opened. Start a new search from the scene, or choose another scene.");
        return;
      }
      const options = saved.request;
      inclusionSource.current = options.reference.unit_id;
      setReference({ ...saved.reference, reference_time: options.reference.time, region: options.reference.region ?? null });
      setReferenceOrigin(`${options.reference.unit_id}:${options.reference.time}`);
      started.current = `${options.reference.unit_id}:${options.reference.time}`;
      setTiming(options.timing); setFilmIds(options.film_ids); setIncludeSourceFilm(options.include_source_film);
      setCohortId(options.cohort_id); setSubjectPoint(options.reference.subject_point ?? null);
    }).finally(() => { if (!abandoned) setLoading(false); });
    return () => { abandoned = true; };
  }, [savedSearchId, requestVersion]);

  function request(): MatchSearchRequest | null {
    if (!cohort || !reference?.unit_id) return null;
    return { cohort_id: cohort.id,
      reference: { unit_id: reference.unit_id, time: reference.reference_time ?? reference.source_start,
        ...(subjectPoint ? { subject_point: subjectPoint } : {}), ...(reference.region ? { region: reference.region } : {}) },
      focus: "auto", timing, film_ids: filmIds, include_source_film: includeSourceFilm,
      min_incoming_seconds: 1, allow_reframing: false };
  }
  function run() {
    const next = request();
    if (!next) return;
    setSelectedId(null); setShowReference(false);
    void search.start(next).then((created) => {
      if (!created) return;
      handledResume.current = `${created.id}:${requestVersion}`;
      router.replace(`/match?${new URLSearchParams({ unit_id: next.reference.unit_id, time: String(next.reference.time), search_id: created.id })}`, { scroll: false });
    });
  }

  useEffect(() => {
    const key = `${unitId}:${time ?? "default"}`;
    if (savedSearchId || !reference || !cohort || (!cohort.motion_ready && !cohort.subject_ready && !cohort.library?.ready) || referenceOrigin !== key || started.current === key) return;
    started.current = key;
    run();
  }, [reference, referenceOrigin, cohort, unitId, time, savedSearchId]);

  const searchReady = !!cohort && (cohort.motion_ready || cohort.subject_ready === true || cohort.library?.ready === true);
  const currentRequest = request();
  const changed = !!search.submitted && !!currentRequest && matchSearchKey({ ...search.submitted, focus: "auto" }) !== matchSearchKey(currentRequest);
  const resultForSource = search.submitted?.reference.unit_id === reference?.unit_id;
  const savedFocus = resultForSource && search.submitted?.focus !== "auto" ? search.submitted?.focus : null;
  const visible = resultForSource ? candidates : [];
  const scopedFilmCount = scopeFilms.filter((film) => (!filmIds.length || filmIds.includes(film.film_id)) && (includeSourceFilm || film.film_id !== reference?.film_id)).length;
  const coverage = resultForSource ? search.job?.result?.coverage : undefined;
  const preparedResult = coverage?.source !== "library_keyframes" && coverage?.window_count != null;
  const coverageLabel = preparedResult ? "Prepared sample" : library || coverage?.source === "library_keyframes" ? "Indexed library" : "Prepared sample";
  const coverageFilmCount = coverage?.film_count ?? scopedFilmCount;
  const referenceUnchanged = !!search.submitted && !!currentRequest && matchSearchKey({ ...search.submitted, reference: currentRequest.reference }) === matchSearchKey(search.submitted);
  const referenceSummary = resultForSource && referenceUnchanged ? search.job?.result?.reference_summary : undefined;

  function openPreview(candidate: SceneMatch, button: HTMLButtonElement) {
    previewTrigger.current = button; setSelectedId(candidate.id); void search.prepare(candidate);
  }
  function closePreview() { search.dismissPreview(); setSelectedId(null); previewTrigger.current?.focus(); }

  function toggleFilm(id: string, checked: boolean) {
    const selectedFilms = new Set(filmIds.length ? filmIds : scopeFilms.map((film) => film.film_id));
    if (checked) selectedFilms.add(id); else selectedFilms.delete(id);
    if (selectedFilms.size) setFilmIds(scopeFilms.every((film) => selectedFilms.has(film.film_id)) ? [] : [...selectedFilms]);
  }

  return <main className={styles.page}>
    <LabWorkspaceHeader title="Match Cuts" className={styles.workspaceHeader} />

    {(sourceError || cohortError) && <div className={styles.error} role="alert">{sourceError || cohortError} <button onClick={() => setRequestVersion((value) => value + 1)}>Retry</button>{savedSearchId && unitId && time != null && <button onClick={() => { handledResume.current = ""; router.replace(`/match?${new URLSearchParams({ unit_id: unitId, time: String(time) })}`); }}>Start new search</button>}</div>}
    {loading ? <p className={styles.pageLoading} role="status">Loading reference…</p> : !reference ? <LabEmptyState title="Find the next cut" description="Match a scene by position, shape or movement.">
      <button onClick={() => setChoosing(true)} title="Selecting a scene starts a search"><MatchIcon name="search" /> Choose a scene</button><small>Search starts when you select a scene.</small>
    </LabEmptyState> : <div className={styles.workspace}>
      <aside className={styles.reference} aria-label="Match reference">
        <header className={styles.panelHeader}><h2>Reference</h2><button className={styles.textButton} disabled={search.busy} onClick={() => setChoosing(true)}>Change scene</button></header>
        <div className={styles.referenceBody}>
          <ReferenceThumbnail reference={reference} onOpen={() => setShowReference(true)} />
          <h2 className={styles.sourceTitle}>{reference.title}</h2>
          <div className={styles.sourceTime}><time>{seconds(reference.reference_time ?? reference.source_start)}</time><span>{timing === "nearby" ? "±1s" : "Pinned frame"}</span></div>
          {referenceSummary?.kind === "people" && referenceSummary.count > 0 && <p className={styles.referenceSummary}>{referenceSummary.count} prominent {referenceSummary.count === 1 ? "person" : "people"}</p>}
          <button className={styles.secondary} onClick={() => setShowReference(true)}><MatchIcon name="adjust" /> Adjust reference</button>
          <form className={styles.controls} onSubmit={(event) => { event.preventDefault(); run(); }}>
            {search.busy ? <button type="button" className={styles.secondary} disabled={!search.job || search.job.cancel_requested} onClick={() => void search.cancel()}>{search.job?.cancel_requested ? "Stopping…" : search.job ? "Cancel search" : "Starting search…"}</button> : <button type="submit" className={styles.primary} disabled={!searchReady}><MatchIcon name="search" /> Find match cuts</button>}
          </form>
          {changed && !search.busy && <p className={styles.warning} role="status">Settings changed. Run search to update results.</p>}
        </div>
        <details className={styles.scope}>
          <summary>Search scope <span>{scopedFilmCount} {scopedFilmCount === 1 ? "film" : "films"}<MatchIcon name="chevron" /></span></summary>
          <div className={styles.options}>
            <p>{library ? `Full library index: ${library.frame_count.toLocaleString()} keyframes across ${library.film_count} films. Searches indexed keyframes, then checks nearby source frames in shortlisted scenes.` : `${cohort?.motion_count ?? 0} analyzed clip windows across ${cohort?.films.length ?? 0} films. This collection includes selected footage, not full films.`}</p>
            <fieldset disabled={search.busy}><legend>Films</legend>{scopeFilms.map((film) => <label key={film.film_id}><input type="checkbox" checked={!filmIds.length || filmIds.includes(film.film_id)} disabled={filmIds.length === 1 && filmIds[0] === film.film_id} onChange={(event) => toggleFilm(film.film_id, event.target.checked)} />{film.title}</label>)}</fieldset>
            {!!filmIds.length && <button className={styles.textButton} disabled={search.busy} onClick={() => setFilmIds([])}>Reset film selection</button>}
            <label className={styles.sourceScope}><input type="checkbox" aria-label="Include source film" checked={includeSourceFilm} disabled={search.busy} onChange={(event) => setIncludeSourceFilm(event.target.checked)} /> Include source film</label>
            {cohorts.length > 1 && <label>Collection <select value={cohortId} disabled={search.busy} onChange={(event) => { setCohortId(event.target.value); setFilmIds([]); }}>{cohorts.map((row) => <option key={row.id} value={row.id}>{row.library?.ready ? `${row.library.film_count} films · indexed library` : `${row.films.length} films · ${row.motion_count} windows`}</option>)}</select></label>}
          </div>
        </details>
        <p className={styles.coverage}>{coverageLabel} · {coverageFilmCount} {coverageFilmCount === 1 ? "film" : "films"}</p>
        {notices.length > 0 && resultForSource && <details className={styles.evidenceNote}><summary>Analysis notes ({notices.length})</summary>{notices.map((notice, index) => <p key={index}>{notice}</p>)}</details>}
      </aside>
      <section className={styles.results} aria-label="Match cut results">
        <header className={styles.panelHeader}><h2>Matches {visible.length > 0 && <span className={styles.resultCount}>{visible.length}</span>}</h2><span>Best match first</span></header>
        <MatchSearchStatus job={resultForSource ? search.job : null} busy={search.busy} count={visible.length} />
        {savedFocus && <p className={styles.warning}>Saved {FOCUS_LABELS[savedFocus]} search. Run search to compare all connections.</p>}
        {search.error && <p className={styles.error} role="alert">{search.error}</p>}
        {!visible.length && !search.busy && <div className={styles.emptyResults}><p>{search.job?.status === "completed" ? "No matches in this collection" : "No results yet"}</p><span>{search.job?.status === "completed" ? "Try a different moment or adjust the search scope." : "Results will appear here."}</span></div>}
        <div className={styles.resultList}>{visible.map((base, index) => {
          const candidate = search.previews[base.id] ?? base;
          const primary = primaryMatchCue(candidate);
          const secondary = candidate.cues?.filter((cue) => cue.code !== primary?.code) ?? [];
          return <article className={styles.result} key={candidate.id}>
            <span className={styles.resultRank}>{String(index + 1).padStart(2, "0")}</span>
            <button className={styles.resultImage} onClick={(event) => openPreview(candidate, event.currentTarget)} aria-label={`Preview cut ${index + 1} to ${candidate.film_title}`}>{candidate.frame_url ? <img src={`${MATCH_API}${candidate.frame_url}`} alt={`Incoming frame from ${candidate.film_title}`} loading={index < 3 ? "eager" : "lazy"} decoding="async" /> : <span className={styles.thumbnailPending}>Preparing frame…</span>}<span className={styles.playOverlay}><MatchIcon name="play" /></span></button>
            <div className={styles.resultContent}><div className={styles.resultTitle}><h3>{candidate.film_title}</h3><time>{seconds(candidate.incoming.reference_time ?? candidate.incoming.source_start)}</time></div>
              <div className={styles.cueList}>{primary && <span className={styles.cue}>{primary.label}</span>}{secondary.map((cue) => <span key={cue.code} className={styles.cue}>{cue.label}</span>)}</div>
              <p>{primary?.description ?? candidate.evidence}</p>
              <div className={styles.resultActions}><button className={styles.textButton} onClick={(event) => openPreview(candidate, event.currentTarget)}><MatchIcon name="play" /> {search.preparingId === candidate.id ? "Preparing…" : "Preview cut"}</button>
                {secondary.length > 0 && <details><summary>Details</summary>{secondary.map((cue) => <p key={cue.code}><strong>{cue.label}</strong> · {cue.description}</p>)}</details>}
              </div>
            </div>
          </article>;
        })}</div>
      </section>
    </div>}

    {choosing && <SourceBrowser context="match" filmIds={[]} replacing={!!reference} onClose={() => setChoosing(false)} onSelect={(shot) => { setChoosing(false); router.push(matchSceneHref(shot)); }} />}
    {showReference && reference && <MatchReferenceEditor onClose={() => setShowReference(false)}><MatchSourcePlayer clip={reference} disabled={search.busy} timing={timing} subjectPoint={subjectPoint} subjectReady={cohort?.subject_ready === true} onTiming={setTiming} onSubjectPoint={setSubjectPoint} onChange={(patch) => setReference((current) => current ? { ...current, ...patch } : current)} loadFrames={sourceFrames} maxHeight={360} /></MatchReferenceEditor>}
    {selected && search.job && <MatchCutPreview candidate={selected} searchId={search.job.id} preparing={!!search.preparingId || search.busy} onPrepare={(force) => void search.prepare(selected, { force })} onClose={closePreview} />}
  </main>;
}
