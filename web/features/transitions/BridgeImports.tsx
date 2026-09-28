"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { labRequest, mediaUrl } from "@/lib/lab";
import type { TransitionJob } from "./transitions";
import { MAX_BRIDGE_UPLOAD, bridgePending, bridgeRequest, bridgeSourceTimingProblem, type BridgeJob } from "./bridges";
import { generationModelTitle, retainedGenerationOriginal, type GenerationJob } from "./generation";
import styles from "./transitions.module.css";
import bs from "./bridges.module.css";

export default function BridgeImports({ job, active, model, prompt, prerequisiteShown = false, generationSource = null, onExternalFile }: {
  job: TransitionJob | null; active: boolean; model: string; prompt: string; prerequisiteShown?: boolean;
  generationSource?: GenerationJob | null; onExternalFile?: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [fileParent, setFileParent] = useState<string | null>(null);
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  const [start, setStart] = useState("0");
  const [end, setEnd] = useState("");
  const [length, setLength] = useState("");
  const [error, setError] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [loadingOriginal, setLoadingOriginal] = useState(false);
  const [fileGeneration, setFileGeneration] = useState<GenerationJob | null>(null);
  const [history, setHistory] = useState<BridgeJob[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const rawPlayer = useRef<HTMLVideoElement>(null);
  const player = useRef<HTMLVideoElement>(null);
  const registerRawPlayer = useCallback((node: HTMLVideoElement | null) => { if (rawPlayer.current !== node) rawPlayer.current?.pause(); rawPlayer.current = node; }, []);
  const registerPlayer = useCallback((node: HTMLVideoElement | null) => { if (player.current !== node) player.current?.pause(); player.current = node; }, []);
  const posting = useRef(false);
  const originalLoad = useRef<AbortController | null>(null);
  const alive = useRef(true);
  const historyRevision = useRef(0);
  const parent = job?.status === "completed" && job.result ? job.id : null;
  const timingProblem = bridgeSourceTimingProblem(job?.request.retime);
  const current = useRef({ parent, active, timingProblem }); current.current = { parent, active, timingProblem };
  const visibleHistory = history.filter((item) => item.request.parent_render_id === parent);
  const fileMatches = fileParent === parent;
  useEffect(() => { alive.current = true; return () => { alive.current = false; rawPlayer.current?.pause(); player.current?.pause(); }; }, []);
  useEffect(() => {
    if (!file) { setFileUrl(null); return; }
    const url = URL.createObjectURL(file); setFileUrl(url); return () => URL.revokeObjectURL(url);
  }, [file]);
  useEffect(() => { if (!active) { rawPlayer.current?.pause(); player.current?.pause(); } }, [active]);
  useEffect(() => {
    originalLoad.current?.abort(); setLoadingOriginal(false);
    setHistory([]); setSelectedId(null); setHistoryError("");
  }, [parent]);
  useEffect(() => {
    if (!generationSource) return;
    const original = retainedGenerationOriginal(generationSource);
    if (!original || generationSource.request.parent_render_id !== current.current.parent) {
      setError("The retained original is unavailable for this source pair. Choose a saved generation again."); return;
    }
    const abort = new AbortController(); originalLoad.current = abort; setLoadingOriginal(true); setError("");
    async function load() {
      try {
        const response = await fetch(mediaUrl(original!), { signal: abort.signal, cache: "no-store" });
        if (!response.ok) throw new Error("The retained original could not be loaded. Refresh the generation and try again.");
        const declared = Number(response.headers.get("Content-Length"));
        if (declared > MAX_BRIDGE_UPLOAD) throw new Error("The retained original exceeds the 128 MiB import limit.");
        const blob = await response.blob();
        if (!blob.size || blob.size > MAX_BRIDGE_UPLOAD) throw new Error("Choose a nonempty video no larger than 128 MiB.");
        if (abort.signal.aborted || current.current.parent !== generationSource!.request.parent_render_id) return;
        const loaded = new File([blob], `generation-${generationSource!.id}.mp4`, { type: blob.type.startsWith("video/") ? blob.type : "video/mp4" });
        setFile(loaded); setFileParent(generationSource!.request.parent_render_id);
        setFileGeneration({ ...generationSource!, request: { ...generationSource!.request } });
        setStart("0"); setEnd(""); setLength("");
      } catch (reason) {
        if (!abort.signal.aborted) setError(reason instanceof Error ? reason.message : "Could not load the retained original.");
      } finally {
        abort.abort();
        if (originalLoad.current === abort) { originalLoad.current = null; if (alive.current) setLoadingOriginal(false); }
      }
    }
    void load(); return () => abort.abort();
  }, [generationSource]);
  useEffect(() => {
    if (!active || !parent) return;
    const abort = new AbortController(), revision = historyRevision.current;
    void labRequest<{ bridges: BridgeJob[] }>(`/transitions/bridges?parent_render_id=${encodeURIComponent(parent)}&limit=30`, { signal: abort.signal })
      .then((value) => { if (!abort.signal.aborted) {
        if (revision === historyRevision.current) { setHistory(value.bridges); setSelectedId((id) => value.bridges.some((item) => item.id === id) ? id : value.bridges[0]?.id ?? null); }
        else setHistory((items) => [...items, ...value.bridges.filter((item) => !items.some((saved) => saved.id === item.id))]);
        setHistoryError("");
      } })
      .catch((reason) => { if (!abort.signal.aborted) setHistoryError(reason.message); });
    return () => abort.abort();
  }, [parent, active, refresh]);
  const pending = visibleHistory.filter(bridgePending).map((item) => item.id).join(",");
  useEffect(() => {
    if (!active || !pending) return;
    const abort = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      const updates = await Promise.allSettled(pending.split(",").map((id) => labRequest<BridgeJob>(`/transitions/bridges/${id}`, { signal: abort.signal })));
      if (abort.signal.aborted) return;
      const values = updates.flatMap((value) => value.status === "fulfilled" ? [value.value] : []);
      historyRevision.current++;
      setHistory((current) => current.map((item) => values.find((value) => value.id === item.id) ?? item));
      setHistoryError(updates.some((value) => value.status === "rejected") ? "Bridge status is unavailable; retrying." : "");
      timer = setTimeout(poll, 1800);
    }
    void poll(); return () => { abort.abort(); clearTimeout(timer); };
  }, [pending, active]);
  async function upload() {
    if (!file || !parent || !fileMatches || !current.current.active || current.current.parent !== parent || current.current.timingProblem || posting.current || originalLoad.current) return;
    setError(""); let metadata;
    try {
      metadata = bridgeRequest(parent, start, end, length, "", model, prompt);
      if (fileGeneration) metadata.provenance = { provider: "Runway", model: fileGeneration.request.model ?? "seedance2_5",
        prompt: fileGeneration.request.prompt, seed: fileGeneration.request.seed ?? null };
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Check the bridge timing."); return; }
    posting.current = true; setUploading(true);
    const form = new FormData(); form.append("file", file); form.append("metadata", JSON.stringify(metadata));
    try {
      const saved = await labRequest<BridgeJob>("/transitions/bridges", { method: "POST", body: form });
      if (alive.current && current.current.parent === parent) { historyRevision.current++; setHistory((items) => [saved, ...items.filter((item) => item.id !== saved.id)]); setSelectedId(saved.id); }
    } catch (reason) { if (alive.current) setError(reason instanceof Error ? reason.message : "Import status is uncertain. Refresh saved auditions before retrying."); }
    finally { posting.current = false; if (alive.current) setUploading(false); }
  }
  async function cancel(item: BridgeJob) {
    try {
      const updated = await labRequest<BridgeJob>(`/transitions/bridges/${item.id}/cancel`, { method: "POST" });
      if (alive.current) { historyRevision.current++; setHistory((items) => items.map((value) => value.id === updated.id ? updated : value)); }
    } catch (reason) { if (alive.current) setHistoryError(reason instanceof Error ? reason.message : "Cancellation failed."); }
  }
  const selected = visibleHistory.find((item) => item.id === selectedId);
  const result = selected?.status === "completed" && !selected.cancel_requested ? selected.result : null;
  const seek = (time: number) => { if (player.current) { player.current.pause(); player.current.currentTime = Math.max(0, time - .5); } };
  return <div>
    <div className={bs.form}>
      <label className={bs.file}><strong>{fileGeneration ? "Adjust generated bridge timing" : "Import a generated bridge"}</strong><span>MP4, MOV or WebM · up to 30 seconds / 128 MiB</span><input type="file" accept="video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm" disabled={uploading} aria-label="Generated bridge video" onChange={(event) => {
        const value = event.target.files?.[0]; if (!value) return; setError("");
        if (!value.size || value.size > MAX_BRIDGE_UPLOAD) { setError("Choose a nonempty video no larger than 128 MiB."); event.target.value = ""; return; }
        originalLoad.current?.abort(); originalLoad.current = null; setLoadingOriginal(false); setFileGeneration(null); onExternalFile?.();
        setFile(value); setFileParent(parent); setStart("0"); setEnd(""); setLength("");
      }} /></label>
      {loadingOriginal && <p className={bs.note} role="status">Loading retained original… <button type="button" className={styles.textButton} onClick={() => { originalLoad.current?.abort(); originalLoad.current = null; setLoadingOriginal(false); }}>Cancel loading</button></p>}
      {fileGeneration && <div className={bs.savedSettings}>
        <p><strong>Local timing copy · no new generation</strong><br />{generationModelTitle(fileGeneration.request.model)} · {fileGeneration.id.slice(0, 8)}{fileGeneration.request.seed != null ? ` · Seed ${fileGeneration.request.seed}` : ""}</p>
        <details><summary>Saved generation notes</summary><p>{fileGeneration.request.prompt}</p>
          <p>Saving copies this model, prompt and seed as import notes. They are user-supplied notes; the original generation and its verified receipt stay saved separately.</p>
          {(fileGeneration.receipt_url || fileGeneration.result?.receipt_url) && <a href={mediaUrl((fileGeneration.receipt_url || fileGeneration.result?.receipt_url)!)}>Original generation receipt ↓</a>}
        </details>
      </div>}
      {fileUrl && <div className={styles.returnedVideo}><video ref={registerRawPlayer} key={fileUrl} src={fileUrl} controls muted playsInline preload="metadata" aria-label="Imported bridge before assembly"
        onError={() => setError("The browser cannot preview this format. The server can still try to import a supported MP4, MOV or WebM.")} /><span>{file?.name}</span></div>}
      {file && !fileMatches && <div className={bs.rebind}><p>The source render changed. Your file and trim are kept.</p><button type="button" className={styles.secondary} disabled={!parent || uploading || !active} onClick={() => setFileParent(parent)}>Use file for this source pair</button></div>}
      {file && <><div className={bs.trim}>
        <label>Bridge in (s)<input type="number" min="0" max="29.92" step=".01" value={start} disabled={uploading} onChange={(e) => setStart(e.target.value)} /></label>
        <label>Bridge out (s)<input type="number" min=".08" max="30" step=".01" placeholder="End of video" value={end} disabled={uploading} onChange={(e) => setEnd(e.target.value)} /></label>
        <label>Duration in edit (s)<input type="number" min=".08" max="30" step=".01" placeholder="Original speed" value={length} disabled={uploading} onChange={(e) => setLength(e.target.value)} /></label>
      </div>
      <div className={bs.transport} role="group" aria-label="Duration in edit">
        {[[".5", "0.5s"], [".75", "0.75s"], ["1", "1s"], ["", "Original"]].map(([value, label]) => <button key={label} type="button" disabled={uploading || loadingOriginal} aria-pressed={length === value} onClick={() => setLength(value)}>{label}</button>)}
      </div>
      <p className={bs.note}>Blank playback length preserves speed. The original file and this trim are saved together; the audition plays A → bridge → B.</p></>}
      <button type="button" className={styles.primary} disabled={!file || !parent || !fileMatches || !!timingProblem || uploading || loadingOriginal || !active} onClick={() => void upload()}>{uploading ? "Saving bridge…" : "Save & render both joins"}</button>
      {timingProblem && !prerequisiteShown && <p className={bs.note}>{timingProblem}</p>}
      {error && <p className={styles.error} role="alert">{error}</p>}
    </div>
    <div className={bs.historyHeading}><h3>Saved imports{visibleHistory.length ? ` · ${visibleHistory.length}` : ""}</h3><button className={styles.textButton} type="button" disabled={!parent || !active} onClick={() => setRefresh((value) => value + 1)}>Refresh saved auditions</button></div>
    {historyError && <p className={styles.error} role="alert">{historyError}</p>}
    {visibleHistory.length > 0 && <div className={bs.history}>
      <div className={bs.variants}>{visibleHistory.map((item) => <button type="button" key={item.id} aria-pressed={item.id === selectedId} onClick={() => setSelectedId(item.id)}><strong>{item.input.name}</strong><span>{item.status} · {item.request.playback_duration != null ? `${item.request.playback_duration.toFixed(2)}s bridge` : "original speed"}</span></button>)}</div>
      {selected && bridgePending(selected) && <div className={bs.pending}><span>{selected.progress || "Waiting for the editor worker"}</span><button type="button" className={styles.secondary} disabled={selected.cancel_requested} onClick={() => void cancel(selected)}>{selected.cancel_requested ? "Cancellation requested" : "Cancel audition"}</button></div>}
      {selected?.error && <details className={styles.failureDetails}><summary>Bridge audition failed — details</summary><pre>{selected.error}</pre></details>}
      {selected && <details className={bs.savedSettings}><summary>Saved trim &amp; generation notes</summary>
        <p>Trim: {selected.request.trim_start.toFixed(2)}–{selected.request.trim_end?.toFixed(2) ?? "end"}s. Playback length: {selected.request.playback_duration?.toFixed(2) ?? "original speed"}{selected.request.playback_duration != null ? "s" : ""}.</p>
        <p>{[selected.request.provenance.provider, selected.request.provenance.model].filter(Boolean).join(" · ") || "No provider/model notes supplied."}</p>
        {selected.request.provenance.prompt && <p>{selected.request.provenance.prompt}</p>}
        <p>These notes were supplied with the import and are not verified provider provenance.</p>
      </details>}
      {result && <div className={bs.player}>
        <video ref={registerPlayer} key={selected!.id} src={mediaUrl(result.preview_url)} controls muted playsInline preload="metadata" aria-label="Original A, imported bridge, original B" onError={() => setHistoryError("The saved bridge could not be played. Try downloading the audition.")} />
        <div className={bs.range} aria-hidden="true"><span style={{ left: `${100 * result.transition_start / result.duration}%`, width: `${100 * (result.transition_end - result.transition_start) / result.duration}%` }} /></div>
        <p className={bs.note}>Imported interval: {result.transition_start.toFixed(2)}–{result.transition_end.toFixed(2)}s. Inspect both joins at normal speed.</p>
        <div className={bs.transport}><button type="button" onClick={() => seek(result.transition_start)}>Inspect A → bridge</button><button type="button" onClick={() => seek(result.transition_end)}>Inspect bridge → B</button>
          <a href={`${mediaUrl(result.preview_url)}?download=1`}>Download audition</a><a href={mediaUrl(result.manifest_url)}>Recipe &amp; provenance</a><a href={mediaUrl(result.original_url)}>Original video</a></div>
      </div>}
    </div>}
  </div>;
}
