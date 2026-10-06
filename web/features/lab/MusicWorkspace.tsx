"use client";

import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { LAB_API, experimentName, labRequest, mediaUrl, seconds } from "@/lib/lab";
import { MAX_DIRECTION_BATCH_SHOTS, MAX_LAB_SAVED_CLIPS, MAX_MUSIC_TIMELINE_SLOTS } from "@/lib/labLimits";
import type {
  LabClip,
  LabDocument,
  MusicMatchEvidence,
  MusicSearchCapabilities,
  NextSceneAdjustment,
  NextSceneOptions,
} from "@/types/lab";
import { useLabProject } from "./useLabProject";
import { useAudioWaveform } from "./useAudioWaveform";
import {
  clearSlot,
  changeMusicPassage,
  directionOf,
  editSlotDirection,
  editSlotSearchPlan,
  joinWithNext,
  moveCut,
  keepCutPlan,
  placeClip,
  planOf,
  searchInputProblem,
  splitSlot,
} from "./musicEdit";
import MusicEditTimeline from "./MusicEditTimeline";
import DialogueEditor from "./DialogueEditor";
import { addDialogue, updateDialogue } from "./dialogueAudio";
import MusicSequencePlayer, { type MusicSequenceHandle } from "./MusicSequencePlayer";
import SongPassagePicker from "./SongPassagePicker";
import SourceWindowReview from "./SourceWindowReview";
import MusicDirectionPanel from "./MusicDirectionPanel";
import MusicSearchDetails from "./MusicSearchDetails";
import MusicAnalysisDetails from "./MusicAnalysisDetails";
import JobStatus from "./JobStatus";
import SceneLibraryPanel from "./SceneLibraryPanel";
import LabWorkspaceHeader, { LabEmptyState } from "./LabWorkspaceHeader";
import EditorIcon from "./EditorIcon";
import DirectionIcon from "@/components/DirectionIcon";
import EditorPopover from "./EditorPopover";
import { fitDraggedScene } from "./sceneLibrary";
import NextScenePanel, { type NextSceneAuditionSelection } from "./NextScenePanel";
import NextSceneAudition from "./NextSceneAudition";
import { nextScenePair, nextSceneResult, sameSceneCrop } from "./nextScene";
import styles from "./musicWorkspace.module.css";

const EXPERIMENT_NAME = experimentName("music-sketch");
type WorkspaceView = "ai" | "edit";

