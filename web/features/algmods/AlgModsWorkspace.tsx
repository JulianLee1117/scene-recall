"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { labRequest, mediaUrl, seconds } from "@/lib/lab";
import type { WorkerStatus } from "@/types/lab";
import LabWorkspaceHeader, { LabEmptyState } from "@/features/lab/LabWorkspaceHeader";
import SourceBrowser from "@/features/lab/SourceBrowser";
import { activeRender, baseline, getPath, round, sameRequest, sceneKey, setPath, sourceFromResult, sourcePayload, specFor, treatmentSummary, validateWindow,
  type Catalog, type Control, type ParamValue, type RenderJob, type RenderRequest, type SourceSelection, type Treatment, type TreatmentKind } from "./algmods";
import styles from "./algmods.module.css";

const PINNED = "alg-mods:scenes";

/** The board: scenes down, effects across, the latest render of each pair in the cell. */
export default function AlgModsWorkspace() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [jobs, setJobs] = useState<RenderJob[]>([]);
  const [pinned, setPinned] = useState<SourceSelection[]>([]);
  const [sceneId, setSceneId] = useState<string | null>(null);
  const [kind, setKind] = useState<TreatmentKind | null>(null);
  const [treatment, setTreatment] = useState<Treatment | null>(null);
  const [window_, setWindow] = useState<SourceSelection | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sideBySide, setSideBySide] = useState(true);
  const [advanced, setAdvanced] = useState(false);
  const [picker, setPicker] = useState(false);
  const [error, setError] = useState("");
  const [stale, setStale] = useState(false);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [retry, setRetry] = useState(0);
  const [workers, setWorkers] = useState<WorkerStatus | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { try { const saved = window.localStorage.getItem(PINNED); if (saved) setPinned(JSON.parse(saved)); } catch { /* per-viewer convenience only */ } }, []);
  function savePinned(next: SourceSelection[]) { setPinned(next); try { window.localStorage.setItem(PINNED, JSON.stringify(next)); } catch { /* ignore */ } }

  useEffect(() => {
    const abort = new AbortController(); setLoading(true); setError(""); setStale(false);
    Promise.all([labRequest<Catalog>("/alg-mods/catalog", { signal: abort.signal }),
      labRequest<{ renders: RenderJob[] }>("/alg-mods/renders?limit=100", { signal: abort.signal })])
      .then(([data, history]) => {
        if (abort.signal.aborted) return;
        if (!Array.isArray(data?.treatments) || !data.treatments.length) { setStale(true); setLoading(false); return; }
        setCatalog(data);
        setJobs(history.renders.filter((job) => job.request?.treatment?.kind));
      })
      .catch((reason) => { if (!abort.signal.aborted) setError(reason.message || "Could not load Alg Mods."); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [retry]);

  const pendingIds = jobs.filter(activeRender).map((job) => job.id).join(",");
  useEffect(() => {
    if (!pendingIds) return;
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      const results = await Promise.allSettled(pendingIds.split(",").map((id) => labRequest<RenderJob>(`/alg-mods/renders/${id}`, { signal: abort.signal })));
      if (abort.signal.aborted) return;
      const updates = results.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
      setJobs((current) => current.map((job) => updates.find((update) => update.id === job.id) ?? job));
      timer = setTimeout(poll, 1600);
    }
    void poll();
    void labRequest<WorkerStatus>("/workers", { signal: abort.signal }).then((status) => { if (!abort.signal.aborted) setWorkers(status); }).catch(() => {});
    return () => { abort.abort(); clearTimeout(timer); };
  }, [pendingIds]);

  // Scenes: every distinct window that has a render, plus the ones pinned before their first render.
  const scenes = useMemo(() => {
    const map = new Map<string, SourceSelection>();
    for (const scene of pinned) map.set(sceneKey(scene), scene);
    for (const job of jobs) { const key = sceneKey(job.request.source); if (!map.has(key)) map.set(key, { ...job.request.source, title: job.source_title }); }
    return [...map.values()];
  }, [pinned, jobs]);
  const effects = catalog?.treatments ?? [];
  const cellJobs = (scene: SourceSelection, effect: TreatmentKind) => jobs.filter((job) => sceneKey(job.request.source) === sceneKey(scene) && job.request.treatment.kind === effect);
  const latest = (scene: SourceSelection, effect: TreatmentKind) => cellJobs(scene, effect).find((job) => job.status === "completed" && job.result) ?? cellJobs(scene, effect)[0] ?? null;
  const scene = scenes.find((item) => sceneKey(item) === sceneId) ?? null;
  const spec = kind ? specFor(catalog, kind) : null;
  const pair = scene && kind ? cellJobs(scene, kind) : [];
  const selected = pair.find((job) => job.id === selectedId) ?? pair[0] ?? null;
  const rendered = selected?.status === "completed" && selected.result ? selected : null;
  const source = window_ ?? scene;
  const request: RenderRequest | null = source && treatment ? { source: sourcePayload(source), treatment, output: { side_by_side: sideBySide } } : null;
  const problem = validateWindow(source, catalog?.limits);
  const unchanged = !!request && !!rendered && sameRequest(request, rendered.request);
  const editorWorker = workers?.workers.find((worker) => worker.role === "editor");
  const controls = (spec?.controls ?? []).filter((control) => advanced || !control.advanced);

  function open(target: SourceSelection, effect: TreatmentKind, job?: RenderJob | null) {
    const chosen = job ?? latest(target, effect);
    setSceneId(sceneKey(target)); setKind(effect); setSelectedId(chosen?.id ?? null);
    setWindow({ ...target });
    setTreatment(chosen ? chosen.request.treatment : specFor(catalog, effect)?.defaults ?? null);
    if (chosen) setSideBySide(chosen.request.output.side_by_side);
  }
  function addScene(next: SourceSelection) {
    savePinned([next, ...pinned.filter((item) => sceneKey(item) !== sceneKey(next))]);
    open(next, kind ?? effects[0]?.id ?? "dots", null);
  }
  function removeScene(target: SourceSelection) {
    savePinned(pinned.filter((item) => sceneKey(item) !== sceneKey(target)));
    if (sceneId === sceneKey(target)) { setSceneId(null); setKind(null); setSelectedId(null); }
  }
  function setParam(key: string, value: ParamValue) { if (treatment) setTreatment(setPath(treatment, key, value)); }
  function setStyle(style: "vivid" | "pastel") {
    const defaults = spec?.styles?.find((item) => item.id === style)?.defaults;
    if (defaults && treatment) setTreatment({ ...defaults, protect_subject: treatment.protect_subject ?? false } as Treatment);
  }
  function adjustWindow(key: "source_start" | "source_end", value: number) {
    if (!source || !Number.isFinite(value)) return;
    setWindow({ ...source, [key]: round(Math.max(0, value)) });
  }
  async function render() {
    if (!request || !source || problem || submitting) return;
    setSubmitting(true); setError("");
    try {
      const job = await labRequest<RenderJob & { reused: boolean }>("/alg-mods/renders", { method: "POST", body: JSON.stringify(request) });
      if (!alive.current) return;
      setJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
      if (sceneKey(source) !== sceneId) { savePinned([source, ...pinned.filter((item) => sceneKey(item) !== sceneKey(source))]); setSceneId(sceneKey(source)); }
      setSelectedId(job.id);
    } catch (reason) { if (alive.current) setError(reason instanceof Error ? reason.message : "The render could not be queued."); }
    finally { if (alive.current) setSubmitting(false); }
  }
  async function cancel(job: RenderJob) {
    try {
      const updated = await labRequest<RenderJob>(`/alg-mods/renders/${job.id}/cancel`, { method: "POST" });
      setJobs((current) => current.map((item) => item.id === updated.id ? updated : item));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "The render could not be cancelled."); }
  }
  function controlField(control: Control) {
    if (!treatment) return null;
    const value = getPath(treatment, control.key);
    if (control.type === "text") return <label key={control.key} className={styles.select}><span>{control.label}</span>
      <input type="text" placeholder={control.placeholder} value={Array.isArray(value) ? value.join(", ") : String(value ?? "")}
        onChange={(event) => setParam(control.key, event.target.value.split(",").map((part) => part.trim()).filter(Boolean))} /></label>;
    if (control.type === "toggle") return <label key={control.key} className={styles.toggle}>
      <input type="checkbox" checked={!!value} onChange={(event) => setParam(control.key, event.target.checked)} />{control.label}</label>;
    if (control.type === "select") return <label key={control.key} className={styles.select}><span>{control.label}</span>
      <select value={String(value ?? "")} onChange={(event) => setParam(control.key, event.target.value)}>
        {control.options?.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select></label>;
    return <label key={control.key} className={styles.range}>
      <span>{control.label}<small>{String(value)}{control.unit ? ` ${control.unit}` : ""}</small></span>
      <input type="range" min={control.min} max={control.max} step={control.step} value={Number(value)} onChange={(event) => setParam(control.key, Number(event.target.value))} />
    </label>;
  }

  if (loading) return <main className={styles.workspace}><LabWorkspaceHeader title="Alg Mods" /><p className={styles.status} role="status">Loading Alg Mods…</p></main>;
  if (stale) return <main className={styles.workspace}><LabWorkspaceHeader title="Alg Mods" />
    <LabEmptyState title="The API is running an older Alg Mods" description="This page expects the treatment catalog, but the API still serves the previous contract. Restart the API and the Lab workers, then try again.">
      <button type="button" onClick={() => setRetry((value) => value + 1)}>Try again</button>
      <small>scripts\restart-scene-recall.ps1 -WaitForJobs</small>
    </LabEmptyState></main>;
  if (!catalog) return <main className={styles.workspace}><LabWorkspaceHeader title="Alg Mods" />
    <LabEmptyState title="Alg Mods is unavailable" description={error || "The treatment catalog could not be loaded. Check that the API is running."}>
      <button type="button" onClick={() => setRetry((value) => value + 1)}>Try again</button>
    </LabEmptyState></main>;

  return <main className={styles.workspace}>
    <LabWorkspaceHeader title="Alg Mods" />
    <div className={styles.intro}>
      <div><span className={styles.eyebrow}>SCENES × EFFECTS</span><h2>Try every effect on every scene</h2>
        <p>Scenes down, effects across. A cell is the latest render of that pair; open it to play, tune and render variants.</p></div>
      <button type="button" className={styles.add} onClick={() => setPicker(true)}>+ Add scene</button>
    </div>

    <div className={styles.boardWrap}>
      <table className={styles.board}>
        <thead><tr><th scope="col">Scene</th>{effects.map((effect) => <th key={effect.id} scope="col" title={effect.description}>{effect.name}</th>)}</tr></thead>
        <tbody>
          {scenes.length === 0 && <tr><td colSpan={effects.length + 1} className={styles.boardEmpty}>No scenes yet. Add one from the library search; night shots with lit, coloured subjects suit the dots.</td></tr>}
          {scenes.map((row) => <tr key={sceneKey(row)}>
            <th scope="row"><span className={styles.sceneTitle}>{row.title}</span><small>{seconds(row.source_start)}–{seconds(row.source_end)}</small>
              {pinned.some((item) => sceneKey(item) === sceneKey(row)) && <button type="button" className={styles.remove} onClick={() => removeScene(row)} aria-label={`Remove ${row.title} from the board`}>×</button>}</th>
            {effects.map((effect) => {
              const job = latest(row, effect.id);
              const active = sceneId === sceneKey(row) && kind === effect.id;
              const count = cellJobs(row, effect.id).length;
              return <td key={effect.id} data-active={active}>
                <button type="button" className={styles.cell} onClick={() => open(row, effect.id, job)} aria-label={`${effect.name} on ${row.title}`}>
                  {job?.status === "completed" && job.result
                    ? <video src={mediaUrl(job.result.preview_url)} muted playsInline preload="metadata" />
                    : <span className={styles.cellState} data-status={job?.status ?? "none"}>{job ? (job.cancel_requested ? "cancelled" : job.status) : "render"}</span>}
                  {count > 1 && <small className={styles.cellCount}>{count}</small>}
                </button>
              </td>;
            })}
          </tr>)}
        </tbody>
      </table>
    </div>

    {scene && kind && spec && treatment && <div className={styles.workbench}>
      <section className={styles.stage} aria-label="Preview">
        <div className={styles.stageTitle}><h3>{spec.name} <span>on {scene.title}</span></h3></div>
        {rendered ? <>
          <video key={rendered.id} className={rendered.result!.side_by_side ? styles.wide : styles.tall} src={mediaUrl(rendered.result!.preview_url)} controls muted playsInline preload="metadata" />
          <div className={styles.receipt}>
            <div><span>{treatmentSummary(rendered.result!.treatment, catalog)}</span><small>{seconds(rendered.request.source.source_start)} – {seconds(rendered.request.source.source_end)}</small></div>
            <a href={mediaUrl(`${rendered.result!.preview_url}?download=1`)}>Download MP4</a> <a href={mediaUrl(rendered.result!.manifest_url)}>Receipt</a>
          </div>
        </> : selected && activeRender(selected) ? <div className={styles.empty}>
          <strong>{selected.status === "queued" ? "Queued" : "Rendering"}</strong>
          <p>{selected.progress_steps?.at(-1) ?? "Waiting for the editor worker."}</p>
          {editorWorker && !editorWorker.online && <p className={styles.warn}>The editor worker is offline. Start it to render.</p>}
          <button type="button" onClick={() => cancel(selected)}>Cancel</button>
        </div> : selected && selected.status === "failed" ? <div className={styles.empty}><strong>Render failed</strong><p>{selected.error || "No detail was recorded."}</p></div>
        : <div className={styles.empty}><strong>Not rendered yet</strong><p>Set the controls on the right and render.</p></div>}
        {pair.length > 1 && <div className={styles.variants}>
          <div><strong>Variants of this pair</strong><small>{pair.length}</small></div>
          <ul>{pair.map((job) => <li key={job.id} data-selected={job.id === selected?.id}>
            <button type="button" onClick={() => open(scene, kind, job)}>
              <span className={styles.summary}>{treatmentSummary(job.request.treatment, catalog)}</span>
              <span className={styles.state} data-status={job.status}>{job.cancel_requested ? "cancelled" : job.status}</span>
            </button></li>)}</ul>
        </div>}
      </section>
      <aside className={styles.settings} aria-label="Settings">
        <div className={styles.block}>
          <div className={styles.blockTitle}><h3>Window</h3></div>
          <div className={styles.trim}>
            <label>In<input type="number" step="0.01" min="0" value={source!.source_start} onChange={(event) => adjustWindow("source_start", event.target.valueAsNumber)} /></label>
            <label>Out<input type="number" step="0.01" min="0" value={source!.source_end} onChange={(event) => adjustWindow("source_end", event.target.valueAsNumber)} /></label>
            <span>{(source!.source_end - source!.source_start).toFixed(2)} s</span>
          </div>
          <p className={styles.hint}>Changing the window makes a new scene row.</p>
        </div>
        {spec.styles && <div className={styles.block}>
          <div className={styles.blockTitle}><h3>Style</h3></div>
          <div className={styles.styles}>{spec.styles.map((style) => <button key={style.id} type="button" aria-pressed={treatment.style === style.id} onClick={() => setStyle(style.id)} title={style.description}>{style.name}</button>)}</div>
        </div>}
        <div className={styles.block}>
          <div className={styles.blockTitle}><h3>{spec.name}</h3>
            <span><button type="button" onClick={() => { const base = baseline(catalog, treatment); if (base) setTreatment({ ...base }); }}>Reset</button> <button type="button" onClick={() => setAdvanced((value) => !value)}>{advanced ? "Fewer controls" : "Fine tune"}</button></span></div>
          <p className={styles.hint} style={{ marginTop: 0, marginBottom: 10 }}>{spec.description}</p>
          {controls.map(controlField)}
          <label className={styles.toggle}><input type="checkbox" checked={sideBySide} onChange={(event) => setSideBySide(event.target.checked)} />Original beside the result</label>
        </div>
        <div className={styles.actions}>
          <button type="button" className={styles.render} disabled={!request || !!problem || submitting || unchanged} onClick={render}>
            {submitting ? "Queuing…" : unchanged ? "Rendered" : `Render ${spec.name.toLowerCase()}`}
          </button>
          <p className={styles.hint}>{problem || (unchanged ? "Change a setting to render a new variant." : "Renders run on the editor worker. A 5-second window takes about a minute.")}</p>
          {error && <p role="alert" className={styles.error}>{error}</p>}
        </div>
      </aside>
    </div>}
    {!scene && scenes.length > 0 && <p className={styles.hint}>Open a cell to play it or render it.</p>}
    {picker && <SourceBrowser filmIds={[]} replacing={false} onClose={() => setPicker(false)} onSelect={(result) => { addScene(sourceFromResult(result)); setPicker(false); }} />}
  </main>;
}
