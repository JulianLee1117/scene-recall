"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { LabError, labRequest } from "@/lib/lab";
import { effectiveEditorDirection } from "./editorDirection";
import type {
  ExperimentId,
  LabDocument,
  LabJob,
  LabProject,
  LabProjectDeletion,
  NextSceneAdjustment,
  NextSceneOptions,
} from "@/types/lab";

type StartJobOptions = {
  mode?: "preview" | "export";
  slotIds?: string[];
  replanTiming?: boolean;
  generate?: { mode: "fill" | "improve" | "regenerate" };
  suggestOnly?: boolean;
  nextScene?: NextSceneOptions;
};

export type LabDraftSnapshot = {
  project: LabProject;
  document: LabDocument;
  name: string;
  history: LabDocument[];
};

function musicCompletionNotice(job: LabJob): string {
  if (job.kind === "generate") {
    return typeof job.result?.message === "string" ? job.result.message
      : "Edit ready. Select a clip to find another scene or adjust its moment.";
  }
  if (job.result?.suggest_only === true) {
    return String(
      job.result.message ??
        "Scenes ready. Preview the alternatives and choose a scene to use.",
    );
  }
  switch (job.kind) {
    case "rhythm":
      return String(job.result?.message ?? "Timing ready. Review the guides and placeholders, then generate the edit or adjust the cuts.");
    case "analyze":
      return "Music analyzed. Review each clip’s direction, then find scenes for the gaps.";
    case "plan":
      return "Prompts planned. Review each clip’s intent, then find scenes. Your timing and selected scenes are kept.";
    default:
      return "Suggestions ready. Play the sequence and compare alternatives for any shot.";
  }
}

function appliedMusicRevision(job: LabJob): number | null {
  return job.status === "completed" &&
    ["generate", "rhythm", "analyze", "plan", "draft"].includes(job.kind) &&
    job.result?.applied === true &&
    typeof job.result.revision === "number"
    ? job.result.revision
    : null;
}

