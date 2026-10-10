"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import AppBar from "@/components/AppBar";
import chrome from "@/components/pageChrome.module.css";
import { labRequest, mediaUrl } from "@/lib/lab";
import type { LabExperiment, LabProjectDeletion, LabProjectSummary } from "@/types/lab";
import { DeleteProjectButton } from "./kit/ProjectActions";
import styles from "./labHome.module.css";

const cleanupNotice = "Project deleted. Some generated files are still awaiting cleanup. Your original music and films are kept.";
// The only lab that saves projects; its edits get the gallery and New edit.
const EDITOR_EXPERIMENT = "music-sketch";

const updatedTime = (project: LabProjectSummary) =>
  typeof project.updated_at === "number"
    ? project.updated_at * 1000
    : new Date(project.updated_at).getTime();

/** The edit's first shots as a contact sheet. */
function ProjectSheet({ units }: { units: string[] }) {
  return (
    <span className={`${styles.sheet}${units.length === 1 ? ` ${styles.sheetSingle}` : ""}`} aria-hidden="true">
      {units.map((unitId) => (
        // eslint-disable-next-line @next/next/no-img-element
        <img key={unitId} src={mediaUrl(`/media/keyframe/${unitId}/0`)} alt="" loading="lazy"
          onError={(event) => { event.currentTarget.style.visibility = "hidden"; }} />
      ))}
    </span>
  );
}