export default function MusicWorkspace() {
  const state = useLabProject("music-sketch");
  const document = state.document;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedDialogueId, setSelectedDialogueId] = useState<string | null>(null);
  const [dialogueAudition, setDialogueAudition] = useState(false);
  const [playhead, setPlayhead] = useState(0);
  const [sequencePlaying, setSequencePlaying] = useState(false);
  const sequencePlayer = useRef<MusicSequenceHandle>(null);
  const [snap, setSnap] = useState(false);
  const [libraryView, setLibraryView] = useState<"next" | "search">("search");
  const [nextAudition, setNextAudition] = useState<NextSceneAuditionSelection | null>(null);
  const [auditionTime, setAuditionTime] = useState(0);
  const [auditionCut, setAuditionCut] = useState(0);
  const [auditionVersion, setAuditionVersion] = useState(0);
  const [passageOpen, setPassageOpen] = useState(false);
  const [chosenView, setChosenView] = useState<WorkspaceView | null>(null);
  const viewProject = useRef<string | null>(null);
  const requestedGeneration = useRef<string | null>(null);
  const [showExport, setShowExport] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [capabilities, setCapabilities] = useState<MusicSearchCapabilities | null>(
    null,
  );
  const [history, setHistory] = useState<{ revision: number; name: string }[]>(
    [],
  );
  const [footageReview, setFootageReview] = useState<{
    clip: LabClip;
    slotId: string;
    evidence?: MusicMatchEvidence | null;
  } | null>(null);
  const draggedScene = useRef<{ clip: LabClip; evidence: MusicMatchEvidence | null } | null>(null);
  const [draggedSceneDuration, setDraggedSceneDuration] = useState<number | null>(null);
  const [localError, setLocalError] = useState("");
  const [films, setFilms] = useState<
    Record<string, { title: string; duration?: number }>
  >({});
  const passageDialog = useRef<HTMLDialogElement>(null),
    exportDialog = useRef<HTMLDialogElement>(null);
  const passageSnapshot = useRef<{
    revision: number;
    undo: LabDocument[];
    draft: ReturnType<typeof state.captureDraft>;
  } | null>(null);
  const requestedExport = useRef(false);
  const requestedShot = useRef<string | null>(null);
  const plan = useMemo(() => (document ? planOf(document) : null), [document]);
  const slots = plan?.slots ?? [];
  const selected =
    slots.find((slot) => slot.id === selectedId) ?? slots[0] ?? null;
  const direction =
    document && selected ? directionOf(document, selected) : null;
  const selectedIndex = slots.findIndex((slot) => slot.id === selected?.id);
  const nextSlot = slots[selectedIndex + 1];
  const nextClip = document?.clips.find(
    (item) => item.id === nextSlot?.clip_id,
  );
  const playheadSlot = slots.find(
    (slot) => slot.start <= playhead && playhead < slot.end,
  );
  const canAddCut =
    !!playheadSlot &&
    slots.length < MAX_MUSIC_TIMELINE_SLOTS &&
    (!playheadSlot.clip_id || (document?.clips.length ?? 0) < MAX_LAB_SAVED_CLIPS) &&
    playhead >= playheadSlot.start + 0.25 &&
    playhead <= playheadSlot.end - 0.25 &&
    !document?.clips.find((item) => item.id === playheadSlot.clip_id)?.locked;
  const hasPlacedLocks = slots.some(
    (slot) => document?.clips.find((item) => item.id === slot.clip_id)?.locked,
  );
  const clip =
    document?.clips.find((item) => item.id === selected?.clip_id) ?? null;
  const reviewedSlot = slots.find((slot) => slot.id === footageReview?.slotId);
  const reviewedClip = document?.clips.find(
    (item) => item.id === reviewedSlot?.clip_id,
  );
  const empty = slots.filter((slot) => !slot.clip_id).length;
  const hasEdit = slots.some((slot) => !!slot.clip_id) || !!document?.analysis?.draft;
  const activeView = chosenView ?? (hasEdit ? "edit" : "ai");
  const working = state.busy || state.activeJob;
  const nextPair = nextScenePair(slots, document?.clips ?? [], selected?.id ?? null);
  const nextResult = nextSceneResult(state.nextSceneJob);
  const nextFresh = !!state.nextSceneJob && state.nextSceneJob.status === "completed" && !state.dirty && state.project?.id === state.nextSceneJob?.project_id &&
    state.project?.revision === state.nextSceneJob?.base_revision;
  const visibleAudition = nextAudition && nextFresh && libraryView === "next" &&
    nextAudition.jobId === state.nextSceneJob?.id && nextAudition.revision === state.project?.revision &&
    nextAudition.scope.anchor_slot_id === nextPair.anchorSlot?.id &&
    nextAudition.scope.next_slot_id === nextPair.nextSlot?.id ? nextAudition : null;
  const undoCommand = useRef({ undo: state.undo, available: false });
  undoCommand.current = {
    undo: state.undo,
    available:
      state.canUndo &&
      !working &&
      !passageOpen &&
      !showExport &&
      !footageReview,
  };
  const searchProblem = searchInputProblem(
    direction,
    document?.clips ?? [],
    capabilities,
  );
  const { peaks, status: waveformStatus } = useAudioWaveform(
    document?.track ?? null,
  );
  const renderUrl = state.render
    ? mediaUrl(
      String(
        state.render.result?.output_url ??
        `/lab/jobs/${state.render.id}/output`,
      ),
    )
    : null;
  const durationMap = useMemo(
    () =>
      Object.fromEntries(
        Object.entries(films).filter(([, film]) => Number.isFinite(film.duration)).map(([id, film]) => [id, film.duration!]),
      ),
    [films],
  );
  const filmTitles = useMemo(
    () => Object.fromEntries(Object.entries(films).map(([id, film]) => [id, film.title])),
    [films],
  );
  const savedClips = useMemo(() => {
    const placed = new Set(plan?.slots.map((slot) => slot.clip_id));
    return document?.clips.filter((item) => !placed.has(item.id)) ?? [];
  }, [document?.clips, plan]);

  useEffect(() => {
    if (!state.project || !document || viewProject.current === state.project.id) return;
    const previous = viewProject.current;
    viewProject.current = state.project.id;
    // Saving an unsaved draft must preserve its open tab and pending generation.
    if (previous === "" && state.project.id) return;
    requestedGeneration.current = null;
    const requested = new URLSearchParams(window.location.search).get("view");
    setChosenView(requested === "ai" || requested === "edit" ? requested : hasEdit ? "edit" : "ai");
  }, [state.project?.id, !!document, hasEdit]);

  useEffect(() => {
    const job = state.job;
    if (!job || job.id !== requestedGeneration.current) return;
    if (["failed", "cancelled", "interrupted"].includes(job.status) ||
      (job.status === "completed" && job.result?.applied !== true)) {
      requestedGeneration.current = null;
    } else if (job.status === "completed" && job.result?.applied === true &&
      job.result.revision === state.project?.revision && !state.dirty) {
      requestedGeneration.current = null;
      switchView("edit");
    }
  }, [state.job?.id, state.job?.status, state.job?.result, state.project?.revision, state.dirty]);

  useEffect(() => {
    if (nextAudition && !visibleAudition) setNextAudition(null);
  }, [nextAudition, visibleAudition]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (
        event.defaultPrevented ||
        event.repeat ||
        event.isComposing ||
        !(event.ctrlKey || event.metaKey) ||
        event.altKey ||
        event.shiftKey ||
        event.key.toLowerCase() !== "z" ||
        window.document.querySelector("dialog[open]") ||
        (event.target as Element | null)?.closest(
          "input, textarea, select, [contenteditable]:not([contenteditable='false']), [role='textbox']",
        )
      )
        return;
      if (undoCommand.current.available) {
        event.preventDefault();
        undoCommand.current.undo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void labRequest<MusicSearchCapabilities>("/music/search-capabilities", {
      signal: controller.signal,
    })
      .then(setCapabilities)
      .catch(() => { });
    fetch(`${LAB_API}/library`, { signal: controller.signal })
      .then((response) => response.json())
      .then((rows: { film_id: string; title: string; duration?: number }[]) => {
        setFilms(
          Object.fromEntries(
            rows
              .filter((row) => row.film_id)
              .map((row) => [
                row.film_id,
                { title: row.title, duration: Number.isFinite(row.duration) ? row.duration : undefined },
              ]),
          ),
        );
      })
      .catch(() => { });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (document?.track) {
      setPlayhead(document.passage.start);
      sequencePlayer.current?.seek(document.passage.start);
    }
  }, [document?.track?.id, document?.passage.start, document?.passage.end]);
  useEffect(() => {
    const dialog = passageDialog.current;
    if (passageOpen) dialog?.showModal();
    else dialog?.close();
  }, [passageOpen, state.project?.id]);
  useEffect(() => {
    const dialog = exportDialog.current;
    if (showExport) dialog?.showModal();
    else dialog?.close();
  }, [showExport]);
  useEffect(() => {
    if (!requestedExport.current || state.job?.kind !== "render") return;
    if (state.job.status === "completed") {
      requestedExport.current = false;
      setShowExport(true);
    } else if (
      ["failed", "cancelled", "interrupted"].includes(state.job.status)
    )
      requestedExport.current = false;
  }, [state.job?.id, state.job?.status]);

  useEffect(() => {
    if (
      !requestedShot.current ||
      !state.job ||
      !["draft", "generate"].includes(state.job.kind)
    )
      return;
    if (state.job.status === "completed") {
      setSelectedId(requestedShot.current);
      setLibraryView("search");
      requestedShot.current = null;
    } else if (
      ["failed", "cancelled", "interrupted"].includes(state.job.status)
    )
      requestedShot.current = null;
  }, [state.job?.id, state.job?.status]);

  function seek(time: number) {
    setNextAudition(null);
    setPlayhead(time);
    sequencePlayer.current?.seek(time);
  }
  function switchView(view: WorkspaceView) {
    state.endChange();
    if (view === "edit") sequencePlayer.current?.seek(playhead);
    setChosenView(view);
    setFootageReview(null);
    const query = new URLSearchParams(window.location.search);
    query.set("view", view);
    window.history.replaceState(window.history.state, "", `?${query.toString()}`);
  }
  function edit(update: (value: LabDocument) => LabDocument, group?: string) {
    if (working) return;
    setLocalError("");
    try {
      state.change(update, group);
    } catch (error) {
      setLocalError(
        error instanceof Error ? error.message : "Could not update this edit.",
      );
    }
  }
  function addCut() {
    if (!canAddCut || !playheadSlot) return;
    edit((current) => splitSlot(current, playheadSlot.id, playhead));
    setSelectedId(playheadSlot.id);
  }
  function useDialogue(source: LabClip, evidence: MusicMatchEvidence | null = null) {
    if (!document?.track || working) return;
    const id = crypto.randomUUID();
    const words = evidence?.matched_text_view === "dialogue" ? evidence.matched_text : "";
    edit((current) => addDialogue(current, source, id, selected?.start ?? playhead, words));
    setSelectedDialogueId(id);
    setNextAudition(null);
  }
  function splitAt(time: number) {
    const target = slots.find((slot) => slot.start < time && time < slot.end);
    if (!target || working) return;
    edit((current) => splitSlot(current, target.id, time));
    setSelectedId(target.id);
    seek(Math.round(time * 24) / 24);
  }
  function removeCut(index: number) {
    const left = slots[index - 1];
    if (!left || working) return;
    edit((current) => joinWithNext(current, left.id, durationMap));
    setSelectedId(left.id);
  }
  function removeScene(slotId: string) {
    const target = slots.find((slot) => slot.id === slotId);
    const source = document?.clips.find((item) => item.id === target?.clip_id);
    if (activeView !== "edit" || working || !source || source.locked) return;
    edit((current) => clearSlot(current, slotId));
  }
  function planDirections(slotIds: string[]) {
    if (working || !slotIds.length || slotIds.length > MAX_DIRECTION_BATCH_SHOTS) return;
    state.change((current) =>
      current.music_timeline
        ? keepCutPlan(current)
        : { ...current, music_timeline: planOf(current) },
    );
    void state.startJob("plan", { slotIds });
  }
  function choose(source: LabClip, evidence: MusicMatchEvidence | null = null) {
    if (!selected) return;
    const fromSearch = selected.alternatives.some((item) => item.clip.id === source.id);
    edit((current) => placeClip(current, selected.id, source, evidence, !fromSearch));
    seek(selected.start);
  }
  function applyReviewedSource(source: LabClip) {
    if (!footageReview || working) return;
    const sourceAdjusted = Math.abs(source.source_start - footageReview.clip.source_start) >= 0.001 ||
      Math.abs(source.source_end - footageReview.clip.source_end) >= 0.001 ||
      !sameSceneCrop(source.crop, footageReview.clip.crop);
    edit((current) => {
      const slot = planOf(current)?.slots.find(
        (item) => item.id === footageReview.slotId,
      );
      const alternative = slot?.alternatives.find(
        (item) =>
          item.clip.id === source.id &&
          Math.abs(item.clip.source_start - source.source_start) < 0.001 &&
          Math.abs(item.clip.source_end - source.source_end) < 0.001 &&
          sameSceneCrop(item.clip.crop, source.crop),
      );
      return placeClip(
        current,
        footageReview.slotId,
        source,
        sourceAdjusted ? null : alternative?.search_evidence ?? (
          Math.abs(source.source_start - footageReview.clip.source_start) < 0.001 &&
            Math.abs(source.source_end - footageReview.clip.source_end) < 0.001 &&
            sameSceneCrop(source.crop, footageReview.clip.crop)
            ? footageReview.evidence ?? null : null
        ),
        sourceAdjusted || !alternative,
      );
    });
    setFootageReview(null);
  }
  function dragScene(source: LabClip | null, evidence: MusicMatchEvidence | null = null) {
    draggedScene.current = source ? { clip: source, evidence } : null;
    setDraggedSceneDuration(source ? source.source_end - source.source_start : null);
  }
  function dropScene(slotId: string) {
    const source = draggedScene.current;
    const target = slots.find((slot) => slot.id === slotId);
    if (working || !source || !target ||
      document?.clips.some((clip) => clip.id === target.clip_id && clip.locked) ||
      source.clip.source_end - source.clip.source_start < target.end - target.start - 1e-6
    ) return;
    const fitted = fitDraggedScene(source.clip, target.end - target.start, source.evidence);
    const fromTargetSearch = target.alternatives.some((item) => item.clip.id === source.clip.id);
    edit((current) => placeClip(current, slotId, fitted, source.evidence, !fromTargetSearch));
    setSelectedId(slotId);
    seek(target.start);
    dragScene(null);
  }
  function findNextScene(options: NextSceneOptions) {
    if (working || nextPair.problem) return;
    setNextAudition(null);
    state.change((current) => current.music_timeline ? current : { ...current, music_timeline: planOf(current) });
    void state.startJob("next-scene", { nextScene: options });
  }
  async function useNextScene(candidateId: string, adjustment: NextSceneAdjustment) {
    if (!nextFresh || working) return;
    const slotId = nextResult?.scope.next_slot_id;
    if (await state.applyNextScene(candidateId, adjustment)) {
      setNextAudition(null);
      if (slotId) setSelectedId(slotId);
      seek(adjustment.cut_time);
    }
  }
  function findScenes(slotIds?: string[], suggestOnly = false) {
    if (working) return;
    state.change((current) =>
      current.music_timeline
        ? keepCutPlan(current)
        : { ...current, music_timeline: planOf(current) },
    );
    requestedShot.current = slotIds?.[0] ?? null;
    void state.startJob("draft", { slotIds, suggestOnly });
  }
  function fillGaps() {
    if (working || !document?.track || !plan || !empty) return;
    // Freeze the displayed arrangement even when an old project has no timeline.
    state.change((current) => current.music_timeline ? current : { ...current, music_timeline: planOf(current) });
    requestedShot.current = null;
    void state.startJob("generate", { generate: { mode: "fill" } });
  }
  async function generateFromDirection() {
    if (working || !document?.track || hasPlacedLocks) return;
    state.endChange();
    setNextAudition(null);
    requestedShot.current = null;
    const job = await state.startJob("generate", { generate: { mode: "regenerate" } });
    if (job) requestedGeneration.current = job.id;
  }
  async function browseHistory(open: boolean) {
    if (!state.project || state.isDraft) return;
    setShowHistory(open);
    if (!open) return;
    setHistoryLoading(true);
    try {
      const result = await labRequest<{
        revisions: { revision: number; name: string }[];
      }>(`/projects/${state.project.id}/revisions`);
      setHistory(result.revisions);
      setHistoryLoading(false);
    } catch (error) {
      state.fail(error);
      setHistoryLoading(false);
    }
  }
  async function openPassage() {
    if (working) return;
    state.endChange();
    const draft = state.captureDraft();
    if (draft) {
      passageSnapshot.current = { revision: 0, undo: state.captureUndo(), draft };
      setPassageOpen(true);
      return;
    }
    const saved = await state.save();
    if (!saved) return;
    passageSnapshot.current = {
      revision: saved.revision,
      undo: state.captureUndo(),
      draft: null,
    };
    setPassageOpen(true);
  }
  async function cancelPassage() {
    if (working) return;
    const snapshot = passageSnapshot.current;
    if (snapshot?.draft && state.isDraft) {
      if (!state.restoreDraft(snapshot.draft)) return;
    } else if (snapshot && snapshot.revision > 0 && state.project?.revision !== snapshot.revision) {
      const restored = await state.restore(snapshot.revision, snapshot.undo);
      if (!restored) return;
    }
    passageSnapshot.current = null;
    setPassageOpen(false);
  }
  function applyPassage(range: { start: number; end: number }, fade: number) {
    if (working) return;
    const needsTiming = !document?.rhythm ||
      Math.abs(document.passage.start - range.start) > 0.001 ||
      Math.abs(document.passage.end - range.end) > 0.001;
    edit((current) => changeMusicPassage(current, range, fade));
    passageSnapshot.current = null;
    setPassageOpen(false);
    if (needsTiming) void state.startJob("rhythm");
  }
  function downloadProject() {
    if (!state.project || !document) return;
    const url = URL.createObjectURL(
      new Blob(
        [
          JSON.stringify(
            { ...state.project, name: state.name, document },
            null,
            2,
          ),
        ],
        { type: "application/json" },
      ),
    );
    const anchor = window.document.createElement("a");
    anchor.href = url;
    anchor.download = `${state.name || "music-edit"}.json`;
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  if (!document || !state.project)
    return (
      <main className={styles.workspace}>
        <LabWorkspaceHeader title={EXPERIMENT_NAME} className={styles.workspaceHeader} />
        <section className={styles.welcome}>
          {state.error ? <>
            <p role="alert">{state.error}</p>
            <button className={styles.primary} disabled={state.busy}
              onClick={() => void (new URLSearchParams(window.location.search).has("project") ? state.reload() : state.create("Untitled music edit"))}>Try again</button>
          </> : <p role="status">Opening your workspace…</p>}
        </section>
      </main>
    );

  return (
    <main className={styles.workspace}>
      <LabWorkspaceHeader title={EXPERIMENT_NAME} className={styles.workspaceHeader} project={{ ...state, project: state.project, activeJob: !!state.activeJob }} tools={<>
        <button disabled={working || !state.canUndo} onClick={state.undo} title="Undo edit (Ctrl / ⌘ Z)">
          <EditorIcon name="undo" /> Undo
        </button>
        {!state.isDraft && <EditorPopover title="Saved revisions" label="History" disabled={!!working} width={340}
          open={showHistory} onOpenChange={(open) => void browseHistory(open)}>
          {(close) => <div className={styles.history}>
            {historyLoading ? <p role="status">Loading revisions…</p> : history.length === 0 ? <p>No earlier revisions.</p> : history.map((item) => (
              <button key={item.revision} disabled={working || item.revision === state.project?.revision}
                onClick={() => { close(); void state.restore(item.revision); }}>
                Revision {item.revision} · {item.name}{item.revision === state.project?.revision ? " · current" : ""}
              </button>
            ))}
          </div>}
        </EditorPopover>}
      </>} trailingTools={document.track && <>
        {!state.isDraft && slots.length > 0 && <a className={styles.exportButton} aria-disabled={state.dirty || undefined}
          href={state.dirty ? undefined : `${LAB_API}/lab/projects/${state.project.id}/timeline.otio`} download
          title={state.dirty ? "Save first: the timeline exports the saved revision"
            : "OpenTimelineIO timeline on the original films, for finishing in DaVinci Resolve"}>Resolve timeline</a>}
        <button className={styles.exportButton}
          disabled={working || !slots.length || empty > 0}
          onClick={() => { requestedExport.current = true; void state.startJob("render", { mode: "export" }); }}>Export video</button>
      </>} />
      <div className={styles.viewTabs} role="tablist" aria-label="Music video workspace"
        onKeyDown={(event) => {
          if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          const view = event.key === "Home" ? "ai" : event.key === "End" ? "edit" : activeView === "ai" ? "edit" : "ai";
          switchView(view);
          window.document.getElementById(`music-tab-${view}`)?.focus();
        }}>
        {(["ai", "edit"] as const).map((view) => <button key={view} type="button"
          id={`music-tab-${view}`} role="tab" aria-selected={activeView === view}
          aria-controls={`music-panel-${view}`} tabIndex={activeView === view ? 0 : -1}
          onClick={() => switchView(view)}>{view === "ai" ? "AI direction" : "Edit"}</button>)}
      </div>
      {!document.track ? <LabEmptyState title="Start with a song" description="Choose a section of music, then build and refine your edit.">
        <button disabled={working} onClick={() => void openPassage()}><EditorIcon name="music" /> Choose a song</button>
        {state.error && <p className={styles.error} role="alert">{state.error}</p>}
      </LabEmptyState> : <>
      <div className={styles.mediaToolbar}>
        <div className={styles.songTools}>
          <button className={styles.trackButton} disabled={working} onClick={() => void openPassage()}>
            <span><EditorIcon name="music" size={20} /></span>
            <div><strong>{document.track?.name ?? "Choose music"}</strong><small>{document.track
              ? `${seconds(document.passage.start)} – ${seconds(document.passage.end)} · Change section`
              : "Choose a song and section"}</small></div>
          </button>
          {document.track && <EditorPopover title="Music analysis" label="Music analysis" width={400} align="start">
            {(close) => <MusicAnalysisDetails document={document} working={!!working}
              onAnalyze={() => { close(); void state.startJob("analyze"); }} />}
          </EditorPopover>}
        </div>
        <label className={styles.outputFormat}>Format
          <select aria-label="Video format" value={document.aspect_ratio} disabled={!!working}
            onChange={(event) => edit((current) => ({ ...current, aspect_ratio: event.target.value as LabDocument["aspect_ratio"] }))}>
            <option value="16:9">Landscape · 16:9</option>
            <option value="9:16">Portrait · 9:16</option>
          </select>
        </label>
      </div>
      {state.job ? <JobStatus compact job={state.job} savedProject={state.project} onCancel={() => void state.cancel()}
        notice={state.notice} actions={renderUrl && !state.activeJob
          ? <button onClick={() => setShowExport(true)}>Latest export <DirectionIcon name="arrow-up-right" /></button> : undefined} />
        : state.notice ? <div className={styles.statusLine} role="status">{state.notice}</div> : null}
      {(localError || (state.error && (state.conflict || state.error !== state.job?.error))) && (
        <div className={styles.error} role="alert">
          {localError || state.error}
          {state.conflict && (
            <div>
              <button onClick={downloadProject}>Download current edits</button>
              <button onClick={() => void state.reload()}>
                Load saved revision
              </button>
            </div>
          )}
        </div>
      )}
      <section id="music-panel-ai" role="tabpanel" aria-labelledby="music-tab-ai"
        className={styles.viewPanel} hidden={activeView !== "ai"} inert={activeView !== "ai"}>
        <MusicDirectionPanel document={document} disabled={!!working}
          active={activeView === "ai" && !passageOpen && !showExport && !footageReview}
          playhead={playhead} onSeek={setPlayhead} onChange={edit} onEndChange={state.endChange}
          waveform={{ peaks, status: waveformStatus }}
          onGenerate={() => void generateFromDirection()} hasEdit={hasEdit}
          generationProblem={hasPlacedLocks ? "Unlock the clips on the timeline to regenerate all scenes and cuts." : undefined} />
      </section>
      <div className={`${styles.editor} ${styles.viewPanel}`} id="music-panel-edit" role="tabpanel"
        aria-labelledby="music-tab-edit" hidden={activeView !== "edit"} inert={activeView !== "edit"}>
        <div className={styles.workbench}>
          <section className={styles.monitor} aria-label="Sequence preview">
            {document.track && plan && !visibleAudition ? (
              <MusicSequencePlayer
                ref={sequencePlayer}
                document={document}
                slots={slots}
                playhead={playhead}
                onTimeChange={setPlayhead}
                filmTitles={filmTitles}
                onPlayingChange={setSequencePlaying}
                compactTransport
                onShowExport={renderUrl ? () => setShowExport(true) : undefined}
                suspended={
                  activeView !== "edit" || passageOpen || showExport || !!footageReview || dialogueAudition
                }
              />
            ) : visibleAudition ? (
              <NextSceneAudition
                key={auditionVersion}
                selection={visibleAudition}
                outputRatio={document.aspect_ratio === "9:16" ? 9 / 16 : 16 / 9}
                latestJob={state.job}
                busy={!!working}
                suspended={activeView !== "edit" || passageOpen || showExport || !!footageReview || dialogueAudition}
                onClose={() => setNextAudition(null)}
                onTimeChange={setAuditionTime}
                onCutChange={setAuditionCut}
                onPrepare={state.prepareNextScene}
                onApply={(id, adjustment) => void useNextScene(id, adjustment)}
                onCancel={() => void state.cancel()}
              />
            ) : (
              <div className={styles.noMusic}>
                <h2>Choose your music</h2>
                <button onClick={() => void openPassage()}>
                  Import a track
                </button>
              </div>
            )}
          </section>
          <aside className={styles.inspector} aria-label="Scenes">
            <div className={styles.inspectorTitle}>
              <div><strong>{libraryView === "next" ? "Following scene" : "Scenes"}</strong><span>{selected
                ? `CLIP ${String(selectedIndex + 1).padStart(2, "0")} · ${seconds(selected.start - document.passage.start)} – ${seconds(selected.end - document.passage.start)}`
                : "Your film library"}</span></div>
              {libraryView === "next" ? <button onClick={() => { setLibraryView("search"); setNextAudition(null); }}><EditorIcon name="back" /> Scenes</button>
                : clip && selected && <button disabled={!!working} onClick={() => setFootageReview({ clip, slotId: selected.id })}>
                  <EditorIcon name="film" /> {clip.locked ? "View clip" : "Adjust clip"}
                </button>}
              {selected && clip && <EditorPopover title="Clip options" label={<EditorIcon name="more" />} triggerLabel="Clip options" width={280}>
                {(close) => <div className={styles.clipOptions}>
                  <button disabled={!!working} onClick={() => { close(); useDialogue(clip, selected.search_evidence ?? null); }}>Use dialogue</button>
                  <button disabled={!!working} onClick={() => { close(); edit((current) => ({ ...current,
                    music_timeline: planOf(current), clips: current.clips.map((item) => item.id === clip.id ? { ...item, locked: !item.locked } : item),
                  })); }}><EditorIcon name="lock" /> {clip.locked ? "Unlock clip" : "Lock clip"}</button>
                  {nextSlot && <button disabled={!!working || !!nextPair.problem} title={nextPair.problem ?? undefined}
                    onClick={() => { close(); setLibraryView("next"); }}><EditorIcon name="next" /> Find following scene</button>}
                  <button disabled={!!working || clip.locked} onClick={() => { close(); edit((current) => clearSlot(current, selected.id)); }}>
                    <EditorIcon name="trash" /> Remove scene
                  </button>
                </div>}
              </EditorPopover>}
            </div>
            <div className={styles.inspectorBody}>
              {selected && direction && libraryView === "search" ? <SceneLibraryPanel
                key={selected.id} slot={selected} currentClip={clip} query={direction.query}
                direction={direction}
                previous={selectedIndex > 0 ? { slot: slots[selectedIndex - 1], clip: document.clips.find((item) => item.id === slots[selectedIndex - 1].clip_id) ?? null } : null}
                next={nextSlot ? { slot: nextSlot, clip: nextClip ?? null } : null}
                films={films} disabled={!!working} searchProblem={searchProblem}
                savedClips={savedClips}
                onClearSaved={() => edit((current) => ({ ...current, clips: current.clips.filter((item) => item.locked || planOf(current)?.slots.some((slot) => slot.clip_id === item.id)) }))}
                searching={!!state.activeJob && state.job?.kind === "draft" && requestedShot.current === selected.id}
                onQuery={(query) => edit((current) => editSlotDirection(current, selected.id, { query }))}
                onSearch={() => findScenes([selected.id], true)} onPlan={() => planDirections([selected.id])}
                searchOptions={<MusicSearchDetails embedded direction={direction} resolved={selected.resolved_search ?? null}
                  evidence={selected.search_evidence ?? null} capabilities={capabilities} clips={document.clips} films={films}
                  disabled={!!working || !!clip?.locked}
                  onFacet={(search_facet) => edit((current) => editSlotDirection(current, selected.id, { search_facet }))}
                  onPlan={(searchPlan) => edit((current) => editSlotSearchPlan(current, selected.id, searchPlan))} />}
                onPreview={(source, evidence) => setFootageReview({ clip: source, slotId: selected.id, evidence })}
                onChoose={choose} onDialogue={useDialogue} onDragScene={dragScene} onDropScene={dropScene}
              /> : selected && libraryView === "next" ? <NextScenePanel
                key={`${nextPair.anchorSlot?.id}-${nextPair.nextSlot?.id}`}
                pair={nextPair} job={state.nextSceneJob} latestJob={state.job} fresh={nextFresh}
                disabled={!!working} films={films} passageStart={document.passage.start} onFind={findNextScene}
                onAudition={(selection) => { if (!nextFresh || working) return;
                  setAuditionTime(selection.scope.t0); setAuditionCut(selection.candidate.cut);
                  setAuditionVersion((value) => value + 1); setNextAudition(selection);
                }} onStopAudition={() => setNextAudition(null)} />
                : <div className={styles.inspectorEmpty}><p>Choose your music to start. Select any timeline clip to find or replace its scene.</p></div>}
            </div>
          </aside>
        </div>
        {plan && (
          <MusicEditTimeline
            document={document}
            plan={plan}
            filmTitles={filmTitles}
            peaks={peaks}
            waveformStatus={waveformStatus}
            playhead={visibleAudition ? auditionTime : playhead}
            playing={activeView === "edit" && sequencePlaying && !visibleAudition}
            selectedId={selectedId}
            selectedDialogueId={selectedDialogueId}
            onSelectDialogue={setSelectedDialogueId}
            onRemoveDialogue={(id) => edit((current) => ({ ...current, dialogue_clips: (current.dialogue_clips ?? []).filter((item) => item.id !== id) }))}
            onMoveDialogue={(id, start) => edit((current) => updateDialogue(current, id, { start }))}
            disabled={working || !!visibleAudition || activeView !== "edit"}
            snap={snap}
            onSnap={setSnap}
            canAddCut={canAddCut}
            onAddCut={addCut}
            onSplitAt={splitAt}
            onRemoveCut={removeCut}
            onRemoveScene={removeScene}
            onFillGaps={fillGaps}
            canFillGaps={empty > 0}
            canReplan={!!document.track && !hasPlacedLocks}
            onDetectBeats={() => void state.startJob("rhythm")}
            canSetEnd={!!selected && !!nextSlot && !clip?.locked && !nextClip?.locked && playhead >= selected.start + 0.25 && playhead <= nextSlot.end - 0.25}
            onSetEnd={() => edit((current) => moveCut(current, selectedIndex + 1, playhead, durationMap))}
            canJoinNext={!!selected && !!nextSlot && !clip?.locked && !nextClip?.locked}
            onJoinNext={() => selected && edit((current) => joinWithNext(current, selected.id, durationMap))}
            onReplan={() =>
              void state.startJob("analyze", { replanTiming: true })
            }
            draggedSceneDuration={draggedSceneDuration}
            previewPair={visibleAudition ? {
              anchorSlotId: visibleAudition.scope.anchor_slot_id,
              nextSlotId: visibleAudition.scope.next_slot_id,
              cut: auditionCut,
            } : null}
            onSelect={(id) => {
              setSelectedId(id);
              setLibraryView("search");
              setLocalError("");
            }}
            onSeek={seek}
            onCut={(index, time) =>
              edit((current) => moveCut(current, index, time, durationMap))
            }
          />
        )}
        {document.track && plan && <DialogueEditor document={document} selectedId={selectedDialogueId} onSelect={setSelectedDialogueId}
          filmTitles={filmTitles} durations={durationMap} disabled={!!working || !!visibleAudition}
          suspended={activeView !== "edit" || passageOpen || showExport || !!footageReview || !!visibleAudition}
          canAddSelected={!!clip} onAddSelected={() => { if (clip) useDialogue(clip, selected?.search_evidence ?? null); }}
          onChange={edit} onEndChange={state.endChange}
          onAuditionChange={(value) => { if (value) sequencePlayer.current?.pause(); setDialogueAudition(value); }} />}
        {document.track && !plan && (
          <p className={styles.error}>
            This older edit is longer than the chosen passage. Its clips are
            preserved. Extend the passage or use History before creating a new
            arrangement.
          </p>
        )}
      </div>

      </>}

      <dialog
        ref={passageDialog}
        className={styles.passageDialog}
        onCancel={(event) => {
          event.preventDefault();
          void cancelPassage();
        }}
        aria-label="Choose song section"
      >
        {passageOpen && (
          <SongPassagePicker
            document={document}
            upload={state.upload}
            disabled={working}
            onApply={applyPassage}
            onClose={() => void cancelPassage()}
          />
        )}
        {state.error && (
          <p className={styles.error} role="alert">
            {state.error}
          </p>
        )}
      </dialog>
      <dialog
        ref={exportDialog}
        className={styles.exportDialog}
        onCancel={() => setShowExport(false)}
        aria-label="Rendered music video"
      >
        <header>
          <strong>Rendered music video</strong>
          <button onClick={() => setShowExport(false)}>Close</button>
        </header>
        {renderUrl && (
          <>
            <video
              src={showExport ? renderUrl : undefined}
              controls
              playsInline
            />
            <div>
              <span>
                {state.dirty ||
                  state.render?.base_revision !== state.project.revision
                  ? "This export is from an earlier revision. Export again to include current edits."
                  : "Ready to download"}
              </span>
              <a
                className={styles.primary}
                href={`${renderUrl}?download=true`}
                download
              >
                Download MP4
              </a>
            </div>
          </>
        )}
      </dialog>
      {footageReview && (
        <SourceWindowReview
          clip={footageReview.clip}
          outputRatio={document.aspect_ratio === "9:16" ? 9 / 16 : 16 / 9}
          filmDuration={films[footageReview.clip.film_id]?.duration}
          disabled={!!working}
          onClose={() => setFootageReview(null)}
          onApply={reviewedClip?.locked || !reviewedSlot ||
            footageReview.clip.source_end - footageReview.clip.source_start < reviewedSlot.end - reviewedSlot.start - 1e-6
            ? undefined : applyReviewedSource}
        />
      )}
    </main>
  );
}
