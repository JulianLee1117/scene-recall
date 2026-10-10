"use client";

import { useEffect, useRef, useState } from "react";
import { labRequest, mediaUrl } from "@/lib/lab";
import LabWorkspaceHeader, { LabEmptyState } from "@/features/lab/kit/LabWorkspaceHeader";
import SourceBrowser from "@/features/lab/kit/SourceBrowser";
import { DEFAULT_RETIME, restoredRetime, speedTitle, cutSpeedTitle, fileSize, renderFileSummary, qualityTitle, activeRender, matchingSourceEndpoints, mergeRenderHistory, recipeTitle, restoredRecipe, samePair, sourceFromResult, sourcePayload, timingSweep, validatePair, variantTiming, workingChanges,
  type TransitionRetime, type SourceSelection, type TransitionCatalog, type TransitionJob, type TransitionRecipe, type TransitionRequest } from "./transitions";
import styles from "./transitions.module.css";
import AIHandoff from "./AIHandoff";
import SourceCard from "./SourceCard";
import PairTimeline from "./PairTimeline";
import { fastEditTiming } from "./fast-edit-timing";
import { sourceBoundary, seamCenter } from "./transitions";
import programStyles from "./program-workspace.module.css";
import RecipeControls from "./RecipeControls";
import SpeedControls from "./SpeedControls";
import ComparisonPlayer, { type AuditionAudio } from "./ComparisonPlayer";
import useNotebook from "./useNotebook";
import type { VariantNote } from "./notebook";

