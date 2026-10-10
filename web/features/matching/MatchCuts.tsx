"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { seconds } from "@/lib/lab";
import { DEFAULT_VISION_LAYERS, shownReason } from "@/lib/matchVision";
import {
  cropFits, defaultOut, FOCUS_LABELS, FRAME_SECONDS, LEAD_IN, LEAD_OUT, matchHref, matchRequest, nearestMoment, percent, resultTime,
  settingsFrom, type ChainLink, type MatchFocus, type MatchSettings, type MomentMatch, type MomentSearchResponse,
  type MomentShot, type OutputFormat,
} from "@/lib/matchCuts";
import LabWorkspaceHeader, { LabEmptyState } from "../lab/LabWorkspaceHeader";
import SourceBrowser from "../lab/SourceBrowser";
import { FramedImage } from "./Framed";
import MatchIcon from "./MatchIcon";
import SequencePlayer, { type Segment } from "./SequencePlayer";
import { CutVision, useVision, VisionControls, VisionOverlay, VisionSwitch } from "./Vision";
import styles from "./matchCuts.module.css";

const CHAIN_KEY = "scene-recall:match-cuts-chain";
const VISION_KEY = "scene-recall:match-cuts-vision";

interface VisionView { on: boolean; visible: ReadonlySet<string> }

/** Vision is remembered in this browser: whether it is on, and which layers show. */
function readVision(): VisionView {
  try {
    const saved = JSON.parse(window.localStorage.getItem(VISION_KEY) ?? "null");
    if (saved && typeof saved.on === "boolean" && Array.isArray(saved.visible)) return { on: saved.on, visible: new Set(saved.visible) };
  } catch { /* unreadable: the defaults */ }
  return { on: false, visible: new Set(DEFAULT_VISION_LAYERS) };
}
function writeVision(vision: VisionView) {
  try { window.localStorage.setItem(VISION_KEY, JSON.stringify({ on: vision.on, visible: [...vision.visible] })); } catch { /* not remembered */ }
}
/** The audition opens in the view used last (for this page). */
let auditionView: "play" | "overlay" = "play";

const FORMATS: { value: OutputFormat; label: string }[] = [
  { value: "landscape", label: "16:9" }, { value: "vertical", label: "9:16" }, { value: "square", label: "1:1" },
];
const frameUrl = (filmId: string, time: number, width = 640) =>
  `/matching/moments/frame?film_id=${filmId}&time=${time.toFixed(3)}&w=${width}`;

function readChain(): ChainLink[] {
  try { return JSON.parse(window.sessionStorage.getItem(CHAIN_KEY) ?? "[]"); } catch { return []; }
}
function writeChain(chain: ChainLink[]) {
  try { window.sessionStorage.setItem(CHAIN_KEY, JSON.stringify(chain)); } catch { /* a private window keeps the chain in memory only */ }
}

