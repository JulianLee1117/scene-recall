"use client";

import { useEffect, useRef, useState } from "react";
import MovieScopeFilter from "@/components/MovieScopeFilter";
import DirectionIcon from "@/components/DirectionIcon";
import {
  clipFromResult,
  experimentName,
  labRequest,
  mediaUrl,
} from "@/lib/lab";
import type { LabClip } from "@/types/lab";
import { useLabProject } from "./useLabProject";
import SourceBrowser from "./SourceBrowser";
import MatchFinder from "./MatchFinder";
import JobStatus from "./JobStatus";
import LabWorkspaceHeader from "./LabWorkspaceHeader";
import styles from "./lab.module.css";
import flow from "./workflow.module.css";

export default function LabEditor() {
  const state = useLabProject("visual-rhymes");
  const { project, document, busy, dirty, activeJob } = state;
  const [browser, setBrowser] = useState<{ replaceId?: string } | null>(null);
  const [revisions, setRevisions] = useState<Array<{
    revision: number;
    name: string;
  }> | null>(null);
  const [showOutput, setShowOutput] = useState(false);
  const completed = useRef<string | null>(null);
  const clips = document?.clips ?? [];
  const renderUrl =
    state.render?.result?.output_url ??
    (state.render ? `/lab/jobs/${state.render.id}/output` : null);
  const working = busy || activeJob;
  const label = experimentName("visual-rhymes");

  useEffect(() => {
    if (state.job?.status !== "completed" || state.job.id === completed.current)
      return;
    completed.current = state.job.id;
    if (state.job.kind === "render") setShowOutput(true);
  }, [
    state.job?.id,
    state.job?.status,
    state.job?.kind,
  ]);

  function patchClip(id: string, patch: Partial<LabClip>) {
    state.change((current) => ({
      ...current,
      clips: current.clips.map((clip) => {
        if (clip.id !== id || clip.locked) return clip;
        const next = { ...clip, ...patch };
        if (
          patch.source_start !== undefined ||
          patch.source_end !== undefined
        ) {
          if (next.reference_time != null)
            next.reference_time = Math.max(
              next.source_start,
              Math.min(next.source_end - 0.001, next.reference_time),
            );
        }
        if (next.window_start != null && next.window_end != null) {
          const reference = next.reference_time ?? next.source_start;
          if (
            next.window_start < next.source_start ||
            next.window_end > next.source_end ||
            reference < next.window_start ||
            reference >= next.window_end
          ) {
            const windowDuration = Math.min(
              4,
              next.window_end - next.window_start,
              next.source_end - next.source_start,
            );
            next.window_start = Math.max(
              next.source_start,
              Math.min(
                next.source_end - windowDuration,
                reference - windowDuration / 2,
              ),
            );
            next.window_end = next.window_start + windowDuration;
          }
        }
        return next;
      }),
    }));
  }
  async function loadRevisions() {
    if (!project || state.isDraft) return;
    if (revisions) {
      setRevisions(null);
      return;
    }
    try {
      setRevisions(
        (
          await labRequest<{
            revisions: Array<{ revision: number; name: string }>;
          }>(`/projects/${project.id}/revisions`)
        ).revisions,
      );
    } catch (reason) {
      state.fail(reason);
    }
  }
  function downloadProject() {
    if (!project || !document) return;
    const blob = new Blob(
      [JSON.stringify({ ...project, name: state.name, document }, null, 2)],
      { type: "application/json" },
    );
    const url = URL.createObjectURL(blob),
      link = window.document.createElement("a");
    link.href = url;
    link.download = `${state.name || "scene-recall"}.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  const scope = document && (
    <div className={flow.scope}>
      <span>Films to use</span>
      <MovieScopeFilter
        selectedFilmIds={document.film_ids}
        onChange={(filmIds) => {
          if (!working)
            state.change((current) => ({ ...current, film_ids: filmIds }));
        }}
      />
    </div>
  );
  const outputSettings = document && (
    <details className={flow.more}>
      <summary>Output settings</summary>
      <div className={flow.settingsRow}>
        <label>
          Shape
          <select
            disabled={working}
            value={document.aspect_ratio}
            onChange={(event) =>
              state.change((current) => ({
                ...current,
                aspect_ratio: event.target.value as "16:9" | "9:16",
              }))
            }
          >
            <option value="16:9">Landscape · 16:9</option>
            <option value="9:16">Portrait · 9:16</option>
          </select>
        </label>
        <button className={styles.ghost} onClick={downloadProject}>
          Download editable project
        </button>
      </div>
    </details>
  );
  return (
    <main>
      <LabWorkspaceHeader title={label} project={project ? { ...state, project, activeJob: !!activeJob } : undefined} tools={<>
        <button disabled={!state.canUndo || working} onClick={state.undo}>Undo</button>
        {!state.isDraft && <button disabled={working} onClick={() => void loadRevisions()}>History</button>}
      </>} trailingTools={project && <>
        <button disabled={working || clips.length !== 2} onClick={() => { setShowOutput(false); void state.startJob("render"); }}>Export</button>
        {renderUrl && !dirty && state.render?.base_revision === project.revision && <a className={styles.secondary} href={mediaUrl(`${renderUrl}?download=true`)} download>Download MP4 <DirectionIcon name="arrow-down" /></a>}
      </>} />
      <div className={`${styles.editor} ${flow.editor}`}>
      {!project || !document ? (
        <section className={flow.stepBody} aria-label="Opening Match Cuts">
          <h1>Match Cuts</h1>
          {state.error ? <>
            <p className={styles.error} role="alert">{state.error}</p>
            <button className={styles.secondary} disabled={busy} onClick={() => void (new URLSearchParams(window.location.search).has("project") ? state.reload() : state.create("Untitled Match Cuts"))}>Try again</button>
          </> : <p className={styles.hint} role="status">Opening your workspace…</p>}
        </section>
      ) : (
        <>
          {state.error && (
            <div className={styles.error} role="alert">
              {state.error}
              {state.conflict && (
                <div className={styles.row}>
                  <button onClick={downloadProject}>
                    Download current edits
                  </button>
                  <button
                    disabled={working}
                    onClick={() => void state.reload()}
                  >
                    Load saved version
                  </button>
                </div>
              )}
            </div>
          )}
          {revisions && (
            <section className={styles.history}>
              <div className={styles.sectionHeading}>
                <h2>Saved versions</h2>
                <button
                  className={styles.ghost}
                  onClick={() => setRevisions(null)}
                >
                  Close
                </button>
              </div>
              {revisions.map((revision) => (
                <div className={styles.historyRow} key={revision.revision}>
                  <span>
                    Revision {revision.revision} · {revision.name}
                  </span>
                  <button
                    disabled={working || revision.revision === project.revision}
                    onClick={() => {
                      void state.restore(revision.revision);
                      setRevisions(null);
                    }}
                  >
                    Restore
                  </button>
                </div>
              ))}
            </section>
          )}
          {activeJob && state.job && (
            <JobStatus job={state.job} onCancel={() => void state.cancel()} />
          )}
          {!activeJob && state.notice && (
            <p className={flow.notice} role="status">
              {state.notice}
            </p>
          )}
          <MatchFinder
            clip={clips[0] ?? null}
            incoming={clips[1] ?? null}
            busy={working}
            job={state.job?.kind === "match" ? state.job : state.matchJob}
            revision={project.revision}
            dirty={dirty}
            canApply={!clips[1]?.locked}
            applyBlockedReason={
              clips[1]?.locked
                ? "Scene B is locked. Unlock it before keeping a new cut."
                : undefined
            }
            onFind={(options) => void state.startJob("match", { match: options })}
            onApply={async (id, previewJobId) => {
              const applied = await state.applyMatch(id, previewJobId);
              if (applied) setShowOutput(false);
              return applied;
            }}
            renderUrl={showOutput ? renderUrl : null}
            onChange={(patch) => { if (clips[0]) patchClip(clips[0].id, patch); }}
            onUnlock={() => state.change((current) => ({ ...current, clips: current.clips.map((item, index) => index === 1 ? { ...item, locked: false } : item) }))}
            onBrowse={() =>
              setBrowser(clips[0] ? { replaceId: clips[0].id } : {})
            }
            onExample={(clip) => {
              if (clips[0]?.locked) return;
              state.change((current) => ({
                ...current,
                clips: [clip, ...current.clips.slice(1)],
              }));
            }}
            scope={<>
              {scope}
              {outputSettings}
              <div className={styles.row}>{clips.map((clip, index) => <button key={clip.id} className={styles.ghost} disabled={working} aria-pressed={clip.locked} onClick={() => state.change((current) => ({ ...current, clips: current.clips.map((item) => item.id === clip.id ? { ...item, locked: !item.locked } : item) }))}>{clip.locked ? "Unlock" : "Lock"} scene {index === 0 ? "A" : "B"}</button>)}</div>
            </>}
          />
          {browser && (
            <SourceBrowser
              filmIds={document.film_ids}
              replacing={!!browser.replaceId}
              onClose={() => setBrowser(null)}
              onSelect={(result) => {
                const replacement = clips.find(
                  (clip) => clip.id === browser.replaceId,
                );
                const clip = clipFromResult(
                  result,
                  replacement
                    ? replacement.source_end - replacement.source_start
                    : 3,
                  true,
                );
                state.change((current) => ({
                  ...current,
                  clips: browser.replaceId
                    ? current.clips.map((item) =>
                        item.id === browser.replaceId ? clip : item,
                      )
                    : [...current.clips, clip],
                }));
                setBrowser(null);
                setShowOutput(false);
              }}
            />
          )}
        </>
      )}
      </div>
    </main>
  );
}