export default function TransitionWorkspace() {
  const [catalog, setCatalog] = useState<TransitionCatalog | null>(null);
  const [recipe, setRecipe] = useState<TransitionRecipe | null>(null);
  const [retime, setRetime] = useState<TransitionRetime>({ ...DEFAULT_RETIME });
  const [a, setA] = useState<SourceSelection | null>(null);
  const [b, setB] = useState<SourceSelection | null>(null);
  const [output, setOutput] = useState<TransitionRequest["output"]>({ aspect: "landscape", quality: "draft" });
  const [jobs, setJobs] = useState<TransitionJob[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [compareId, setCompareId] = useState<string | null>(null);
  const [picker, setPicker] = useState<"a" | "b" | null>(null);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [retry, setRetry] = useState(0);
  const [mode, setMode] = useState<"local" | "ai">("local");
  const [monitorSide, setMonitorSide] = useState<"a" | "b" | null>(null);
  const [sourceInspection, setSourceInspection] = useState<{ side: "a" | "b"; film: string; time: number; token: number } | null>(null);
  const [sourcePosition, setSourcePosition] = useState<{ side: "a" | "b"; sourceTime: number } | null>(null);
  const [previewTime, setPreviewTime] = useState(0);
  const [previewSeek, setPreviewSeek] = useState<{ time: number; token: number; jobId: string } | undefined>();
  const [filmDurations, setFilmDurations] = useState<{ a?: number; b?: number }>({});
  const inspectionToken = useRef(0);
  const [bpm, setBpm] = useState(140);
  const [group, setGroup] = useState("motion");
  const [audio, setAudio] = useState<AuditionAudio | null>(null);
  const [favoritesOnly, setFavoritesOnly] = useState(false);
  const [historyLimit, setHistoryLimit] = useState(6);
  const [recipeName, setRecipeName] = useState("");
  const [notice, setNotice] = useState("");
  const [previewIntent, setPreviewIntent] = useState<{ id: string; previousId: string | null; comparison: boolean } | null>(null);
  const { notebook, setNotebook, error: notebookError } = useNotebook();
  const alive = useRef(true);
  const posting = useRef(false);
  const draftStarted = useRef(false);
  const selectionRevision = useRef(0);
  const historyRevision = useRef(0);
  const selectedRef = useRef(selectedId); selectedRef.current = selectedId;
  useEffect(() => { alive.current = true; if (new URLSearchParams(window.location.search).get("mode") === "ai") setMode("ai"); return () => { alive.current = false; }; }, []);
  useEffect(() => () => { if (audio?.url) URL.revokeObjectURL(audio.url); }, [audio?.url]);
  useEffect(() => {
    const abort = new AbortController(); setLoading(true); setError("");
    const selectionAtStart = selectionRevision.current, historyAtStart = historyRevision.current;
    Promise.all([labRequest<TransitionCatalog>("/transitions/recipes", { signal: abort.signal }), labRequest<{ renders: TransitionJob[] }>("/transitions/renders?limit=100", { signal: abort.signal })])
      .then(([data, history]) => {
        if (abort.signal.aborted) return;
        setCatalog(data); setRecipe((current) => current ?? data.recipes.find((item) => item.id === "whip-pan")?.defaults ?? data.recipes[0]?.defaults ?? null);
        setJobs((current) => mergeRenderHistory(current, history.renders, historyRevision.current !== historyAtStart));
        if (selectionRevision.current !== selectionAtStart) return;
        const linked = new URLSearchParams(window.location.search).get("render");
        if (selectedRef.current) return;
        if (linked && !history.renders.some((job) => job.id === linked)) {
          void labRequest<TransitionJob>(`/transitions/renders/${encodeURIComponent(linked)}`, { signal: abort.signal }).then((job) => {
            if (!abort.signal.aborted) {
              historyRevision.current++; setJobs((current) => mergeRenderHistory(current, [job], true));
              if (selectionRevision.current === selectionAtStart && !selectedRef.current) { setSelectedId(job.id); hydrateInitial(job, data); }
            }
          }).catch((reason) => { if (!abort.signal.aborted) setError(reason.message); });
        } else {
          const restored = history.renders.find((job) => job.id === linked) ?? history.renders.find((job) => job.status === "completed");
          setSelectedId(restored?.id ?? null); if (restored) hydrateInitial(restored, data);
        }
      }).catch((reason) => { if (!abort.signal.aborted) setError(reason.message || "Could not load Transitions."); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [retry]);

  const pendingIds = jobs.filter(activeRender).map((job) => job.id).join(",");
  useEffect(() => {
    if (!pendingIds) { setPollError(""); return; }
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      const results = await Promise.allSettled(pendingIds.split(",").map((id) => labRequest<TransitionJob>(`/transitions/renders/${id}`, { signal: abort.signal })));
      if (abort.signal.aborted) return;
      const updates = results.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
      if (updates.length) historyRevision.current++;
      setJobs((current) => current.map((job) => updates.find((update) => update.id === job.id) ?? job));
      const failure = results.find((result) => result.status === "rejected");
      setPollError(failure?.status === "rejected" ? failure.reason?.message || "Render status unavailable; retrying." : "");
      timer = setTimeout(poll, 1600);
    }
    void poll();
    return () => { abort.abort(); clearTimeout(timer); };
  }, [pendingIds]);

  const selected = jobs.find((job) => job.id === selectedId) ?? null;
  const rendered = selected?.status === "completed" && selected.result ? selected : null;
  const definition = catalog?.recipes.find((item) => item.id === recipe?.id);
  const invalid = validatePair(a, b, recipe, catalog?.limits.max_clip_seconds ?? 12, retime);
  const fastTiming = fastEditTiming(a, b, recipe);
  const currentRequest: TransitionRequest | null = a && b && recipe ? { outgoing: sourcePayload(a), incoming: sourcePayload(b), recipe, retime, output } : null;
  const sourceEndpoints = matchingSourceEndpoints(rendered, currentRequest);
  const comparable = rendered ? jobs.filter((job) => job.id !== rendered.id && job.status === "completed" && job.result && samePair(job.request, rendered.request)) : [];
  const compare = comparable.find((job) => job.id === compareId);
  const differences = rendered ? workingChanges(rendered.request, currentRequest, definition) : [];
  const olderRender = !!rendered && !!catalog?.renderer_version && rendered.result!.renderer_version !== catalog.renderer_version;
  const pendingPreview = previewIntent ? jobs.find((job) => job.id === previewIntent.id) : null;
  const railPair = rendered?.request ?? currentRequest;
  const pairVersions = railPair ? jobs.filter((job) => samePair(job.request, railPair)).slice(0, 12) : [];
  const sweep = currentRequest ? timingSweep(currentRequest, catalog?.limits.max_clip_seconds ?? 12) : [];
  const groups = [...new Set((catalog?.recipes ?? []).map((item) => item.group ?? (item.id === "hard-cut" ? "baseline" : "motion")))];
  const visibleGroup = groups.includes(group) ? group : groups[0];
  const history = favoritesOnly ? jobs.filter((job) => notebook.variants[job.id]?.favorite) : jobs;
  const aiPairChanged = !!rendered && (!currentRequest || !samePair(rendered.request, currentRequest));
  const aiNeedsPreparation = !rendered || aiPairChanged || restoredRetime(rendered.request.retime).mode !== "off";
  const aiBaseline = catalog?.recipes.find((item) => item.id === "hard-cut");
  const aiPreparation: TransitionRequest | null = currentRequest && aiBaseline ? { ...currentRequest, recipe: { ...aiBaseline.defaults }, retime: { ...DEFAULT_RETIME }, output: { ...currentRequest.output, quality: "draft" } } : null;
  const aiPreparationProblem = validatePair(a, b, aiBaseline?.defaults ?? null, catalog?.limits.max_clip_seconds ?? 12, DEFAULT_RETIME);

  useEffect(() => {
    if (!previewIntent || !pendingPreview || pendingPreview.status !== "completed" || !pendingPreview.result) return;
    const previous = jobs.find((job) => job.id === previewIntent.previousId);
    if (previewIntent.comparison && previous?.status === "completed" && previous.result && samePair(previous.request, pendingPreview.request)) {
      setSelectedId(previous.id); setCompareId(pendingPreview.id === previous.id ? null : pendingPreview.id);
    } else {
      setSelectedId(pendingPreview.id);
      setCompareId(previous?.status === "completed" && previous.result && previous.id !== pendingPreview.id && samePair(previous.request, pendingPreview.request) ? previous.id : null);
      const url = new URL(window.location.href); url.searchParams.set("render", pendingPreview.id); window.history.replaceState(null, "", url);
    }
    setPreviewIntent(null);
  }, [previewIntent, pendingPreview, jobs]);

  function selectJob(job: TransitionJob) {
    setMonitorSide(null); setPreviewSeek(undefined);
    selectionRevision.current++; selectedRef.current = job.id; setPreviewIntent(null); setSelectedId(job.id); setCompareId(null);
    const url = new URL(window.location.href); url.searchParams.set("render", job.id); window.history.replaceState(null, "", url);
  }
  function reuse(job: TransitionJob) {
    reuseRequest(job.request, job.source_titles);
  }
  function selectComparison(id: string | null) { setMonitorSide(null); selectionRevision.current++; setPreviewIntent(null); setCompareId(id); }
  function chooseMode(next: "local" | "ai") {
    setMode(next); const url = new URL(window.location.href); url.searchParams.set("mode", next); window.history.replaceState(null, "", url);
  }
  function changeSource(side: "a" | "b", source: SourceSelection) {
    draftStarted.current = true;
    if ((side === "a" ? a : b)?.film_id !== source.film_id) setFilmDurations((current) => ({ ...current, [side]: undefined }));
    if (side === "a") setA(source); else setB(source);
  }
  function inspectSource(side: "a" | "b", time?: number) {
    const source = side === "a" ? a : b;
    if (!source) { setPicker(side); return; }
    setMonitorSide(side);
    const target = time ?? (side === "a" ? Math.max(source.source_start, source.source_end - .1) : source.source_start);
    setSourceInspection({ side, film: source.film_id, time: target, token: ++inspectionToken.current });
  }
  function trimSource(side: "a" | "b", edge: "source_start" | "source_end", time: number) {
    const source = side === "a" ? a : b;
    if (!source) return;
    const next = sourceBoundary(source, edge, time, filmDurations[side] ?? Infinity);
    if (!next) return;
    changeSource(side, { ...source, ...next });
    inspectSource(side, edge === "source_start" ? next.source_start : Math.max(next.source_start, next.source_end - 1 / 30));
  }
  function changeRetime(next: TransitionRetime) { draftStarted.current = true; setRetime(next); }
  function changeRecipe(next: TransitionRecipe) { draftStarted.current = true; setRecipe(next); }
  function changeOutput(next: TransitionRequest["output"]) { draftStarted.current = true; setOutput(next); }
  function hydrateInitial(job: TransitionJob, data: TransitionCatalog) {
    if (!draftStarted.current) reuseRequest(job.request, job.source_titles, data);
  }
  function reuseRequest(request: TransitionRequest, titles?: { outgoing: string; incoming: string }, data = catalog) {
    const preset = data?.recipes.find((item) => item.id === request.recipe.id);
    if (!preset) { setError("This saved recipe is unavailable in the current renderer. Choose another effect."); return; }
    draftStarted.current = true;
    setFilmDurations((current) => ({ a: a?.film_id === request.outgoing.film_id ? current.a : undefined, b: b?.film_id === request.incoming.film_id ? current.b : undefined }));
    setSourceInspection(null); setSourcePosition(null);
    setA({ ...request.outgoing, title: titles?.outgoing || "Clip A" }); setB({ ...request.incoming, title: titles?.incoming || "Clip B" });
    setRecipe(restoredRecipe(request.recipe, preset)); setRetime(restoredRetime(request.retime)); setGroup(preset.group ?? "motion"); setOutput({ ...request.output }); setError("");
  }
  async function enqueue(requests: TransitionRequest[], comparison = false) {
    if (posting.current || !requests.length) return;
    const batch = requests.slice(0, 3);
    const problem = batch.map((request) => validatePair(request.outgoing, request.incoming, request.recipe, catalog?.limits.max_clip_seconds ?? 12, request.retime)).find(Boolean);
    if (problem) { setError(problem); return; }
    if (mode === "local") setMonitorSide(null);
    posting.current = true; setSubmitting(true); setError(""); setNotice(""); let queued = 0, reused = 0;
    const revision = selectionRevision.current, previousId = rendered?.id ?? null;
    const queuedJobs: TransitionJob[] = [];
    const desiredFrames = Math.round((comparison ? requests[0].recipe.duration : recipe?.duration ?? requests[0].recipe.duration) * 30);
    try {
      for (const request of batch) {
        if (!alive.current) break;
        const job = await labRequest<TransitionJob>("/transitions/renders", { method: "POST", body: JSON.stringify(request) }); queued++; if (job.reused) reused++;
        queuedJobs.push(job);
        if (alive.current) { historyRevision.current++; setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]); }
      }
      if (alive.current && queued > 1) setNotice(`${queued} timing variants ${reused ? `ready or queued · ${reused} reused` : "queued"}. The center timing opens when ready; compare the others below the preview.`);
      else if (alive.current && reused) setNotice("Reused the matching saved render.");
    } catch (reason) { if (alive.current) setError(`${queued ? `${queued} variant${queued > 1 ? "s" : ""} already queued. ` : ""}${reason instanceof Error ? reason.message : "Could not start the render."}`); }
    finally {
      posting.current = false;
      if (alive.current) {
        setSubmitting(false);
        if (queuedJobs.length && selectionRevision.current === revision) {
          const target = [...queuedJobs].sort((a, b) => Math.abs(Math.round(a.request.recipe.duration * 30) - desiredFrames) - Math.abs(Math.round(b.request.recipe.duration * 30) - desiredFrames))[0];
          setPreviewIntent({ id: target.id, previousId, comparison });
          if (!previousId) setSelectedId(target.id);
        }
      }
    }
  }
  function render(recipeOverride?: TransitionRecipe) { setMonitorSide(null); if (currentRequest) void enqueue([{ ...currentRequest, recipe: recipeOverride ?? currentRequest.recipe }]); }
  function compareBaseline(withoutSpeed = false) {
    setMonitorSide(null);
    const baseline = catalog?.recipes.find((item) => item.id === "hard-cut");
    if (!rendered || !baseline) return;
    const target: TransitionRequest = { ...rendered.request, recipe: withoutSpeed ? rendered.request.recipe : baseline.defaults,
      retime: withoutSpeed ? { ...DEFAULT_RETIME } : restoredRetime(rendered.request.retime) };
    const targetDefinition = catalog?.recipes.find((item) => item.id === target.recipe.id);
    const existing = jobs.find((job) => job.id !== rendered.id && job.status === "completed" && job.result && samePair(job.request, target)
      && workingChanges(job.request, target, targetDefinition).length === 0
      && (!catalog?.renderer_version || job.result.renderer_version === catalog.renderer_version));
    if (existing) { selectionRevision.current++; setPreviewIntent(null); setCompareId(existing.id); }
    else void enqueue([target], true);
  }
  function updateNote(id: string, patch: Partial<VariantNote>) {
    setNotebook((current) => ({ ...current, variants: Object.fromEntries([[id, { ...(current.variants[id] ?? { favorite: false, note: "" }), ...patch }], ...Object.entries(current.variants).filter(([key]) => key !== id)].slice(0, 500)) }));
  }
  function saveRecipe() {
    if (!currentRequest || invalid) return;
    const name = recipeName.trim() || `${recipeTitle(currentRequest.recipe.id)} · ${Math.round(currentRequest.recipe.duration * 30)}f`;
    setNotebook((current) => ({ ...current, recipes: [{ id: crypto.randomUUID(), name: name.slice(0, 100), request: structuredClone(currentRequest), source_titles: { outgoing: a!.title, incoming: b!.title }, saved_at: Date.now() }, ...current.recipes].slice(0, 50) }));
    setRecipeName(""); setNotice(`“${name}” saved in this browser.`);
  }
  async function showFavorites() {
    setFavoritesOnly(!favoritesOnly); setHistoryLimit(6);
    if (favoritesOnly) return;
    const missing = Object.keys(notebook.variants).filter((id) => notebook.variants[id].favorite && !jobs.some((job) => job.id === id)).slice(0, 20);
    const restored = await Promise.allSettled(missing.map((id) => labRequest<TransitionJob>(`/transitions/renders/${encodeURIComponent(id)}`)));
    if (!alive.current) return;
    const found = restored.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
    if (found.length) { historyRevision.current++; setJobs((current) => [...current, ...found.filter((item) => !current.some((job) => job.id === item.id))]); }
  }
  async function cancel(job: TransitionJob) {
    try { const next = await labRequest<TransitionJob>(`/transitions/renders/${job.id}/cancel`, { method: "POST" }); if (alive.current) { historyRevision.current++; setJobs((current) => current.map((item) => item.id === next.id ? next : item)); } }
    catch (reason) { if (alive.current) setError(reason instanceof Error ? reason.message : "Could not cancel the render."); }
  }

  return <main>
    <LabWorkspaceHeader title="Transitions" />
    <div className={styles.workspace}>
    <p className={programStyles.intro}>Choose two clips on the timeline. Shape their handoff in the monitor.</p>
    <div className={styles.modeSwitch} role="tablist" aria-label="Transition workflow" onKeyDown={(event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault(); const next = event.key === "Home" ? "local" : event.key === "End" ? "ai" : mode === "local" ? "ai" : "local";
      chooseMode(next); event.currentTarget.querySelector<HTMLButtonElement>(next === "local" ? "#local-tab" : "#ai-tab")?.focus();
    }}><button id="local-tab" type="button" role="tab" tabIndex={mode === "local" ? 0 : -1} aria-selected={mode === "local"} aria-controls="local-panel" onClick={() => chooseMode("local")}><strong>Local effects</strong><span>Shape, render &amp; compare</span></button>
      <button id="ai-tab" type="button" role="tab" tabIndex={mode === "ai" ? 0 : -1} aria-selected={mode === "ai"} aria-controls="ai-panel" onClick={() => chooseMode("ai")}><strong>AI transitions</strong><span>Generate or bring a result</span></button></div>
    {(error || pollError) && <div className={styles.errorBanner} role="alert"><span>{error || pollError}</span><button type="button" onClick={() => setRetry((value) => value + 1)}>Reload lab</button></div>}
    {notice && <p className={styles.notice} role="status">{notice}<button type="button" aria-label="Dismiss notice" onClick={() => setNotice("")}>×</button></p>}
    {loading && !catalog ? <p className={styles.loading} role="status">Loading transition tools…</p> : !catalog ? <LabEmptyState title="Transition tools are unavailable" description="Check that the Scene Recall API is running, then reload the lab."><button className={styles.secondary} onClick={() => setRetry((value) => value + 1)}>Try again</button></LabEmptyState> : <>
      <div id="local-panel" role="tabpanel" aria-labelledby="local-tab" hidden={mode !== "local"}>
      <div className={styles.workbench}>
        <div className={styles.stage}>
          <div className={programStyles.program}>
            <div className={programStyles.header}><h2>{monitorSide ? `Clip ${monitorSide.toUpperCase()} · working footage` : "Transition monitor"}</h2>
              <div className={programStyles.monitorTabs} aria-label="Monitor view">
                <button type="button" aria-pressed={monitorSide === "a"} disabled={!a} onClick={() => inspectSource("a")}>Clip A</button>
                <button type="button" aria-pressed={monitorSide === null} onClick={() => setMonitorSide(null)}>Transition</button>
                <button type="button" aria-pressed={monitorSide === "b"} disabled={!b} onClick={() => inspectSource("b")}>Clip B</button>
              </div>
            </div>
            <div className={programStyles.screen}>
              {(["a", "b"] as const).map((side) => <div key={side} hidden={monitorSide !== side}>
                <SourceCard monitor label={side === "a" ? "A" : "B"} source={side === "a" ? a : b} endpoint={sourceEndpoints?.[side]} aspect={output.aspect}
                  disabled={submitting || !!picker || mode !== "local" || monitorSide !== side}
                  inspection={sourceInspection?.side === side ? sourceInspection : undefined}
                  onPositionChange={(time) => setSourcePosition((current) => current?.side === side && current.sourceTime === time ? current : { side, sourceTime: time })}
                  onDurationKnown={(duration) => setFilmDurations((current) => current[side] === duration ? current : { ...current, [side]: duration })}
                  onChoose={() => setPicker(side)} onChange={(source) => changeSource(side, source)} />
              </div>)}
              <div hidden={monitorSide !== null}>
                {rendered ? <ComparisonPlayer job={rendered} compare={compare} audio={audio} onAudioChange={setAudio} active={!picker && mode === "local" && monitorSide === null}
                  hideScrubber externalSeek={previewSeek} onTimeChange={setPreviewTime} />
                  : selected && activeRender(selected) ? <div className={programStyles.empty} role="status"><strong>{selected.status === "queued" ? "Waiting to render" : "Rendering your transition"}</strong><p>{selected.progress}</p><button type="button" className={styles.secondary} onClick={() => void cancel(selected)}>Cancel render</button></div>
                  : selected && selected.status !== "completed" ? <div className={programStyles.empty}><strong>Preview did not finish</strong><p>{selected.error && selected.error.length < 220 ? selected.error : "Load the saved settings to try again."}</p><button type="button" className={styles.secondary} onClick={() => reuse(selected)}>Load settings to retry</button></div>
                  : <div className={programStyles.empty}><strong>{a && b ? "Your clips are ready" : "Build an A → B transition"}</strong><p>{a && b ? "Inspect or trim the clips on the timeline below, then preview the transition." : "Choose the first and second clip below. Click either clip to inspect it here."}</p><div className={programStyles.emptyActions}>{!a && <button type="button" className={styles.secondary} onClick={() => setPicker("a")}>Choose clip A</button>}{!b && <button type="button" className={styles.secondary} onClick={() => setPicker("b")}>Choose clip B</button>}{a && b && <button type="button" className={styles.primary} disabled={!!invalid || submitting} onClick={() => render()}>Preview transition</button>}</div></div>}
              </div>
            </div>
            {rendered && differences.length > 0 && <div className={programStyles.update} role="status"><span>Preview needs update · {differences.join(", ").toLowerCase()}. The monitor's transition is the last saved render.</span><button type="button" disabled={!!invalid || submitting} onClick={() => render()}>Update preview</button></div>}
            {pendingPreview && pendingPreview.id !== rendered?.id && activeRender(pendingPreview) && <div className={programStyles.update} role="status"><span>Rendering new preview · the previous version stays available</span><button type="button" disabled={pendingPreview.cancel_requested} onClick={() => void cancel(pendingPreview)}>Cancel</button></div>}
            <div className={programStyles.sequence}>
              <div className={programStyles.sequenceHeader}><strong>Your edit · A → B</strong><div><button type="button" disabled={!a || !b || submitting} onClick={() => { draftStarted.current = true; setA(b); setB(a); setFilmDurations((current) => ({ a: current.b, b: current.a })); setSourceInspection(null); setSourcePosition(null); }}>Swap clips</button><button type="button" className={programStyles.previewButton} disabled={!!invalid || submitting} onClick={() => render()}>Preview edit</button></div></div>
              <PairTimeline a={a} b={b} recipe={recipe} retime={retime} selectedSide={monitorSide} position={monitorSide ? sourcePosition : null}
                filmDurations={filmDurations} disabled={submitting || !!picker || mode !== "local"}
                playbackTime={monitorSide === null && rendered && !differences.length ? previewTime : null}
                onPreviewSeek={monitorSide === null && rendered && !differences.length ? (time) => setPreviewSeek({ time, token: ++inspectionToken.current, jobId: rendered.id }) : undefined}
                onSelect={(side) => inspectSource(side)} onChoose={(side) => setPicker(side)} onTrim={trimSource} onSeek={inspectSource}
                onSelectTransition={() => { setMonitorSide(null); if (rendered && !differences.length) setPreviewSeek({ time: seamCenter(rendered), token: ++inspectionToken.current, jobId: rendered.id }); }} onDuration={(duration) => { if (recipe) changeRecipe({ ...recipe, duration }); }} />
            </div>
          </div>
          {rendered && <details className={programStyles.details}><summary>Saved preview · {recipeTitle(rendered.request.recipe.id)} · {variantTiming(rendered.request.recipe)} · downloads & comparison</summary>
            <h2>Saved preview · {recipeTitle(rendered.request.recipe.id)}</h2>
            <div className={styles.previewReceipt}>
              <span>Saved preview · {variantTiming(rendered.request.recipe)} · {speedTitle(rendered.request.retime)} · {qualityTitle(rendered.request.output.quality)}</span>
              <p>{rendered.source_titles?.outgoing || "Clip A"} → {rendered.source_titles?.incoming || "Clip B"}</p>
              <small>{renderFileSummary(rendered)}</small>{cutSpeedTitle(rendered) && <small>{cutSpeedTitle(rendered)}</small>}
              <div className={styles.previewState}><span>{differences.length ? `Working settings changed · ${differences.join(", ").toLowerCase()}. Preview uses its saved source pair.` : "Matches working settings"}</span><button type="button" onClick={() => reuse(rendered)}>Load to refine</button></div>
              {olderRender && <small>Earlier renderer · render again to try the updated effects.</small>}
            </div>
              <div className={styles.renderActions}>
                <a href={`${mediaUrl(rendered.result!.preview_url)}?download=1`} download>Download MP4{fileSize(rendered.storage?.output_bytes) ? ` · ${fileSize(rendered.storage?.output_bytes)}` : ""} ↓</a><a href={mediaUrl(rendered.result!.manifest_url)} download>Recipe &amp; manifest ↓</a>
                {rendered.request.output.quality !== "export" && <button className={styles.textButton} type="button" disabled={submitting || !!(pendingPreview && activeRender(pendingPreview))} onClick={() => void enqueue([{ ...rendered.request, output: { ...rendered.request.output, quality: "export" } }])}>Render 1080p copy</button>}
                {rendered.request.recipe.id !== "hard-cut" && <button className={styles.textButton} type="button" disabled={submitting || !!(pendingPreview && activeRender(pendingPreview) && previewIntent?.comparison)} onClick={() => compareBaseline()}>Compare hard cut{restoredRetime(rendered.request.retime).mode !== "off" ? " · same speed" : ""}</button>}
                {restoredRetime(rendered.request.retime).mode !== "off" && <button className={styles.textButton} type="button" disabled={submitting || !!(pendingPreview && activeRender(pendingPreview) && previewIntent?.comparison)} onClick={() => compareBaseline(true)}>Compare without speed</button>}
              </div>
              {comparable.length > 0 && <label className={styles.compareLabel}>Compare the saved pair<select value={compare?.id ?? ""} aria-label="Compare render" onChange={(event) => selectComparison(event.target.value || null)}>
                <option value="">Single preview</option>{comparable.map((job) => <option key={job.id} value={job.id}>{recipeTitle(job.request.recipe.id)} · {variantTiming(job.request.recipe)} · {speedTitle(job.request.retime)} · {qualityTitle(job.request.output.quality)}{job.result?.renderer_version !== catalog.renderer_version ? " · earlier" : ""} · {job.id.slice(0, 6)}</option>)}
              </select></label>}
              <details className={styles.variantNotes}><summary>Keep a note <span>{notebook.variants[rendered.id]?.favorite ? "★ Favorite" : "This browser"}</span></summary><div>
                <button type="button" className={styles.secondary} aria-pressed={!!notebook.variants[rendered.id]?.favorite} onClick={() => updateNote(rendered.id, { favorite: !notebook.variants[rendered.id]?.favorite })}>{notebook.variants[rendered.id]?.favorite ? "★ Favorited" : "☆ Favorite variant"}</button>
                <label>What worked?<textarea aria-label="Variant notes" rows={3} maxLength={2000} value={notebook.variants[rendered.id]?.note ?? ""} placeholder="Timing, energy, a detail to try next…" onChange={(event) => updateNote(rendered.id, { note: event.target.value })} /></label><small>Favorites and notes are saved in this browser.</small>
              </div></details>
          </details>}
          <details className={programStyles.details}><summary>Compare versions of this pair</summary>
            {pairVersions.length > 1 && <div className={styles.variantRail}><div><strong>{rendered ? "Versions of saved preview pair" : "Versions of working pair"}</strong><small>Preview a version, then load it to refine</small></div><div className={styles.variantChips}>{pairVersions.map((item) => <div key={item.id} data-selected={item.id === selectedId} data-comparing={item.id === compare?.id}>
              <button type="button" aria-pressed={item.id === selectedId} onClick={() => selectJob(item)}><strong>{variantTiming(item.request.recipe)}</strong><span>{recipeTitle(item.request.recipe.id)}{restoredRetime(item.request.retime).mode !== "off" ? ` · ${speedTitle(item.request.retime)}` : ""}</span><small>{item.status === "completed" ? item.id === compare?.id ? "Comparing" : "Ready" : item.status}</small></button>
              {rendered && item.id !== rendered.id && item.status === "completed" && item.result && samePair(rendered.request, item.request) && <button type="button" aria-label={`Compare ${variantTiming(item.request.recipe)} ${recipeTitle(item.request.recipe.id)} ${item.id.slice(0, 6)}`} aria-pressed={compare?.id === item.id} onClick={() => selectComparison(compare?.id === item.id ? null : item.id)}>{compare?.id === item.id ? "Remove comparison" : "Compare"}</button>}
            </div>)}</div></div>}          </details>
        </div>
        <aside className={styles.settings} aria-label="Transition controls">
          <div className={styles.sectionTitle}><h2>Working effect</h2><span>{catalog.recipes.length} recipes</span></div>
          <div className={styles.recipeGroups} role="group" aria-label="Transition families">{groups.map((family) => <button type="button" key={family} aria-pressed={visibleGroup === family} onClick={() => setGroup(family)}>{({ motion: "Motion", light: "Light", reveal: "Reveal", baseline: "Basics", experimental: "Experimental" } as Record<string, string>)[family] ?? family}</button>)}</div>
          {visibleGroup === "experimental" && <p className={styles.experimentalNote}>Experimental looks. Try them on your footage and refine the strength.</p>}
          <div className={styles.presets}>{catalog.recipes.filter((item) => (item.group ?? (item.id === "hard-cut" ? "baseline" : "motion")) === visibleGroup).map((item) => <button key={item.id} type="button" aria-pressed={recipe?.id === item.id} onClick={() => changeRecipe({ ...item.defaults })}>
            <span className={styles.presetArt} data-effect={item.id} aria-hidden="true"><i /><i /></span><strong>{item.name}</strong>
          </button>)}</div>
          {definition && recipe && <RecipeControls key={definition.id} definition={definition} recipe={recipe} bpm={bpm} onBpmChange={setBpm} onChange={changeRecipe} />}
          <SpeedControls value={retime} a={a} b={b} onChange={changeRetime} />
          {recipe?.id === "whip-pan" && <div className={programStyles.quickStart}><button type="button" disabled={!fastTiming.available || submitting} onClick={() => { if (fastTiming.available) { changeRecipe(fastTiming.recipe); changeRetime(fastTiming.retime); } }}>Try fast edit timing</button><p>{fastTiming.available ? "8-frame whip · 2× speed ramps. Adjust the speed and duration to fit your footage." : fastTiming.reason}</p></div>}
          <div className={styles.output}><label className={styles.selectLabel}>Frame<select value={output.aspect} onChange={(e) => changeOutput({ ...output, aspect: e.target.value as TransitionRequest["output"]["aspect"] })}>
            <option value="landscape">16:9 · Landscape</option><option value="portrait">9:16 · Portrait</option><option value="square">1:1 · Square</option></select></label>
            <label className={styles.selectLabel}>Quality<select value={output.quality} onChange={(e) => changeOutput({ ...output, quality: e.target.value as TransitionRequest["output"]["quality"] })}>
              <option value="draft">Draft · 480p</option><option value="high">Review · 720p</option><option value="export">Export · 1080p</option></select></label></div>
          <button type="button" className={styles.primary} disabled={!!invalid || submitting} onClick={() => void render()}>{submitting ? "Starting…" : "Preview transition"}<span aria-hidden="true">↗</span></button>
          {recipe?.id !== "hard-cut" && <button type="button" className={styles.sweepButton} disabled={sweep.length < 2 || submitting || !!invalid} onClick={() => void enqueue(sweep)}>Render {sweep.length || 3} timings <small>{sweep.length ? sweep.map((item) => variantTiming(item.recipe)).join(" / ") : "−2f / current / +2f"}</small></button>}
          {invalid ? <p className={styles.hint}>{invalid}</p> : <p className={styles.hint}>Each render keeps its source times and settings. Adjust anything, then render another version.</p>}
          <details className={styles.saveRecipe}><summary>Save this recipe</summary><label>Name<input aria-label="Saved recipe name" maxLength={100} value={recipeName} placeholder={recipe ? `${recipeTitle(recipe.id)} · ${Math.round(recipe.duration * 30)}f` : "My recipe"} onChange={(event) => setRecipeName(event.target.value)} /></label><button type="button" className={styles.secondary} disabled={!!invalid || !currentRequest} onClick={saveRecipe}>Save in this browser</button></details>
        </aside>
      </div>
      <section className={styles.experiments}>
        <details className={styles.historyDrawer}><summary>Saved renders <span>{jobs.length} versions · favorites &amp; saved recipes</span></summary>
          <div className={styles.historyTools}><button type="button" aria-pressed={favoritesOnly} onClick={() => void showFavorites()}>★ Favorites</button><span>Most recent {jobs.length} renders</span></div>
          {!history.length ? <p className={styles.historyEmpty}>{favoritesOnly ? "Favorite a rendered variant to keep it close." : "Your experiments will collect here. Render a few versions on the same pair to compare them."}</p> : <div className={styles.history}>{history.slice(0, historyLimit).map((job) => <div className={styles.historyCard} key={job.id} data-selected={selectedId === job.id}><button className={styles.historyItem} type="button" aria-pressed={selectedId === job.id} onClick={() => selectJob(job)}>
            <span className={styles.historyThumbnail}>{job.result?.frame_a_url ? <img src={mediaUrl(job.result.frame_a_url)} alt="" loading="lazy" /> : <span aria-hidden="true">A → B</span>}</span>
            <span><strong>{recipeTitle(job.request.recipe.id)}</strong><small>{job.request.recipe.id === "hard-cut" ? "Straight cut" : `${Math.round(job.request.recipe.duration * 30)}f · ${job.request.recipe.easing}`} · {qualityTitle(job.request.output.quality)} · {job.request.output.aspect}{restoredRetime(job.request.retime).mode !== "off" ? ` · ${speedTitle(job.request.retime)}` : ""}</small><small className={styles.historySource}>{job.source_titles?.outgoing || "Clip A"} → {job.source_titles?.incoming || "Clip B"}</small></span>
            <span className={styles.jobState} data-status={job.status}>{job.status === "completed" ? "Ready" : job.status}</span>
          </button><button className={styles.favoriteButton} type="button" aria-label={`${notebook.variants[job.id]?.favorite ? "Unfavorite" : "Favorite"} ${recipeTitle(job.request.recipe.id)} ${job.id.slice(0, 6)}`} aria-pressed={!!notebook.variants[job.id]?.favorite} onClick={() => updateNote(job.id, { favorite: !notebook.variants[job.id]?.favorite })}>{notebook.variants[job.id]?.favorite ? "★" : "☆"}</button></div>)}</div>}
          {history.length > historyLimit && <button type="button" className={styles.showMore} onClick={() => setHistoryLimit((count) => count + 12)}>Show more renders <span>{history.length - historyLimit} remaining</span></button>}
          <p className={styles.hint}>Rendered versions are retained across visits. Unrendered changes stay in this session.</p>
          {notebook.recipes.length > 0 && <details className={styles.savedRecipes}><summary>Saved recipes <span>{notebook.recipes.length} · This browser</span></summary><div>{notebook.recipes.map((saved) => <div key={saved.id}><button type="button" onClick={() => reuseRequest(saved.request, saved.source_titles)}><strong>{saved.name}</strong><small>{saved.source_titles?.outgoing || "Clip A"} → {saved.source_titles?.incoming || "Clip B"} · {speedTitle(saved.request.retime)}</small></button><button type="button" aria-label={`Remove saved recipe ${saved.name}`} onClick={() => setNotebook((current) => ({ ...current, recipes: current.recipes.filter((item) => item.id !== saved.id) }))}>×</button></div>)}</div></details>}
          {notebookError && <p className={styles.error} role="status">{notebookError}</p>}
        </details>
      </section>
      </div>
      <div id="ai-panel" role="tabpanel" aria-labelledby="ai-tab" hidden={mode !== "ai"}>
        <div className={styles.aiPreparation}>
          <div><strong>{rendered ? aiPairChanged ? "Working pair differs from the prepared pair" : aiNeedsPreparation ? "Prepare endpoints at original speed" : "Source pair prepared" : "Prepare your clips for AI"}</strong><p>{rendered ? "AI uses the saved endpoints below. No AI video has been generated by preparing this pair." : "Choose two clips, then prepare their endpoints. This is a local step; it does not generate an AI video."}</p></div>
          <div><button type="button" className={styles.secondary} onClick={() => chooseMode("local")}>{a && b ? "Edit clips" : "Choose clips"}</button>
            {(aiNeedsPreparation || !!(pendingPreview && activeRender(pendingPreview))) && <button type="button" className={styles.primary} disabled={!aiPreparation || !!aiPreparationProblem || submitting || !!(pendingPreview && activeRender(pendingPreview))} onClick={() => aiPreparation && void enqueue([aiPreparation])}>{submitting || (pendingPreview && activeRender(pendingPreview)) ? "Preparing…" : "Prepare working pair"}</button>}</div>
          {aiPreparationProblem && <p className={styles.hint}>{aiPreparationProblem}</p>}
          {pendingPreview && activeRender(pendingPreview) && <p className={styles.hint} role="status">{pendingPreview.progress || "Preparing a local cut with Speed off. Your AI draft stays here."}</p>}
          {pendingPreview && !activeRender(pendingPreview) && pendingPreview.status !== "completed" && <p className={styles.error} role="alert">The prepared preview did not finish. Your saved pair is unchanged. <button type="button" className={styles.textButton} onClick={() => { selectJob(pendingPreview); chooseMode("local"); }}>View render details</button></p>}
        </div>
        <AIHandoff job={rendered} active={mode === "ai"} sourcePairChanged={aiPairChanged} />
      </div>
    </>}
    {picker && <SourceBrowser filmIds={[]} replacing={false} onClose={() => setPicker(null)} onSelect={(result) => { const next = sourceFromResult(result); changeSource(picker, next); setFilmDurations((current) => ({ ...current, [picker]: undefined })); setMonitorSide(picker); setSourceInspection({ side: picker, film: next.film_id, time: next.source_start, token: ++inspectionToken.current }); setPicker(null); }} />}
    </div>
  </main>;
}
