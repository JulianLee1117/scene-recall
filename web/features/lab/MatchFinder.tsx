"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { labRequest, mediaUrl, seconds } from "@/lib/lab";
import type { LabClip, LabJob, MatchCandidate, MatchCohort, MatchOptions } from "@/types/lab";
import MatchSourcePlayer from "./MatchSourcePlayer";
import MatchTimingControls from "./MatchTimingControls";
import DirectionIcon from "@/components/DirectionIcon";
import EditorIcon from "./EditorIcon";
import styles from "./lab.module.css";
import flow from "./workflow.module.css";
import cut from "./matchWorkspace.module.css";

type Preview = { candidate: MatchCandidate; jobId: string };
type Pending = { sourceJobId: string; candidateId: string; adjusted: boolean; job: LabJob };

export default function MatchFinder({ clip, incoming, busy, job, revision, dirty, canApply = true, applyBlockedReason, renderUrl, onFind, onApply, onExample, onBrowse, onChange, onUnlock, scope }: {
  clip: LabClip | null;
  incoming?: LabClip | null;
  busy: boolean;
  job: LabJob | null;
  revision: number;
  dirty: boolean;
  canApply?: boolean;
  applyBlockedReason?: string;
  renderUrl?: string | null;
  onFind: (options: MatchOptions) => void;
  onApply: (id: string, previewJobId?: string) => Promise<boolean>;
  onExample: (clip: LabClip) => void;
  onBrowse: () => void;
  onChange: (patch: Partial<LabClip>) => void;
  onUnlock?: () => void;
  scope?: ReactNode;
}) {
  const [cohorts, setCohorts] = useState<MatchCohort[]>([]);
  const [identity, setIdentity] = useState("");
  const [focus, setFocus] = useState<NonNullable<MatchOptions["focus"]>>("auto");
  const [timing, setTiming] = useState<"nearby" | "fixed">("nearby");
  const [subjectPoint, setSubjectPoint] = useState<{ x: number; y: number } | null>(null);
  const [reframe, setReframe] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [original, setOriginal] = useState(false);
  const [loading, setLoading] = useState(true);
  const [showMore, setShowMore] = useState(false);
  const [showSource, setShowSource] = useState(false);
  const [showExport, setShowExport] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [prepared, setPrepared] = useState<Record<string, Preview>>({});
  const [adjustment, setAdjustment] = useState<Preview | null>(null);
  const [adjusting, setAdjusting] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [kept, setKept] = useState<{ candidateId: string; revision: number } | null>(null);
  const inFlight = useRef(false);
  const attempted = useRef(new Set<string>());
  const activeJobId = useRef(job?.id);
  activeJobId.current = job?.id;

  useEffect(() => {
    const controller = new AbortController();
    labRequest<{ cohorts: MatchCohort[] }>("/matching/cohorts", { signal: controller.signal })
      .then(({ cohorts: next }) => { setCohorts(next); setIdentity(next[0]?.id ?? ""); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    setPrepared({}); setPending(null); setAdjustment(null); setAdjusting(false);
    setOriginal(false); setShowMore(false); setShowSource(false); setShowExport(false); setKept(null);
    attempted.current.clear(); inFlight.current = false; setPreparing(false);
    const candidates = (job?.result?.candidates ?? []) as MatchCandidate[];
    setSelected(candidates[0]?.id ?? null);
  }, [job?.id]);
  useEffect(() => { setSubjectPoint(null); }, [clip?.id]);
  useEffect(() => { if (renderUrl) { setShowExport(true); setShowSource(false); } }, [renderUrl]);

  useEffect(() => {
    if (!pending || !["queued", "running"].includes(pending.job.status)) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      labRequest<LabJob>(`/jobs/${pending.job.id}`, { signal: controller.signal })
        .then((next) => { if (activeJobId.current === pending.sourceJobId) setPending({ ...pending, job: next }); })
        .catch((reason) => {
          if (!controller.signal.aborted) { setError(reason.message); setPending(null); inFlight.current = false; }
        });
    }, 800);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [pending]);

  useEffect(() => {
    if (!pending || ["queued", "running"].includes(pending.job.status)) return;
    const completed = pending;
    setPending(null); inFlight.current = false;
    if (completed.sourceJobId !== activeJobId.current) return;
    const candidate = completed.job.result?.candidate as MatchCandidate | undefined;
    if (completed.job.status !== "completed" || !candidate?.preview_ready) {
      setError(completed.job.error ?? candidate?.preview_warning ?? "This preview could not be prepared. Choose another match or retry.");
      return;
    }
    const preview = { candidate, jobId: completed.adjusted ? completed.job.id : completed.sourceJobId };
    if (completed.adjusted) {
      const currentId = selected ?? ((job?.result?.candidates ?? []) as MatchCandidate[])[0]?.id;
      if (candidate.id === currentId) { setAdjustment(preview); setOriginal(false); }
    }
    else setPrepared((current) => ({ ...current, [candidate.id]: preview }));
  }, [pending]);

  const cohort = cohorts.find((item) => item.id === identity);
  const candidates = (job?.result?.candidates ?? []) as MatchCandidate[];
  const base = candidates.find((item) => item.id === selected) ?? candidates[0];
  const variant = adjustment?.candidate.id === base?.id ? adjustment : (base ? prepared[base.id] : undefined);
  const chosen = variant?.candidate ?? base;
  const previewJobId = variant?.jobId ?? job?.id;
  const previewActive = preparing || !!pending;
  const stale = !!job && (dirty || job.base_revision !== revision || job.result?.reference_clip_id !== clip?.id);
  const isKept = kept?.candidateId === chosen?.id && kept?.revision === revision && !dirty;
  const subjectReady = cohort?.subject_ready === true;
  const shapeReady = cohort?.shape_ready ?? cohort?.visual_ready;
  const ready = focus === "image" ? shapeReady : focus === "subject" ? subjectReady : focus === "camera" ? cohort?.motion_ready : cohort?.motion_ready || shapeReady || subjectReady;
  const example = cohort?.motion_examples?.[0] ?? cohort?.examples[0];
  const playable = chosen?.preview_ready === true;
  const notices = Array.isArray(job?.result?.notices) ? job.result.notices.filter((value): value is string => typeof value === "string") : [];
  const channelNames: Record<string, string> = { subject: "Subject movement", camera: "Camera movement", shape: "Shape & composition", image: "Image layout", movement: "Movement" };
  const unavailable = [...new Set(notices.map((notice) => channelNames[notice.split(":", 1)[0]]).filter(Boolean))];
  const availableChannels = Array.isArray(job?.result?.available_channels) ? job.result.available_channels.filter((value): value is string => typeof value === "string").map((value) => channelNames[value] ?? value) : [];

  async function preparePreview(candidateId: string, adjusted?: { outgoing_time: number; incoming_time: number }) {
    if (!job || inFlight.current) return;
    const sourceJobId = job.id;
    inFlight.current = true; setPreparing(true); setError("");
    if (!adjusted) attempted.current.add(candidateId);
    try {
      const next = await labRequest<LabJob>(`/jobs/${sourceJobId}/matches/${candidateId}/${adjusted ? "adjust" : "preview"}`, {
        method: "POST", ...(adjusted ? { body: JSON.stringify({ base_revision: revision, ...adjusted }) } : {}),
      });
      if (activeJobId.current === sourceJobId) setPending({ sourceJobId, candidateId, adjusted: !!adjusted, job: next });
    } catch (reason) {
      if (activeJobId.current === sourceJobId) setError(reason instanceof Error ? reason.message : "Could not prepare this preview.");
      inFlight.current = false;
    } finally { if (activeJobId.current === sourceJobId) setPreparing(false); }
  }

  // Serve a selected audition first, then prepare the other top suggestions.
  useEffect(() => {
    if (!job || busy || previewActive || stale || inFlight.current) return;
    const queue = [base, ...candidates.slice(0, 3)].filter((item): item is MatchCandidate => !!item);
    const next = queue.find((item) => !item.preview_ready && !prepared[item.id] && !attempted.current.has(item.id));
    if (next) void preparePreview(next.id);
  }, [job, busy, previewActive, base, prepared, stale]);

  function find() {
    if (!cohort || !clip) return;
    setError(""); setShowSource(false); setShowExport(false); setAdjusting(false); setAdjustment(null);
    onFind({ cohort_id: cohort.id, reference_clip_id: clip.id,
      mode: focus === "image" ? "image" : "movement", movement: focus === "subject" ? "subject" : "camera",
      focus, timing, allow_reframing: reframe, ...(subjectPoint ? { subject_point: subjectPoint } : {}) });
  }
  function useExample() {
    if (!example) return;
    const center = "reference_time" in example ? Number(example.reference_time) : (example.t_start + example.t_end) / 2;
    onExample({ id: crypto.randomUUID(), unit_id: example.unit_id, film_id: example.film_id, title: example.caption,
      source_start: Math.max(example.t_start, center - 2), source_end: Math.min(example.t_end, center + 1), reference_time: center, region: null, crop: null, locked: false });
    setShowSource(true); setShowExport(false);
  }
  const previewSrc = chosen && previewJobId ? (chosen.preview_url ?? `/lab/jobs/${previewJobId}/matches/${chosen.id}/preview`) : null;

  return <section className={cut.workspace} aria-label="Match Cuts workspace">
    <div className={cut.intro}><h1>{clip ? "Find the connection." : "Choose a scene. Find its next shot."}</h1><p>Play the transition. Keep the cut that feels right.</p></div>
    {!clip ? <div className={cut.empty}>
      <div className={cut.emptyPair} aria-hidden="true">A <span><DirectionIcon name="arrow-right" size={28} /></span> B</div>
      <h2>Start with a scene</h2><p>Choose from your films, or try a prepared example.</p>
      <div className={cut.actions}><button className={styles.primary} disabled={busy} onClick={onBrowse}>Choose a scene</button>{example && <button className={styles.secondary} disabled={busy} onClick={useExample}>Try an example</button>}</div>
    </div> : <>
      <div className={cut.toolbar}>
        <button className={styles.secondary} disabled={busy || clip.locked || previewActive} onClick={onBrowse}>Change scene A</button>
        <label>Match focus <select value={focus} disabled={busy || previewActive} onChange={(event) => setFocus(event.target.value as typeof focus)}>
          <option value="auto">Automatic</option>
          <option value="subject" disabled={!subjectReady}>Subject movement{!subjectReady ? " · unavailable" : ""}</option>
          <option value="camera" disabled={!cohort?.motion_ready}>Camera movement{!cohort?.motion_ready ? " · unavailable" : ""}</option>
          <option value="image" disabled={!shapeReady}>Shape & composition{!shapeReady ? " · unavailable" : ""}</option>
        </select></label>
        <button className={styles.primary} disabled={busy || previewActive || !ready || clip.locked} onClick={find}>{busy ? "Finding matches…" : "Find matches"}</button>
      </div>
      <div className={cut.monitor} aria-busy={busy || (!!pending && pending.candidateId === chosen?.id)}>
        {showExport && renderUrl ? <video className={cut.pairVideo} src={mediaUrl(renderUrl)} controls muted playsInline aria-label="Exported cut" /> : !showSource && chosen && job ? <>
          {playable && previewSrc ? <video className={cut.pairVideo} key={`${previewJobId}-${chosen.id}-${original}`} src={mediaUrl(`${previewSrc}${original ? `${previewSrc.includes("?") ? "&" : "?"}original=true` : ""}`)} controls autoPlay muted playsInline preload="auto" aria-label="A to B transition preview" onError={() => setError("This preview could not load. Try another suggestion or prepare it again.")} /> : <div className={cut.placeholder}>
            <img src={mediaUrl(`/lab/jobs/${job.id}/matches/${chosen.id}/frame`)} alt={`Incoming frame from ${chosen.film_title}`} />
            <div role="status">{previewActive || busy ? "Preparing this cut…" : <button className={styles.secondary} onClick={() => void preparePreview(chosen.id)}>Retry preview</button>}</div>
          </div>}
          <div className={cut.caption}>
            <p>{chosen.evidence}{chosen.crop ? " · reframing proposed" : ""}</p>
            <div className={cut.actions}>
              <button className={styles.ghost} disabled={busy || previewActive || stale} aria-expanded={adjusting} onClick={() => { setAdjusting(!adjusting); setShowExport(false); }}>Adjust timing</button>
              <button className={styles.primary} disabled={busy || (preparing && adjusting) || !!pending?.adjusted || stale || !playable || clip.locked || !canApply || isKept} onClick={async () => {
                if (await onApply(chosen.id, adjustment?.candidate.id === chosen.id ? adjustment.jobId : undefined)) { setKept({ candidateId: chosen.id, revision: revision + 1 }); setAdjusting(false); }
              }}>{isKept ? "Cut kept ✓" : "Keep cut"}</button>
            </div>
          </div>
          {chosen.crop && playable ? <label className={cut.original}><input type="checkbox" checked={original} onChange={(event) => setOriginal(event.target.checked)} /> Show original framing</label> : null}
          {adjusting && <MatchTimingControls key={base.id} candidate={base} disabled={busy || previewActive || stale} onPreview={(outgoing, incoming) => void preparePreview(chosen.id, { outgoing_time: outgoing, incoming_time: incoming })} onCancel={() => { setAdjustment(null); setAdjusting(false); }} />}
        </> : <MatchSourcePlayer key={clip.id} clip={clip} disabled={busy || previewActive} timing={timing} onTiming={setTiming} subjectPoint={subjectPoint} subjectReady={subjectReady} onSubjectPoint={setSubjectPoint} onChange={onChange} />}
      </div>
      <div className={cut.pairStrip} aria-label="A to B cut">
        <button aria-pressed={showSource || !chosen} onClick={() => { setShowSource(true); setShowExport(false); }}><b>A</b><span><strong>{clip.title}</strong><small>{seconds(chosen && !showSource ? chosen.outgoing.reference_time ?? chosen.outgoing.source_end : clip.reference_time ?? clip.source_start)} · {timing === "fixed" ? "pinned" : "nearby timing"}</small></span><small>Edit moment</small></button>
        <span aria-hidden="true"><DirectionIcon name="arrow-right" /></span>
        <button disabled={!chosen && !renderUrl} aria-pressed={!showSource && !!chosen} onClick={() => { setShowSource(false); setShowExport(!chosen); }}><b>B</b><span><strong>{chosen?.film_title ?? incoming?.title ?? "Your next shot"}</strong><small>{chosen ? seconds(chosen.incoming.reference_time ?? chosen.incoming.source_start) : "Find matches to audition"}</small></span>{isKept && <small>Kept</small>}</button>
      </div>
      {candidates.length > 0 && <div className={cut.suggestions}>
        <div className={cut.suggestionHeading}><h2>Try a cut</h2><span>{previewActive || busy ? "Preparing previews…" : "Select to play"}</span></div>
        <div className={cut.candidates}>{(showMore ? candidates : candidates.slice(0, 3)).map((candidate, index) => <button key={candidate.id} aria-pressed={candidate.id === chosen?.id} onClick={() => { setSelected(candidate.id); setShowSource(false); setShowExport(false); setOriginal(false); setAdjustment(null); setAdjusting(false); }}>
          <img src={mediaUrl(`/lab/jobs/${job!.id}/matches/${candidate.id}/frame`)} alt="" loading="lazy" /><span><small>{index + 1}</small><strong>{candidate.film_title}</strong><small>{candidate.preview_ready || prepared[candidate.id] ? <EditorIcon name="play" size={12} /> : "…"}</small></span>
        </button>)}</div>
        {candidates.length > 3 && <button className={styles.ghost} onClick={() => setShowMore(!showMore)}>{showMore ? "Show top three" : `${candidates.length - 3} more suggestions`}</button>}
      </div>}
      {job && !candidates.length && !busy && <p className={cut.status} role="status">{String(job.result?.message ?? "No strong matches found. Try another moment or match focus.")}</p>}
      {stale && !isKept && <p className={cut.status}>Your edit changed. Find matches again before keeping another cut.</p>}
      {!canApply && <p className={cut.status}>{applyBlockedReason} {onUnlock && <button className={styles.ghost} disabled={busy} onClick={onUnlock}>Unlock scene B</button>}</p>}
      {clip.locked && <p className={cut.status}>Scene A is locked. Unlock it in project details to change the reference.</p>}
      {chosen?.preview_warning && <p className={cut.status}>{chosen.preview_warning}</p>}
      {notices.length > 0 && <p className={cut.status} role="status">{unavailable.length ? `${unavailable.join(" and ")} could not be assessed for this moment.` : "Some matching evidence was unavailable for this moment."} Results use the available evidence. See Details for the reason.</p>}
    </>}
    <div className={cut.coverage}><span>{loading ? "Checking prepared footage…" : cohort ? `Searching ${cohort.films.length} prepared films` : "No footage is prepared yet"}</span>{cohort && !subjectReady && <span>Subject tracking unavailable</span>}{cohort && !shapeReady && <span>Shape matching unavailable</span>}</div>
    <details className={flow.more}>
      <summary>Details & options</summary>
      <div className={cut.details}>
        <label className={flow.check}><input type="checkbox" checked={reframe} disabled={busy || previewActive} onChange={(event) => setReframe(event.target.checked)} /> Try reframing</label>
        <p>Original speed and framing are the default. Reframing proposes a crop you can compare in playback.</p>
        {cohorts.length > 1 && <label>Prepared footage <select value={identity} disabled={busy || previewActive} onChange={(event) => setIdentity(event.target.value)}>{cohorts.map((item) => <option key={item.id} value={item.id}>{item.shot_count} shots · {item.films.length} films</option>)}</select></label>}
        {cohort && <p>{cohort.shot_count} prepared shots · {cohort.motion_count} movement windows. {cohort.subject_note}</p>}
        {availableChannels.length > 0 && <p>Available matching methods: {availableChannels.join(", ")}.</p>}
        {notices.map((notice, index) => <p key={index}>{notice}</p>)}{scope}
        {example && clip && <button className={styles.ghost} disabled={busy || clip.locked || previewActive} onClick={useExample}>Use an example scene</button>}
      </div>
    </details>
    {error && <p role="alert" className={styles.error}>{error}</p>}
  </section>;
}