export function useLabProject(experiment: ExperimentId) {
  const [project, setProject] = useState<LabProject | null>(null);
  const [document, setDocument] = useState<LabDocument | null>(null);
  const [history, setHistory] = useState<LabDocument[]>([]);
  const [name, setNameState] = useState("");
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const [job, setJob] = useState<LabJob | null>(null);
  const [render, setRender] = useState<LabJob | null>(null);
  const [nextSceneJob, setNextSceneJob] = useState<LabJob | null>(null);
  const nextSceneAction = useRef(false);
  const pendingSave = useRef<Promise<LabProject | null> | null>(null);
  const lifecycle = useRef(0);
  const [loadVersion, setLoadVersion] = useState(0);
  const documentRef = useRef(document);
  const historyRef = useRef<LabDocument[]>([]);
  const changeGroup = useRef<string | null>(null);
  const endChange = useCallback(() => { changeGroup.current = null; }, []);
  const replaceHistory = useCallback((items: LabDocument[]) => {
    historyRef.current = items;
    setHistory(items);
  }, []);
  const nameRef = useRef(name);
  const setName = useCallback((value: string) => {
    endChange();
    nameRef.current = value;
    setNameState(value);
  }, [endChange]);
  const projectRef = useRef(project);
  const dirty = useMemo(() =>
    !!project &&
    (name !== project.name ||
      JSON.stringify(document) !== JSON.stringify(project.document)),
    [document, name, project]);
  const isDraft = project?.revision === 0;

  const accept = useCallback((next: LabProject, undoable = false) => {
    endChange();
    const previous = documentRef.current;
    if (undoable && previous && projectRef.current?.id === next.id)
      replaceHistory([...historyRef.current.slice(-49), previous]);
    else replaceHistory([]);
    projectRef.current = next;
    documentRef.current = next.document;
    nameRef.current = next.name;
    setProject(next);
    setDocument(next.document);
    setName(next.name);
    setConflict(false);
  }, [endChange, replaceHistory, setName]);
  const fail = useCallback((reason: unknown) => {
    setError(
      reason instanceof Error
        ? reason.message
        : "The request could not be completed.",
    );
    if (reason instanceof LabError && reason.status === 409) setConflict(true);
  }, []);

  useEffect(() => {
    endChange();
    lifecycle.current += 1;
    const controller = new AbortController();
    const id = new URLSearchParams(window.location.search).get("project");
    setBusy(true);
    labRequest<LabProject>(id ? `/projects/${encodeURIComponent(id)}` : `/experiments/${experiment}/draft`, {
      signal: controller.signal,
    })
      .then((next) => {
        if (controller.signal.aborted) return;
        if (next.experiment_id !== experiment)
          throw new Error(
            "This project belongs to a different experiment. Open it from the Lab.",
          );
        if (!id && (next.id !== "" || next.revision !== 0))
          throw new Error("The experiment did not return an unsaved workspace.");
        accept(next);
        if (!id) return;
        void labRequest<{ jobs: LabJob[] }>(`/projects/${next.id}/jobs`, {
          signal: controller.signal,
        })
          .then(({ jobs }) => {
            if (controller.signal.aborted || projectRef.current?.id !== next.id) return;
            const active = jobs.find(
              (item) => item.status === "queued" || item.status === "running",
            );
            const latest = jobs[0];
            const revision = latest ? appliedMusicRevision(latest) : null;
            const recent =
              revision === null || revision === next.revision
                ? latest
                : undefined;
            const recovered = active ?? recent;
            if (recovered) setJob(recovered);
            const latestRender = jobs.find(
              (item) => item.kind === "render" && item.status === "completed",
            );
            if (latestRender) setRender(latestRender);
            setNextSceneJob(
              jobs.find((item) => item.kind === "next-scene" &&
                item.base_revision === next.revision &&
                ["queued", "running", "completed"].includes(item.status)) ?? null,
            );
          })
          .catch(() => {});
      })
      .catch((reason) => {
        if (!controller.signal.aborted) fail(reason);
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false);
      });
    return () => {
      endChange();
      controller.abort();
      lifecycle.current += 1;
    };
  }, [accept, endChange, experiment, fail, loadVersion]);

  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const create = async (_title: string) => {
    endChange();
    // Opening/retrying a workspace only reads its template or saved project.
    // Creation is reserved for the first save of actual edits.
    if (projectRef.current) return;
    setError("");
    setLoadVersion((version) => version + 1);
  };
  const change = (update: (current: LabDocument) => LabDocument, group?: string) => {
    if (!group || group !== changeGroup.current) endChange();
    const current = documentRef.current;
    if (!current) return;
    const next = update(current);
    if (next === current) return;
    setNotice("");
    // One focused field or drag can issue many edits while remaining one Undo.
    // Refs advance synchronously so another event need not wait for a render.
    if (!group || group !== changeGroup.current)
      replaceHistory([...historyRef.current.slice(-49), current]);
    changeGroup.current = group ?? null;
    documentRef.current = next;
    setDocument(next);
  };
  const undo = () => {
    endChange();
    const previous = historyRef.current.at(-1);
    if (previous) {
      setNotice("");
      documentRef.current = previous;
      setDocument(previous);
      replaceHistory(historyRef.current.slice(0, -1));
    }
  };
  const save = (): Promise<LabProject | null> => {
    endChange();
    if (pendingSave.current) return pendingSave.current;
    const currentProject = projectRef.current;
    const snapshot = documentRef.current;
    const savedName = nameRef.current;
    if (!currentProject || !snapshot) return Promise.resolve(null);
    if (
      savedName === currentProject.name &&
      JSON.stringify(snapshot) === JSON.stringify(currentProject.document)
    )
      return Promise.resolve(currentProject.revision === 0 ? null : currentProject);
    setBusy(true);
    setError("");
    const firstSave = currentProject.revision === 0;
    const savingLifecycle = lifecycle.current;
    const saving = (async () => {
    try {
      const next = await labRequest<LabProject>(
        firstSave ? "/projects" : `/projects/${currentProject.id}`,
        {
          method: firstSave ? "POST" : "PUT",
          body: JSON.stringify({
            ...(firstSave ? { experiment_id: experiment } : { base_revision: currentProject.revision }),
            document: snapshot,
            name: savedName,
          }),
        },
      );
      if (lifecycle.current !== savingLifecycle || projectRef.current !== currentProject) return null;
      projectRef.current = next;
      setProject(next);
      if (firstSave) {
        const query = new URLSearchParams(window.location.search);
        query.set("project", next.id);
        window.history.replaceState(null, "", `?${query.toString()}`);
      }
      setConflict(false);
      setNotice("Changes saved.");
      if (documentRef.current === snapshot) {
        documentRef.current = next.document;
        setDocument(next.document);
      }
      if (nameRef.current === savedName) setName(next.name);
      return next;
    } catch (reason) {
      if (lifecycle.current === savingLifecycle) fail(reason);
      return null;
    } finally {
      pendingSave.current = null;
      if (lifecycle.current === savingLifecycle) setBusy(false);
    }
    })();
    pendingSave.current = saving;
    return saving;
  };
  const reload = async () => {
    endChange();
    if (!project?.id) return;
    setBusy(true);
    setError("");
    try {
      accept(await labRequest<LabProject>(`/projects/${project.id}`));
      setNotice("Loaded the saved revision.");
    } catch (reason) {
      fail(reason);
    } finally {
      setBusy(false);
    }
  };
  const saveForExit = async (): Promise<boolean> => {
    endChange();
    const current = projectRef.current;
    if (current?.revision === 0 && nameRef.current === current.name &&
        JSON.stringify(documentRef.current) === JSON.stringify(current.document)) return true;
    const saved = await save();
    if (!saved) return false;
    const unchanged =
      projectRef.current?.id === saved.id &&
      nameRef.current === saved.name &&
      JSON.stringify(documentRef.current) === JSON.stringify(saved.document);
    if (!unchanged)
      setError("New changes were made while saving. Save again before exiting.");
    return unchanged;
  };
  const remove = async (): Promise<LabProjectDeletion | false> => {
    endChange();
    const current = projectRef.current;
    if (!current?.id || busy || job?.status === "queued" || job?.status === "running")
      return false;
    setBusy(true);
    setError("");
    try {
      const result = await labRequest<LabProjectDeletion>(
        `/projects/${current.id}?base_revision=${current.revision}`,
        { method: "DELETE" },
      );
      try { window.localStorage.removeItem(`lab-job:${current.id}`); }
      catch { /* Browser storage cannot undo a successful server deletion. */ }
      projectRef.current = null;
      documentRef.current = null;
      nameRef.current = "";
      setProject(null);
      setDocument(null);
      setName("");
      replaceHistory([]);
      setJob(null);
      setRender(null);
      setNextSceneJob(null);
      setNotice("");
      setConflict(false);
      return result;
    } catch (reason) {
      fail(reason);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const upload = async (file: File) => {
    endChange();
    const draft = projectRef.current;
    if (draft?.revision === 0) {
      const importLifecycle = lifecycle.current;
      const before = documentRef.current;
      if (!before || pendingSave.current) return;
      setBusy(true);
      setError("");
      const form = new FormData();
      form.set("file", file);
      try {
        const track = await labRequest<NonNullable<LabDocument["track"]>>("/tracks", { method: "POST", body: form });
        if (lifecycle.current !== importLifecycle) return;
        if (projectRef.current !== draft || documentRef.current !== before) {
          setError("The workspace changed during import. Your current edits are kept; choose the song again.");
          return;
        }
        const end = Math.min(30, track.duration);
        change((current) => ({ ...current, track, passage: { start: 0, end },
          clips: [], dialogue_clips: [], effects: [], analysis: null, rhythm: null, music_timeline: null, direction_plan: null,
          song_context: null, visual_plan: null,
          editor_direction: { ...effectiveEditorDirection(current), ranges: [] },
          audio_fade_in_seconds: Math.min(current.audio_fade_in_seconds ?? 0, end),
          audio_fade_out_seconds: Math.min(current.audio_fade_out_seconds ?? 0, end) }));
        setNotice("Track imported. Choose the passage you want to work with.");
      } catch (reason) {
        if (lifecycle.current === importLifecycle) fail(reason);
      } finally {
        if (lifecycle.current === importLifecycle) setBusy(false);
      }
      return;
    }
    const saved = await save();
    if (!saved) return;
    setBusy(true);
    setError("");
    const form = new FormData();
    form.set("file", file);
    form.set("base_revision", String(saved.revision));
    try {
      accept(
        await labRequest<LabProject>(`/projects/${saved.id}/track`, {
          method: "POST",
          body: form,
        }),
        true,
      );
      setNotice("Track imported. Choose the passage you want to work with.");
    } catch (reason) {
      fail(reason);
    } finally {
      setBusy(false);
    }
  };
  const startJob = async (
    kind: LabJob["kind"],
    {
      slotIds,
      replanTiming = false,
      generate,
      mode,
      suggestOnly = false,
      nextScene,
    }: StartJobOptions = {},
  ) => {
    endChange();
    const saved = await save();
    if (!saved) return null;
    if (projectRef.current?.id !== saved.id || nameRef.current !== saved.name ||
        JSON.stringify(documentRef.current) !== JSON.stringify(saved.document)) {
      setError("New changes were made while saving. Start the operation again to use your current edit.");
      return null;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const next = await labRequest<LabJob>(`/projects/${saved.id}/jobs`, {
        method: "POST",
        body: JSON.stringify({
          kind,
          ...(mode ? { mode } : {}),
          base_revision: saved.revision,
          ...(slotIds ? { slot_ids: slotIds } : {}),
          ...(replanTiming ? { replan_timing: true } : {}),
          ...(generate ? { generate } : {}),
          ...(suggestOnly ? { suggest_only: true } : {}),
          ...(nextScene ? { next_scene: nextScene } : {}),
        }),
      });
      setJob(next);
      if (kind === "next-scene") setNextSceneJob(next);
      window.localStorage.setItem(`lab-job:${saved.id}`, next.id);
      return next;
    } catch (reason) {
      fail(reason);
      return null;
    } finally {
      setBusy(false);
    }
  };

  const activeJob = job?.status === "queued" || job?.status === "running";
  useEffect(() => {
    if (!job) return;
    let stopped = false;
    let connectionFailed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await labRequest<LabJob>(`/jobs/${job.id}`);
        if (stopped) return;
        if (connectionFailed) {
          setError("");
          connectionFailed = false;
        }
        if (next.status === "queued" || next.status === "running") {
          setJob(next);
          timer = setTimeout(poll, 2000);
          return;
        }
        window.localStorage.removeItem(`lab-job:${next.project_id}`);
        if (next.status === "completed") {
          if (next.kind === "next-scene") {
            setNextSceneJob(next);
            setNotice("Next-scene alternatives are ready. Play them with your music before choosing.");
          } else if (next.kind === "next-scene-preview") {
            setNotice("Adjusted preview ready. Play it, then use the scene when it works.");
          } else if (next.kind === "render") {
            setRender(next);
            setNotice("Preview and export are ready.");
          } else {
            const saved = await labRequest<LabProject>(
              `/projects/${next.project_id}`,
            );
            if (stopped) return;
            const current = projectRef.current;
            const appliedRevision = appliedMusicRevision(next);
            if (
              appliedRevision !== null &&
              (saved.revision > appliedRevision ||
                (current?.revision ?? 0) > appliedRevision)
            ) {
              // A later save (including a saved Undo) supersedes this result.
              // It is neither a fresh success nor an unapplied-job conflict.
              setJob(next);
              return;
            }
            if (
              next.result?.applied !== false &&
              current &&
              current.revision === saved.revision &&
              JSON.stringify(documentRef.current) ===
                JSON.stringify(saved.document) &&
              nameRef.current === saved.name
            ) {
              setNotice(musicCompletionNotice(next));
            } else if (
              next.result?.applied !== false &&
              current &&
              current.revision === next.base_revision &&
              JSON.stringify(documentRef.current) ===
                JSON.stringify(current.document) &&
              nameRef.current === current.name
            ) {
              accept(saved, true);
              setNotice(musicCompletionNotice(next));
            } else {
              setNotice(
                "The job finished against an earlier revision. Your current edits are preserved; saved revisions remain available.",
              );
              if (current && saved.revision !== current.revision) {
                setConflict(true);
                setError(
                  "A newer revision is saved. Download your current edits before loading it, or keep working locally.",
                );
              }
            }
          }
        } else if (next.error) setError(next.error);
        else if (next.status === "cancelled")
          setNotice(
            `The ${next.kind} job was cancelled. Your saved project is available for editing.`,
          );
        else if (next.status === "interrupted")
          setError(
            `The ${next.kind} job was interrupted. Review the saved project before explicitly starting it again.`,
          );
        else if (next.status === "failed")
          setError(
            `The ${next.kind} job failed. Your saved project is preserved.`,
          );
        // Keep editor actions locked until a completed music job's saved
        // revision is accepted. Opening the passage picker earlier snapshots
        // the old revision and Cancel could then roll back the completed job.
        setJob(next);
      } catch (reason) {
        if (!stopped) {
          connectionFailed = true;
          fail(reason);
          timer = setTimeout(poll, 6000);
        }
      }
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
    // Track the job identity, not each progress update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id, accept, fail]);

  const cancel = async () => {
    endChange();
    if (!job) return;
    try {
      const next = await labRequest<LabJob>(`/jobs/${job.id}/cancel`, {
        method: "POST",
      });
      // Completion can win a cancellation race. Let polling accept its project
      // revision before marking that result available to the editor.
      if (
        next.status !== "completed" ||
        (next.kind !== "rhythm" &&
          next.kind !== "analyze" &&
          next.kind !== "plan" &&
          next.kind !== "draft" &&
          next.kind !== "generate")
      )
        setJob(next);
    } catch (reason) {
      fail(reason);
    }
  };
  const restore = async (revision: number, undoHistory?: LabDocument[]) => {
    endChange();
    if (!projectRef.current?.id || revision < 1) return false;
    const saved = await save();
    if (!saved) return false;
    setBusy(true);
    setError("");
    try {
      accept(
        await labRequest<LabProject>(`/projects/${saved.id}/restore`, {
          method: "POST",
          body: JSON.stringify({ base_revision: saved.revision, revision }),
        }),
      );
      if (undoHistory) replaceHistory([...undoHistory]);
      setNotice(`Restored revision ${revision} as a new revision.`);
      return true;
    } catch (reason) {
      fail(reason);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const captureDraft = (): LabDraftSnapshot | null => {
    const current = projectRef.current;
    const value = documentRef.current;
    return current?.revision === 0 && value
      ? { project: current, document: value, name: nameRef.current, history: [...historyRef.current] }
      : null;
  };
  const restoreDraft = (snapshot: LabDraftSnapshot): boolean => {
    endChange();
    if (projectRef.current !== snapshot.project || snapshot.project.revision !== 0 || pendingSave.current) return false;
    documentRef.current = snapshot.document;
    nameRef.current = snapshot.name;
    setDocument(snapshot.document);
    setName(snapshot.name);
    replaceHistory([...snapshot.history]);
    setNotice("");
    setError("");
    return true;
  };

  function nextSceneSnapshot() {
    const current = projectRef.current;
    const snapshot = documentRef.current;
    if (!current || !snapshot || nextSceneJob?.status !== "completed" ||
      nextSceneJob.project_id !== current.id || nextSceneJob.base_revision !== current.revision ||
      nameRef.current !== current.name || JSON.stringify(snapshot) !== JSON.stringify(current.document)) {
      setError("Your edit changed. Find next-scene alternatives again before previewing or applying them.");
      return null;
    }
    return { project: current, document: snapshot, name: nameRef.current, job: nextSceneJob };
  }

  const prepareNextScene = async (candidateId: string, adjustment: NextSceneAdjustment) => {
    endChange();
    if (busy || activeJob || nextSceneAction.current) return null;
    const snapshot = nextSceneSnapshot();
    if (!snapshot) return null;
    nextSceneAction.current = true;
    setBusy(true);
    setError("");
    try {
      const next = await labRequest<LabJob>(
        `/jobs/${snapshot.job.id}/next-scenes/${encodeURIComponent(candidateId)}/preview`,
        { method: "POST", body: JSON.stringify({ source_start: adjustment.source_start, cut_time: adjustment.cut_time, crop: adjustment.crop }) },
      );
      setJob(next);
      setNotice("");
      window.localStorage.setItem(`lab-job:${snapshot.project.id}`, next.id);
      return next;
    } catch (reason) {
      fail(reason);
      return null;
    } finally {
      nextSceneAction.current = false;
      setBusy(false);
    }
  };

  const applyNextScene = async (candidateId: string, adjustment: NextSceneAdjustment) => {
    endChange();
    if (busy || activeJob || nextSceneAction.current) return false;
    const snapshot = nextSceneSnapshot();
    if (!snapshot) return false;
    nextSceneAction.current = true;
    setBusy(true);
    setError("");
    try {
      const next = await labRequest<LabProject>(`/jobs/${snapshot.job.id}/apply-next-scene`, {
        method: "POST",
        body: JSON.stringify({
          base_revision: snapshot.project.revision, candidate_id: candidateId, ...adjustment,
        }),
      });
      if (projectRef.current?.id !== snapshot.project.id) return false;
      if (projectRef.current.revision !== snapshot.project.revision ||
        documentRef.current !== snapshot.document || nameRef.current !== snapshot.name) {
        setConflict(true);
        setError("The scene was saved, but newer local changes were made while applying. Your local edits are preserved; download them before loading the saved revision.");
        return false;
      }
      accept(next, true);
      setNotice("Scene applied. Undo restores the previous scene and timing.");
      return true;
    } catch (reason) {
      fail(reason);
      return false;
    } finally {
      nextSceneAction.current = false;
      setBusy(false);
    }
  };
  return {
    project,
    document,
    name,
    setName,
    busy,
    dirty,
    isDraft,
    error,
    notice,
    conflict,
    job,
    activeJob,
    render,
    nextSceneJob,
    prepareNextScene,
    applyNextScene,
    create,
    change,
    endChange,
    undo,
    canUndo: history.length > 0,
    captureUndo: () => [...historyRef.current],
    captureDraft,
    restoreDraft,
    save,
    saveForExit,
    remove,
    reload,
    upload,
    startJob,
    cancel,
    restore,
    fail,
  };
}