export default function MatchCuts() {
  const router = useRouter();
  const params = useSearchParams();
  const unitId = params.get("unit_id") ?? "";
  const urlTime = params.get("time");
  const settings = useMemo(() => settingsFrom(new URLSearchParams(params.toString())), [params]);
  const [status, setStatus] = useState<{ ready: boolean; moments?: number; films?: { film_id: string }[] } | null>(null);
  const [shot, setShot] = useState<MomentShot | null>(null);
  const [time, setTime] = useState<number | null>(null);
  const [response, setResponse] = useState<MomentSearchResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const [choosing, setChoosing] = useState(false);
  const [selected, setSelected] = useState<MomentMatch | null>(null);
  const [chain, setChain] = useState<ChainLink[]>([]);
  const [playingChain, setPlayingChain] = useState(false);
  // The cut under the pointer and, while on one of its reasons, that reason.
  const [hovered, setHovered] = useState<{ match: MomentMatch; reason: string | null } | null>(null);
  const [vision, setVision] = useState<VisionView>({ on: false, visible: new Set(DEFAULT_VISION_LAYERS) });
  const requestId = useRef(0);
  const referenceVision = useVision(shot?.unit_id ?? null, time, vision.on);
  const hoveredVision = useVision(hovered?.match.unit_id ?? null, hovered?.match.time ?? null, vision.on && hovered !== null);
  const changeVision = (next: VisionView) => { setVision(next); writeVision(next); };

  useEffect(() => { setChain(readChain()); setVision(readVision()); }, []);
  useEffect(() => {
    const controller = new AbortController();
    matchRequest<{ ready: boolean; moments?: number; films?: { film_id: string }[] }>("/status", { signal: controller.signal })
      .then(setStatus).catch((reason) => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!unitId) { setShot(null); setTime(null); setResponse(null); return; }
    const controller = new AbortController();
    setError("");
    const query = new URLSearchParams({ unit_id: unitId, ...(urlTime ? { time: urlTime } : {}) });
    matchRequest<MomentShot>(`/shot?${query}`, { signal: controller.signal })
      .then((next) => { setShot(next); setTime(nearestMoment(next, urlTime ? Number(urlTime) : next.time)); })
      .catch((reason) => { if (!controller.signal.aborted) { setShot(null); setError(reason.message); } });
    return () => controller.abort();
  }, [unitId, urlTime]);

  const chained = chain.length > 0 && chain[chain.length - 1].unit_id === unitId;
  const outgoingCrop = chained ? chain[chain.length - 1].crop : null;
  const excluded = useMemo(() => chain.map((link) => link.unit_id), [chain]);

  useEffect(() => {
    if (!shot || time === null || shot.unit_id !== unitId) return;
    const id = ++requestId.current;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setSearching(true);
      matchRequest<MomentSearchResponse>("/search", {
        method: "POST", signal: controller.signal,
        body: JSON.stringify({ unit_id: shot.unit_id, time, focus: settings.focus, output: settings.output,
          reframe: settings.reframe, include_same_film: settings.includeSameFilm, exclude_unit_ids: excluded,
          outgoing_crop: outgoingCrop && shot && cropFits(outgoingCrop, shot.aspect, settings.output) ? outgoingCrop : null,
          limit: 30 }),
      }).then((next) => { if (id === requestId.current) { setResponse(next); setError(""); } })
        .catch((reason) => { if (!controller.signal.aborted && id === requestId.current) setError(reason.message); })
        .finally(() => { if (id === requestId.current) setSearching(false); });
    }, 160);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [shot, time, settings, excluded, outgoingCrop, unitId]);

  function navigate(next: Partial<MatchSettings> & { unit?: string; at?: number }, replace = true) {
    const href = matchHref(next.unit ?? unitId, next.at ?? time ?? 0, { ...settings, ...next });
    if (replace) router.replace(href, { scroll: false }); else router.push(href, { scroll: false });
  }

  function commitTime(value: number) {
    if (!shot) return;
    const snapped = nearestMoment(shot, value);
    setTime(snapped);
    if (chained) updateChain(chain.map((link, index) => index === chain.length - 1 ? { ...link, end: snapped } : link));
    navigate({ at: snapped });
  }

  function updateChain(next: ChainLink[]) { setChain(next); writeChain(next); }

  function addToChain(match: MomentMatch, start: number) {
    if (!shot || time === null || !response) return;
    const base = chained ? chain : [{
      unit_id: shot.unit_id, film_id: shot.film_id, film_title: shot.film_title, start: Math.max(shot.t_start, time - LEAD_OUT),
      end: time, t_start: shot.t_start, t_end: shot.t_end, crop: response.reference.crop, aspect: shot.aspect,
      content_box: shot.content_box,
    }];
    const link: ChainLink = { unit_id: match.unit_id, film_id: match.film_id, film_title: match.film_title, start,
      end: 0, t_start: match.t_start, t_end: match.t_end, crop: match.crop, aspect: match.aspect, content_box: match.content_box };
    link.end = defaultOut(link);
    const next = [...base.slice(0, -1), { ...base[base.length - 1], end: time }, link];
    updateChain(next);
    setSelected(null);
    navigate({ unit: match.unit_id, at: link.end }, false);
  }

  function undoLink() {
    if (chain.length < 2) { updateChain([]); return; }
    const next = chain.slice(0, -1);
    updateChain(next.length === 1 ? [] : next);
    const last = next[next.length - 1];
    navigate({ unit: last.unit_id, at: last.end }, false);
  }

  const results = response && response.reference.unit_id === unitId ? response.results : [];
  const reference = response && response.reference.unit_id === unitId ? response.reference : null;
  const referenceCrop = reference?.crop ?? outgoingCrop ?? null;

  return <main className={styles.lab}>
    <LabWorkspaceHeader title="Match Cuts" />
    <div className={styles.page}>
    {status && !status.ready && <p className={styles.notice}>The match index is not built yet. Run <code>python -m pipeline.evidence moments</code>, then <code>python -m pipeline.matching.moments index</code>.</p>}
    {error && <p className={styles.error} role="alert">{error}</p>}
    {!unitId ? <LabEmptyState title="Find match cuts" description="Pick any instant of any shot. Every instant across the library is compared by subject, pose, light and motion, and the best cut lands on the matching frame.">
      <button className={styles.primary} onClick={() => setChoosing(true)}><MatchIcon name="search" /> Choose a shot</button>
    </LabEmptyState> : <div className={styles.workspace}>
      <aside className={styles.side} aria-label="Cut from">
        <header className={styles.sideHeader}><h2>Cut from</h2><button className={styles.textButton} onClick={() => setChoosing(true)}>Change shot</button></header>
        {shot && time !== null ? <>
          <FramedImage src={frameUrl(shot.film_id, time, 1280)} crop={referenceCrop} aspect={shot.aspect} output={settings.output}
            alt={`${shot.film_title} at ${seconds(time)}`} eager>
            {(place) => referenceVision && <VisionOverlay vision={referenceVision} visible={vision.visible} place={place} />}
          </FramedImage>
          <div className={styles.sourceLine}><strong>{shot.film_title}</strong><time>{seconds(time)}</time></div>
          <input className={styles.scrub} type="range" aria-label="Cut point" min={shot.t_start} max={shot.t_end} step={0.25}
            value={time} onChange={(event) => setTime(Number(event.target.value))}
            onPointerUp={(event) => commitTime(Number((event.target as HTMLInputElement).value))}
            onKeyUp={(event) => commitTime(Number((event.target as HTMLInputElement).value))} />
          <div className={styles.scrubLabels}><time>{seconds(shot.t_start)}</time><span>The last frame before the cut</span><time>{seconds(shot.t_end)}</time></div>
          <VisionControls on={vision.on} onToggle={(on) => changeVision({ ...vision, on })} layers={referenceVision?.layers ?? []}
            visible={vision.visible} onVisibleChange={(visible) => changeVision({ ...vision, visible })} vision={referenceVision} />
        </> : <div className={styles.placeholder} />}
        <section className={styles.settings} aria-label="Match settings">
          <div className={styles.setting}><span>Format</span><div className={styles.segmented}>{FORMATS.map((format) =>
            <button key={format.value} aria-pressed={settings.output === format.value} onClick={() => navigate({ output: format.value })}>{format.label}</button>)}</div></div>
          <div className={styles.setting}><span>Match on</span><div className={styles.chips}>{(Object.keys(FOCUS_LABELS) as MatchFocus[]).map((focus) =>
            <button key={focus} aria-pressed={settings.focus === focus} onClick={() => navigate({ focus })}>{FOCUS_LABELS[focus]}</button>)}</div></div>
          <label className={styles.check}><input type="checkbox" checked={settings.reframe} onChange={(event) => navigate({ reframe: event.target.checked })} />
            <span>Reframe to align<small>Zooms the next shot up to 1.5x so its subject lands on the same spot</small></span></label>
          <label className={styles.check}><input type="checkbox" checked={settings.includeSameFilm} onChange={(event) => navigate({ includeSameFilm: event.target.checked })} />
            <span>Other scenes of this film</span></label>
        </section>
      </aside>
      <section className={styles.results} aria-label="Matches">
        <header className={styles.resultsHeader}>
          <h2>Cuts to <span>{results.length || ""}</span></h2>
          <span className={styles.meta}>{searching ? "Searching…" : response ? `${response.searched.moments.toLocaleString()} instants in ${response.searched.films} films · ${Math.round(response.elapsed_ms.total)} ms` : ""}</span>
        </header>
        {!results.length && !searching && response && <p className={styles.empty}>No usable matches. Try another instant or loosen the settings.</p>}
        <div className={styles.grid}>{results.map((match, index) => {
          const isHovered = hovered?.match === match;
          // With Vision on, the hovered cut draws why it matched: its strongest reason, or the one pointed at.
          const shown = isHovered && vision.on ? shownReason(match.reasons, hovered.reason) : null;
          return <article key={`${match.unit_id}:${match.time}`} className={styles.card}
            onMouseEnter={() => setHovered({ match, reason: null })} onMouseLeave={() => setHovered(null)}>
            <button className={styles.cardImage} onClick={() => setSelected(match)} aria-label={`Audition cut ${index + 1}: ${match.film_title}`}>
              <FramedImage src={match.frame_url} crop={match.crop} aspect={match.aspect} output={settings.output} alt={`${match.film_title} at ${seconds(match.time)}`}
                eager={index < 8} overlay={isHovered && reference ? { src: reference.frame_url, crop: reference.crop, aspect: reference.aspect } : null}>
                {(place, over) => <>
                  <span className={styles.rank}>{index + 1}</span>
                  {shown && <CutVision incoming={hoveredVision} outgoing={referenceVision} reason={shown} place={place} over={over} />}
                </>}
              </FramedImage>
            </button>
            <div className={styles.cardBody}>
              <div className={styles.cardTitle}><h3>{match.film_title}</h3><time>{seconds(match.time)}</time></div>
              <div className={styles.reasons}>{match.reasons.length ? match.reasons.slice(0, 3).map((reason) =>
                <span key={reason.code} data-shown={shown?.code === reason.code || undefined} onMouseEnter={() => setHovered({ match, reason: reason.code })}
                  title={`Stronger than ${percent(0.9 + reason.strength / 10)} of random cuts`}>{reason.label}</span>)
                : <span className={styles.loose} title="Nothing clearly lines up; the closest the library has">Loose match</span>}</div>
            </div>
          </article>;
        })}</div>
      </section>
    </div>}
    {chain.length > 0 && <footer className={styles.chain} aria-label="Match-cut chain">
      <div className={styles.chainLinks}>{chain.map((link, index) =>
        <div key={`${link.unit_id}:${index}`} className={styles.chainLink} data-current={index === chain.length - 1}>
          <FramedImage src={frameUrl(link.film_id, index === 0 ? link.end : link.start, 320)} crop={link.crop} aspect={link.aspect} output={settings.output} alt={link.film_title} />
          <span>{link.film_title}</span><time>{(link.end - link.start).toFixed(1)}s</time>
        </div>)}</div>
      <div className={styles.chainActions}>
        <button className={styles.primary} disabled={chain.length < 2} onClick={() => setPlayingChain(true)}><MatchIcon name="play" /> Play chain</button>
        <button className={styles.secondary} onClick={undoLink}>Undo last</button>
        <button className={styles.textButton} onClick={() => updateChain([])}>Clear</button>
      </div>
    </footer>}
    {choosing && <SourceBrowser context="match" filmIds={[]} replacing={!!unitId} onClose={() => setChoosing(false)}
      onSelect={(result) => { setChoosing(false); updateChain([]); navigate({ unit: result.unit_id, at: resultTime(result) }, false); }} />}
    {selected && shot && time !== null && reference && <Audition match={selected} shot={shot} time={time} referenceCrop={reference.crop}
      output={settings.output} vision={vision} onVision={(on) => changeVision({ ...vision, on })} onClose={() => setSelected(null)} onChain={addToChain}
      onOpen={(match, start) => { setSelected(null); updateChain([]); navigate({ unit: match.unit_id, at: start }, false); }} />}
    {playingChain && <Dialog title="Chain" onClose={() => setPlayingChain(false)}>
      <SequencePlayer output={settings.output} segments={chain.map((link, index): Segment => ({ key: `${link.unit_id}:${index}`, filmId: link.film_id,
        start: link.start, end: link.end, crop: link.crop, aspect: link.aspect, contentBox: link.content_box, label: link.film_title }))} />
    </Dialog>}
    </div>
  </main>;
}