export default function LabHome() {
  // Each list is null until it lands, and lands on its own: the experiments
  // are a few hundred bytes and never wait for the saved edits.
  const [projects, setProjects] = useState<LabProjectSummary[] | null>(null);
  const [experiments, setExperiments] = useState<LabExperiment[] | null>(null);
  const [projectError, setProjectError] = useState("");
  const [experimentError, setExperimentError] = useState("");
  const [deletionNotice, setDeletionNotice] = useState("");
  const [filter, setFilter] = useState("");
  const [requestVersion, setRequestVersion] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    if (new URLSearchParams(window.location.search).get("cleanup") === "pending") setDeletionNotice(cleanupNotice);
    setProjectError("");
    setExperimentError("");
    labRequest<{ projects: LabProjectSummary[] }>("/projects", { signal: controller.signal }).then(
      (result) => {
        if (!controller.signal.aborted) setProjects([...result.projects].sort((a, b) => updatedTime(b) - updatedTime(a)));
      },
      (reason) => { if (!controller.signal.aborted) setProjectError(reason?.message || "Could not load projects."); },
    );
    labRequest<{ experiments: LabExperiment[] }>("/experiments", { signal: controller.signal }).then(
      (result) => {
        if (controller.signal.aborted) return;
        const frozen = (experiment: LabExperiment) => (experiment.status === "frozen" ? 1 : 0);
        setExperiments([...result.experiments].sort((a, b) => frozen(a) - frozen(b)));
      },
      (reason) => { if (!controller.signal.aborted) setExperimentError(reason?.message || "Could not load experiments."); },
    );
    return () => controller.abort();
  }, [requestVersion]);

  const editor = experiments?.find((experiment) => experiment.id === EDITOR_EXPERIMENT);
  const savedCount = projects?.length ?? 0;
  const query = filter.trim().toLowerCase();
  const visibleProjects = (projects ?? []).filter((project) =>
    !query || `${project.name} ${project.track_name ?? ""}`.toLowerCase().includes(query));
  const retry = <button type="button" onClick={() => setRequestVersion((value) => value + 1)}>Try again</button>;

  const deleteProject = async (project: LabProjectSummary): Promise<boolean> => {
    const result = await labRequest<LabProjectDeletion>(
      `/projects/${project.id}?base_revision=${project.revision}`,
      { method: "DELETE" },
    );
    try { window.localStorage.removeItem(`lab-job:${project.id}`); }
    catch { /* Browser storage cannot undo a successful server deletion. */ }
    setProjects((current) => (current ?? []).filter((item) => item.id !== project.id));
    setDeletionNotice(result.cleanup_pending ? cleanupNotice : "");
    return true;
  };

  return <>
    <AppBar active="lab" />
    <main className={`${chrome.page} ${styles.home}`}>
      <header className={chrome.header}>
        <span className={chrome.eyebrow}>LAB</span>
        <h1 className={chrome.title}>Experiments</h1>
        <p className={chrome.description}>Try one, or pick up an edit where you left it.</p>
      </header>

      <section aria-label="Experiments">
        {experimentError && <p className={`${chrome.error} ${styles.error}`} role="alert">{experimentError}{retry}</p>}
        {!experiments ? (!experimentError && <p className={styles.empty} role="status">Loading experiments…</p>)
          : !experiments.length ? <p className={styles.empty}>No experiments are available yet.</p>
          : <ul className={styles.experiments}>
            {experiments.map((experiment) => (
              <li key={experiment.id}>
                <Link
                  className={`${styles.experiment}${experiment.status === "frozen" ? ` ${styles.frozen}` : ""}`}
                  href={experiment.route || `/lab/${experiment.id}`}
                  aria-label={`Open ${experiment.name}`}
                >
                  <h3>
                    {experiment.name}
                    {experiment.status === "frozen" && (
                      <span className={styles.frozenTag} title="Still runnable, but no new work is planned">Frozen</span>
                    )}
                  </h3>
                  <p>{experiment.description}</p>
                  <span className={styles.open} aria-hidden="true">
                    {experiment.id === EDITOR_EXPERIMENT && savedCount > 0
                      ? `${savedCount} saved edit${savedCount === 1 ? "" : "s"}`
                      : "Open"}
                  </span>
                </Link>
              </li>
            ))}
          </ul>}
      </section>

      <section className={styles.projects} aria-labelledby="projects-heading" id="edits">
        <div className={styles.sectionHeading}>
          <h2 id="projects-heading">
            Saved edits
            {editor && <span className={styles.headingNote}>from {editor.name}</span>}
          </h2>
          <div className={styles.projectTools}>
            {savedCount > 8 && (
              <input
                className={styles.filter}
                type="search"
                value={filter}
                placeholder="Find an edit"
                aria-label="Find an edit by name or song"
                onChange={(event) => setFilter(event.target.value)}
              />
            )}
            {editor && (
              <Link className={styles.newEdit} href={editor.route || `/lab/${editor.id}`}>
                New edit
              </Link>
            )}
          </div>
        </div>
        {deletionNotice && <p className={styles.notice} role="status">{deletionNotice}</p>}
        {projectError && <p className={`${chrome.error} ${styles.error}`} role="alert">{projectError}{retry}</p>}
        {!projects ? (!projectError && <p className={styles.empty} role="status">Loading edits…</p>)
          : !projects.length ? <p className={styles.empty}>Edits you save in AI Music Video appear here.</p>
          : !visibleProjects.length ? <p className={styles.empty}>No edit matches “{filter.trim()}”.</p>
          : <ul className={styles.projectGrid}>
            {visibleProjects.map((project) => {
              const experiment = experiments?.find((item) => item.id === project.experiment_id);
              const route = experiment?.project_route || experiment?.route || `/lab/${project.experiment_id}`;
              const song = project.track_name?.replace(/\.(wav|mp3|m4a|flac|aac|ogg|aiff?)$/i, "");
              const updated = new Date(updatedTime(project));
              return (
                <li key={project.id} className={styles.projectCard}>
                  <Link href={`${route}?project=${encodeURIComponent(project.id)}`} className={styles.project}>
                    {/* An API started before this listing existed sends no summary yet. */}
                    <ProjectSheet units={project.sheet_unit_ids ?? []} />
                    <strong>{project.name}</strong>
                    {song && <span className={styles.projectSong} title={song}>{song}</span>}
                    <span className={styles.projectMeta}>
                      {project.experiment_id !== EDITOR_EXPERIMENT && <span>{experiment?.name ?? project.experiment_id}</span>}
                      {project.clip_count > 0 && <span>{project.clip_count} clip{project.clip_count === 1 ? "" : "s"}</span>}
                      <time dateTime={updated.toISOString()}>
                        {updated.toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                      </time>
                    </span>
                    {!!project.active_job_count && <span className={styles.busy}>In progress · open to cancel</span>}
                  </Link>
                  <span className={styles.projectDelete}>
                    <DeleteProjectButton
                      name={project.name}
                      disabled={!!project.active_job_count}
                      disabledReason="Open the project and cancel its active job before deleting"
                      onDelete={() => deleteProject(project)}
                    />
                  </span>
                </li>
              );
            })}
          </ul>}
      </section>
    </main>
  </>;
}
