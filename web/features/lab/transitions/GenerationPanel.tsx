"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { labRequest, mediaUrl } from "@/lib/lab";
import type { TransitionJob } from "./transitions";
import { bridgeSourceTimingProblem } from "./bridges";
import { dollars, generationKey, generationPending, generationRejected, generationRequest, quoteRequest, generationModels, generationModelTitle, retainedGenerationOriginal,
  type GenerationDraft, type GenerationJob, type GenerationProvider, type GenerationQuote, type GenerationRequest,
  type GenerationResolution, type GenerationReview, type PromptExpansion } from "./generation";
import styles from "./generation.module.css";

export default function GenerationPanel({ job, active, prompt, prerequisiteShown = false, onAdjustTiming }: { job: TransitionJob | null; active: boolean; prompt: string; prerequisiteShown?: boolean; onAdjustTiming?: (generation: GenerationJob) => void }) {
  const parent = job?.status === "completed" && job.result ? job.id : "";
  const timingProblem = bridgeSourceTimingProblem(job?.request.retime);
  const [duration, setDuration] = useState(4);
  const [resolution, setResolution] = useState<GenerationResolution>("720p");
  const [seed, setSeed] = useState(42);
  const [modelId, setModelId] = useState("seedance2_5");
  const [expansion, setExpansion] = useState<PromptExpansion>("disabled");
  const [provider, setProvider] = useState<GenerationProvider | null>(null);
  const [providerError, setProviderError] = useState("");
  const [review, setReview] = useState<GenerationReview | null>(null);
  const [quoting, setQuoting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [attempt, setAttempt] = useState<GenerationRequest | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [history, setHistory] = useState<GenerationJob[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [recovering, setRecovering] = useState(false);
  const [cancelling, setCancelling] = useState<string | null>(null);
  const alive = useRef(true), posting = useRef(false), recoveryBusy = useRef(false), cancelBusy = useRef(false);
  const sequence = useRef(0), quoteBusy = useRef<string | null>(null);
  const historyRevision = useRef(0);
  const uncertainRequests = useRef(new Set<string>());
  const player = useRef<HTMLVideoElement | null>(null);
  const registerPlayer = useCallback((node: HTMLVideoElement | null) => { if (player.current !== node) player.current?.pause(); player.current = node; }, []);
  const models = generationModels(provider), model = models.find((item) => item.id === modelId) ?? models[0];
  const draft: GenerationDraft = { parent_render_id: parent, model: modelId, prompt, duration, resolution,
    ...(model.capabilities.seed ? { seed } : {}), ...(model.capabilities.prompt_expansion_modes.length ? { prompt_expansion_mode: expansion } : {}) };
  const key = generationKey(draft);
  const current = useRef({ key, parent, active, timingProblem }); current.current = { key, parent, active, timingProblem };
  let invalid = timingProblem; if (!invalid) try { quoteRequest(draft, model); } catch (reason) { invalid = reason instanceof Error ? reason.message : "Check generation settings."; }
  const reviewed = review?.key === key && !timingProblem ? review : null;
  let submitProblem = "";
  if (reviewed) { try { generationRequest(draft, reviewed, model); } catch (reason) { submitProblem = reason instanceof Error ? reason.message : "Request a fresh quote."; } }
  const visibleHistory = history.filter((item) => item.request.parent_render_id === parent);
  const selected = visibleHistory.find((item) => item.id === selectedId);
  const result = selected?.status === "completed" && !selected.cancel_requested ? selected.result : null;
  const originalUrl = selected ? retainedGenerationOriginal(selected) : null;

  useEffect(() => { alive.current = true; return () => { alive.current = false; sequence.current++; player.current?.pause(); }; }, []);
  useEffect(() => { sequence.current++; quoteBusy.current = null; setReview(null); setQuoting(false); }, [key]);
  useEffect(() => { if (!active) player.current?.pause(); }, [active]);
  useEffect(() => {
    if (!active) return;
    const abort = new AbortController(); setProviderError("");
    void labRequest<GenerationProvider>("/transitions/providers", { signal: abort.signal }).then((value) => { if (!abort.signal.aborted) setProvider(value); })
      .catch((reason) => { if (!abort.signal.aborted) setProviderError(reason.message || "Connection status is unavailable."); });
    return () => abort.abort();
  }, [active, refresh]);
  useEffect(() => {
    setHistory([]); setSelectedId(null); setHistoryError("");
  }, [parent]);
  useEffect(() => {
    if (!active || !parent) return;
    const abort = new AbortController();
    const revision = historyRevision.current;
    void labRequest<{ generations: GenerationJob[] }>(`/transitions/generations?parent_render_id=${encodeURIComponent(parent)}&limit=30`, { signal: abort.signal })
      .then((value) => { if (!abort.signal.aborted) {
        if (revision === historyRevision.current) { setHistory(value.generations); setSelectedId((id) => value.generations.some((item) => item.id === id) ? id : value.generations[0]?.id ?? null); }
        else setHistory((items) => [...items, ...value.generations.filter((item) => !items.some((saved) => saved.id === item.id))]);
        setHistoryError("");
      } })
      .catch((reason) => { if (!abort.signal.aborted) setHistoryError(reason.message || "Saved generation status is unavailable."); });
    return () => abort.abort();
  }, [parent, active, refresh]);
  const pending = visibleHistory.filter(generationPending).map((item) => item.id).join(",");
  useEffect(() => {
    if (!active || !pending) return;
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      const updates = await Promise.allSettled(pending.split(",").map((id) => labRequest<GenerationJob>(`/transitions/generations/${id}`, { signal: abort.signal })));
      if (abort.signal.aborted) return;
      const values = updates.flatMap((value) => value.status === "fulfilled" ? [value.value] : []);
      historyRevision.current++;
      setHistory((items) => items.map((item) => values.find((value) => value.id === item.id) ?? item));
      setHistoryError(updates.some((value) => value.status === "rejected") ? "Generation status is unavailable; checking again shortly." : "");
      timer = setTimeout(poll, 2200);
    }
    void poll(); return () => { abort.abort(); clearTimeout(timer); };
  }, [pending, active]);

  function accept(saved: GenerationJob) {
    if (!alive.current) return;
    historyRevision.current++;
    uncertainRequests.current.delete(saved.id); setAttempt(null); setError("");
    if (current.current.parent === saved.request.parent_render_id) { setHistory((items) => [saved, ...items.filter((item) => item.id !== saved.id)]); setSelectedId(saved.id); }
    setNotice(`Generation ${saved.id.slice(0, 8)} is saved. Status updates do not submit another request.`);
  }
  async function getQuote() {
    if (!active || timingProblem || posting.current || attempt || quoteBusy.current === key) return;
    let body;
    try { body = quoteRequest(draft, model); } catch (reason) { setError(reason instanceof Error ? reason.message : "Check generation settings."); return; }
    const token = ++sequence.current; quoteBusy.current = key; setQuoting(true); setReview(null); setError(""); setNotice("");
    try {
      const quote = await labRequest<GenerationQuote>("/transitions/bridges/quote", { method: "POST", body: JSON.stringify(body) });
      if (alive.current && token === sequence.current && current.current.key === key) setReview({ key, quote, request_id: crypto.randomUUID() });
    } catch (reason) { if (alive.current && token === sequence.current) setError(reason instanceof Error ? reason.message : "Could not prepare a quote."); }
    finally { if (token === sequence.current) { quoteBusy.current = null; if (alive.current) setQuoting(false); } }
  }
  async function submit(payload: GenerationRequest) {
    if (!current.current.active || posting.current || (current.current.timingProblem && payload.parent_render_id === current.current.parent)) return;
    posting.current = true; setSubmitting(true); setReview(null); setAttempt(payload); setError(""); setNotice("");
    try { accept(await labRequest<GenerationJob>("/transitions/generations", { method: "POST", body: JSON.stringify(payload) })); }
    catch (reason) {
      if (alive.current) {
        if (generationRejected(reason) && !uncertainRequests.current.has(payload.request_id)) { setAttempt(null); setError(reason instanceof Error ? reason.message : "This generation request was rejected. Request a fresh quote."); }
        else { uncertainRequests.current.add(payload.request_id); setError("This request’s submission status remains uncertain. Check its status or retry the same request below; its identity and reviewed estimate are preserved."); }
      }
    } finally { posting.current = false; if (alive.current) setSubmitting(false); }
  }
  function generate() {
    if (!reviewed || current.current.timingProblem || current.current.key !== reviewed.key || !current.current.active || !provider?.configured || !provider.generation_enabled || attempt) return;
    try { void submit(generationRequest(draft, reviewed, model)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Request a fresh quote before generating."); }
  }
  async function recover() {
    if (!attempt || recoveryBusy.current || posting.current) return;
    recoveryBusy.current = true; setRecovering(true);
    try { accept(await labRequest<GenerationJob>(`/transitions/generations/${attempt.request_id}`)); }
    catch { if (alive.current) setError("The request could not be found or reached yet. Retry the same request to safely resume; its identity and reviewed estimate will be preserved."); }
    finally { recoveryBusy.current = false; if (alive.current) setRecovering(false); }
  }
  async function cancel(item: GenerationJob) {
    if (cancelBusy.current) return;
    cancelBusy.current = true; setCancelling(item.id);
    try { const updated = await labRequest<GenerationJob>(`/transitions/generations/${item.id}/cancel`, { method: "POST" }); if (alive.current) { historyRevision.current++; setHistory((items) => items.map((value) => value.id === updated.id ? updated : value)); if (!generationPending(item)) setNotice("Provider cancellation was checked. Read the submission receipt for the observed provider state."); } }
    catch (reason) { if (alive.current) setHistoryError(reason instanceof Error ? reason.message : "Cancellation status is unavailable."); }
    finally { cancelBusy.current = false; if (alive.current) setCancelling(null); }
  }
  function inspect(time: number) { if (player.current) { player.current.pause(); player.current.currentTime = Math.max(0, time - .5); } }
  const receiptUrl = selected?.receipt_url ?? selected?.result?.receipt_url;
  function chooseModel(id: string) {
    const next = models.find((item) => item.id === id); if (!next) return;
    setModelId(id); setDuration(next.defaults.duration); setResolution(next.defaults.resolution);
    setExpansion(next.defaults.prompt_expansion_mode ?? "disabled");
  }

  return <section className={styles.panel} aria-label="Generate an AI bridge with Runway">
    <div className={styles.heading}><div><h3>Generate with Runway</h3></div><span className={styles.status} data-ready={!!provider?.configured && !!provider.generation_enabled}>{provider ? provider.configured && provider.generation_enabled ? "Connected" : "Setup needed" : "Checking connection"}</span></div>
    {provider && (!provider.configured || !provider.generation_enabled) && <details className={styles.setup}>
      <summary>{provider.configured ? "Generation is not enabled on this server" : "Connect Runway · quotes work before setup"}</summary>
      <p>Set <code>{provider.credential_environment || "RUNWAYML_API_SECRET"}</code> in the server’s <code>.env</code>, then restart the API and editor worker. The key stays on the server. You can review quotes before connecting.</p>
      <button type="button" onClick={() => setRefresh((value) => value + 1)}>Refresh connection</button>
    </details>}
    {providerError && <p className={styles.error} role="alert">{providerError} <button type="button" onClick={() => setRefresh((value) => value + 1)}>Check again</button></p>}
    {provider?.configured && !provider.live_verified && <p className={styles.note}>Key configured. Review a quote before generating.</p>}
    <div className={styles.inputs}>
      <label>Model<select aria-label="Generation model" value={modelId} disabled={submitting} onChange={(event) => chooseModel(event.target.value)}>{models.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
      <label>Duration<select aria-label="Generation duration" value={duration} disabled={submitting} onChange={(event) => setDuration(Number(event.target.value))}>{model.capabilities.durations.map((value) => <option key={value} value={value}>{value} seconds</option>)}</select></label>
      <label>Resolution<select aria-label="Generation resolution" value={resolution} disabled={submitting} onChange={(event) => setResolution(event.target.value as GenerationResolution)}>{model.capabilities.resolutions.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
    </div>
    <p className={styles.note}>Prompt: {prompt.length.toLocaleString()} / {model.max_prompt_length.toLocaleString()} characters. Model changes keep your writing.</p>
    {(model.capabilities.seed || model.capabilities.prompt_expansion_modes.length > 0) && <details className={styles.details}><summary>Advanced provider options</summary><div className={styles.inputs}>
      {model.capabilities.seed && <label>Seed<input aria-label="Generation seed" type="number" min="0" max="4294967295" step="1" value={Number.isFinite(seed) ? seed : ""} disabled={submitting} onChange={(event) => setSeed(event.target.valueAsNumber)} /></label>}
      {model.capabilities.prompt_expansion_modes.length > 0 && <label>Prompt expansion<select aria-label="Generation prompt expansion" value={expansion} disabled={submitting} onChange={(event) => setExpansion(event.target.value as PromptExpansion)}>{model.capabilities.prompt_expansion_modes.map((value) => <option key={value} value={value}>{value === "disabled" ? "Off · use my prompt" : value === "balanced" ? "Balanced" : "Quality"}</option>)}</select></label>}
    </div>{model.capabilities.prompt_expansion_modes.length > 0 && <p className={styles.note}>Expansion lets the provider elaborate your prompt. Off preserves your submitted wording; the generated result remains model-dependent.</p>}</details>}
    <button type="button" className={styles.quoteButton} disabled={!!invalid || quoting || submitting || !!attempt || !active} onClick={() => void getQuote()}>{quoting ? "Preparing quote…" : "Review generation quote"}</button>
    {invalid && !(prerequisiteShown && invalid === timingProblem) && <p className={styles.note}>{invalid}</p>}
    <p className={styles.note}>Local quote only · no upload or charge. This lab accepts estimates up to $3.00 before tax.</p>
    {reviewed && <div className={styles.quote}>
      <div><strong>{dollars(reviewed.quote.estimated_usd)}</strong><span>Estimated · {reviewed.quote.estimated_credits} credits · {model.label} · {duration}s · {resolution}</span></div>
      <p>Generating uploads two endpoint stills framed to this render and your prompt to Runway. Source video and music stay here. Auditions are muted.</p>
      {reviewed.quote.input_dimensions && <p>Endpoint inputs: {reviewed.quote.input_dimensions.width} × {reviewed.quote.input_dimensions.height}. Returned dimensions may vary; the audition is fitted to this render.</p>}
      <button type="button" className={styles.generateButton} disabled={!!submitProblem || !provider?.configured || !provider.generation_enabled || submitting || !!attempt || !active} onClick={generate}>Generate · estimated {dollars(reviewed.quote.estimated_usd)}</button>
      <p>Runway charges may apply once submitted; cancellation may not prevent charges.</p>
      {submitProblem && <p className={styles.note}>{submitProblem}</p>}
      <small>Quoted price: {reviewed.quote.price_version}. Any setting or prompt change requires a fresh review.</small>
    </div>}
    {attempt && <div className={styles.recovery} role="status"><strong>{submitting ? "Saving your generation request…" : "Keep this request identity"}</strong><p>{generationModelTitle(attempt.model, models)} · {attempt.duration}s · {attempt.resolution}{attempt.seed != null ? ` · Seed ${attempt.seed}` : ""} · reviewed estimate {attempt.max_credits} credits. Request {attempt.request_id}.</p>
      {attempt.parent_render_id !== parent && <p>This pending request belongs to an earlier source render. Retrying keeps its original endpoints and prompt.</p>}
      {!submitting && <div><button type="button" disabled={recovering} onClick={() => void recover()}>{recovering ? "Checking…" : "Check request status"}</button><button type="button" disabled={recovering || !active} onClick={() => void submit(attempt)}>Retry same request</button></div>}
    </div>}
    {error && <p className={styles.error} role="alert">{error}</p>}{notice && <p className={styles.notice} role="status">{notice}</p>}
    <div className={styles.historyHeading}><h4>Saved generations{visibleHistory.length ? ` · ${visibleHistory.length}` : ""}</h4><button type="button" disabled={!parent} onClick={() => setRefresh((value) => value + 1)}>Refresh</button></div>
    {historyError && <p className={styles.error} role="alert">{historyError}</p>}
    {!visibleHistory.length && <p className={styles.note}>Completed, failed, and interrupted requests stay attached to this source render.</p>}
    <div className={styles.history}>{visibleHistory.map((item) => <button type="button" key={item.id} aria-pressed={selectedId === item.id} onClick={() => setSelectedId(item.id)}><strong>{generationModelTitle(item.request.model, models)} · {item.request.duration}s · {item.request.resolution}</strong><span>{item.status} · {item.id.slice(0, 8)}</span></button>)}</div>
    {selected && generationPending(selected) && <div className={styles.pending}><p>{selected.progress || "Waiting for the editor worker"}</p><button type="button" disabled={!!selected.cancel_requested || cancelling === selected.id} onClick={() => void cancel(selected)}>{selected.cancel_requested || cancelling === selected.id ? "Cancellation requested" : "Cancel generation"}</button><small>Cancellation is best effort after provider submission.</small></div>}
    {selected && ["failed", "interrupted", "cancelled"].includes(selected.status) && <div className={styles.pending}><button type="button" disabled={cancelling === selected.id} onClick={() => void cancel(selected)}>{cancelling === selected.id ? "Checking cancellation…" : "Cancel retained provider task"}</button><small>Reconcile the submission receipt before starting another generation. This checks the retained task; it does not submit a new one.</small></div>}
    {selected?.error && <details className={styles.details}><summary>Generation details</summary><pre>{selected.error}</pre>{receiptUrl && <a href={mediaUrl(receiptUrl)}>Download submission receipt</a>}</details>}
    {selected && originalUrl && <div className={styles.details}>
      {onAdjustTiming && <button type="button" disabled={!active || !!timingProblem} onClick={() => {
        if (current.current.active && current.current.parent === selected.request.parent_render_id && !current.current.timingProblem) onAdjustTiming(selected);
      }}>Adjust timing</button>}
      <p>Trim this retained original or change its playback length locally. No new generation or charge.</p>
      {!result && <a href={mediaUrl(originalUrl)}>Recover generated original ↓</a>}
    </div>}
    {selected && <details className={styles.details}><summary>Saved request &amp; provenance</summary><p>{generationModelTitle(selected.request.model, models)}{selected.request.seed != null ? ` · Seed ${selected.request.seed}` : ""}{selected.request.prompt_expansion_mode ? ` · Expansion ${selected.request.prompt_expansion_mode}` : ""} · reviewed estimate {selected.request.max_credits} credits · {selected.request.price_version}</p><p>{selected.request.prompt}</p><p>Request {selected.id}{result?.task_id ? ` · Runway task ${result.task_id}` : ""}</p>{receiptUrl && <a href={mediaUrl(receiptUrl)}>Submission receipt ↓</a>}</details>}
    {result && <div className={styles.player}>
      <video key={selected!.id} ref={registerPlayer} src={mediaUrl(result.preview_url)} controls muted playsInline preload="metadata" aria-label="Original A, generated bridge, original B" onError={() => setHistoryError("The saved generation could not be played. Download the audition to inspect it.")} />
      <p className={styles.note}>Generated bridge: {result.transition_start.toFixed(2)}–{result.transition_end.toFixed(2)}s. Inspect the entry and exit at normal speed.</p>
      <div className={styles.actions}><button type="button" onClick={() => inspect(result.transition_start)}>Inspect A → bridge</button><button type="button" onClick={() => inspect(result.transition_end)}>Inspect bridge → B</button></div>
      <div className={styles.downloads}><a href={`${mediaUrl(result.preview_url)}?download=1`}>Download audition ↓</a><a href={mediaUrl(result.manifest_url)}>Recipe &amp; provenance ↓</a><a href={mediaUrl(result.original_url)}>Original generated video ↓</a></div>
    </div>}
    <p className={styles.links}><a href="https://docs.dev.runwayml.com/guides/pricing/" target="_blank" rel="noreferrer">Runway pricing ↗</a></p>
  </section>;
}