function Audition({ match, shot, time, referenceCrop, output, vision, onVision, onClose, onChain, onOpen }: {
  match: MomentMatch;
  shot: MomentShot;
  time: number;
  referenceCrop: [number, number, number, number] | null;
  output: OutputFormat;
  vision: VisionView;
  onVision: (on: boolean) => void;
  onClose: () => void;
  onChain: (match: MomentMatch, start: number) => void;
  onOpen: (match: MomentMatch, start: number) => void;
}) {
  const [start, setStart] = useState(match.time);
  const [view, setView] = useState(auditionView);
  const [opacity, setOpacity] = useState(0.5);
  // Vision shows one reason at a time: the strongest, until another is pointed at.
  const [pointed, setPointed] = useState<string | null>(null);
  useEffect(() => { setStart(match.time); setPointed(null); }, [match]);
  const chooseView = (next: "play" | "overlay") => { auditionView = next; setView(next); };
  const shown = shownReason(match.reasons, pointed);
  const incoming = useVision(match.unit_id, start, vision.on && view === "overlay");
  const outgoing = useVision(shot.unit_id, time, vision.on && view === "overlay");
  const nudge = (delta: number) => setStart((value) => Math.max(match.t_start, Math.min(match.t_end - 0.1, value + delta)));
  const segments: Segment[] = [
    { key: `a:${shot.unit_id}`, filmId: shot.film_id, start: Math.max(shot.t_start, time - LEAD_IN), end: time, crop: referenceCrop,
      aspect: shot.aspect, contentBox: shot.content_box, label: shot.film_title },
    { key: `b:${match.unit_id}`, filmId: match.film_id, start, end: Math.min(match.t_end, start + LEAD_OUT), crop: match.crop,
      aspect: match.aspect, contentBox: match.content_box, label: match.film_title },
  ];
  return <Dialog title={`${shot.film_title} → ${match.film_title}`} onClose={onClose}>
    <div className={styles.viewTabs} role="tablist">
      <button role="tab" aria-selected={view === "play"} onClick={() => chooseView("play")}>Play the cut</button>
      <button role="tab" aria-selected={view === "overlay"} onClick={() => chooseView("overlay")}>Overlay</button>
    </div>
    {view === "play" ? <SequencePlayer segments={segments} output={output} /> : <div className={styles.overlayView}>
      <FramedImage src={frameUrl(match.film_id, start, 1280)} crop={match.crop} aspect={match.aspect} output={output} alt="Incoming frame"
        overlay={{ src: frameUrl(shot.film_id, time, 1280), crop: referenceCrop, aspect: shot.aspect }} overlayOpacity={opacity} eager>
        {(place, over) => vision.on && <CutVision incoming={incoming} outgoing={outgoing} reason={shown} place={place} over={over} />}
      </FramedImage>
      <div className={styles.overlayControls}>
        <label className={styles.opacity}>Outgoing frame <input type="range" min={0} max={1} step={0.05} value={opacity} onChange={(event) => setOpacity(Number(event.target.value))} /></label>
        <VisionSwitch on={vision.on} onToggle={onVision} />
      </div>
      {/* The reasons are Vision's legend: point at one, or pick it, to draw it on both frames. */}
      {vision.on && <ul className={styles.reasonBars} aria-label="Why this cut matched">{match.reasons.map((reason) =>
        <li key={reason.code} data-shown={shown?.code === reason.code || undefined}>
          {reason.layers?.length
            ? <button type="button" className={styles.reasonName} aria-pressed={shown?.code === reason.code}
                onMouseEnter={() => setPointed(reason.code)} onFocus={() => setPointed(reason.code)} onClick={() => setPointed(reason.code)}>{reason.label}</button>
            : <span>{reason.label}</span>}
          <span className={styles.strength}><span style={{ width: `${Math.round(reason.strength * 100)}%` }} /></span>
          <data value={reason.strength}>{reason.strength.toFixed(2)}</data></li>)}</ul>}
    </div>}
    <div className={styles.auditionBar}>
      <div className={styles.nudge} aria-label="Incoming first frame">
        <button className={styles.secondary} onClick={() => nudge(-0.25)} title="A quarter second earlier">−¼s</button>
        <button className={styles.secondary} onClick={() => nudge(-FRAME_SECONDS)} title="One frame earlier">−1f</button>
        <time>{seconds(start)}</time>
        <button className={styles.secondary} onClick={() => nudge(FRAME_SECONDS)} title="One frame later">+1f</button>
        <button className={styles.secondary} onClick={() => nudge(0.25)} title="A quarter second later">+¼s</button>
      </div>
      {/* With Vision's bars showing, the plain reasons would repeat them. */}
      {!(vision.on && view === "overlay") && <div className={styles.reasons}>{match.reasons.map((reason) => <span key={reason.code}>{reason.label}</span>)}</div>}
      <div className={styles.auditionActions}>
        <button className={styles.secondary} onClick={() => onOpen(match, start)}>Match from this shot</button>
        <button className={styles.primary} onClick={() => onChain(match, start)}>Add to chain &amp; continue</button>
      </div>
    </div>
  </Dialog>;
}

function Dialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const target = dialog.current;
    target?.showModal();
    return () => target?.close();
  }, []);
  return <dialog ref={dialog} className={styles.dialog} onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClick={(event) => { if (event.target === dialog.current) onClose(); }}>
    <header className={styles.dialogHeader}><h2>{title}</h2><button className={styles.iconButton} onClick={onClose} aria-label="Close"><MatchIcon name="close" /></button></header>
    {children}
  </dialog>;
}
