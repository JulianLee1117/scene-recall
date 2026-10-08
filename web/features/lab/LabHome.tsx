"use client";

import Link from "next/link";
import DirectionIcon from "@/components/DirectionIcon";
import { useEffect, useState } from "react";
import { experimentName, labRequest, mediaUrl } from "@/lib/lab";
import type { LabExperiment, LabProject, LabProjectDeletion } from "@/types/lab";
import { DeleteProjectButton } from "./ProjectActions";
import styles from "./labHome.module.css";

type ListedProject = LabProject & { active_job_count?: number };
const cleanupNotice = "Project deleted. Some generated files are still awaiting cleanup. Your original music and films are kept.";
// The only lab that saves projects; its edits get the gallery and New edit.
const EDITOR_EXPERIMENT = "music-sketch";

const updatedTime = (project: LabProject) =>
  typeof project.updated_at === "number"
    ? project.updated_at * 1000
    : new Date(project.updated_at).getTime();

/** A small mark per lab so the tiles read at a glance. */
function ExperimentGlyph({ id }: { id: string }) {
  const common = { width: 26, height: 26, viewBox: "0 0 26 26", fill: "none", stroke: "currentColor", strokeWidth: 1.5, strokeLinecap: "round" as const, "aria-hidden": true };
  if (id === "music-sketch") return (
    <svg {...common}><path d="M3 13v0M7 9v8M11 5v16M15 8v10M19 11v4M23 13v0" /></svg>
  );
  if (id === "visual-rhymes") return (
    <svg {...common}><rect x="2.5" y="6" width="12" height="9" rx="1.5" /><rect x="11.5" y="11" width="12" height="9" rx="1.5" /></svg>
  );
  if (id === "transitions") return (
    <svg {...common}><rect x="3" y="6" width="20" height="14" rx="1.5" /><path d="M15 6 9 20" /></svg>
  );
  if (id === "alg-mods") return (
    <svg {...common} fill="currentColor" stroke="none">
      {[5, 10, 15, 20].flatMap((x, column) => [6, 11, 16, 21].map((y, row) => (
        <circle key={`${x}-${y}`} cx={x + (row % 2) * 1.5} cy={y} r={0.9 + ((column + row) % 3) * 0.45} />
      )))}
    </svg>
  );
  return <svg {...common}><circle cx="13" cy="13" r="8" /></svg>;
}

/** Up to four of the edit's shots as a contact sheet. */
function ProjectSheet({ project }: { project: LabProject }) {
  const units = Array.from(new Set((project.document?.clips ?? [])
    .map((clip) => clip.unit_id)
    .filter((unitId): unitId is string => Boolean(unitId) && !String(unitId).startsWith("gen-")))).slice(0, 4);
  return (
    <span className={`${styles.sheet}${units.length === 1 ? ` ${styles.sheetSingle}` : ""}`} aria-hidden="true">
      {units.length === 0 ? <span className={styles.sheetEmpty}><ExperimentGlyph id={project.experiment_id} /></span>
        : units.map((unitId) => (
          // eslint-disable-next-line @next/next/no-img-element
          <img key={unitId} src={mediaUrl(`/media/keyframe/${unitId}/0`)} alt="" loading="lazy"
            onError={(event) => { event.currentTarget.style.visibility = "hidden"; }} />
        ))}
    </span>
  );
}

export default function LabHome() {
  const [projects, setProjects] = useState<ListedProject[]>([]);
  const [experiments, setExperiments] = useState<LabExperiment[]>([]);
  const [projectError, setProjectError] = useState("");
  const [deletionNotice, setDeletionNotice] = useState("");
  const [experimentError, setExperimentError] = useState("");
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [requestVersion, setRequestVersion] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    if (new URLSearchParams(window.location.search).get("cleanup") === "pending") setDeletionNotice(cleanupNotice);
    setLoading(true);
    setProjectError("");
    setExperimentError("");
    Promise.allSettled([
      labRequest<{ projects: ListedProject[] }>("/projects", {
        signal: controller.signal,
      }),
      labRequest<{ experiments: LabExperiment[] }>("/experiments", {
        signal: controller.signal,
      }),
    ]).then(([projectResult, experimentResult]) => {
      if (controller.signal.aborted) return;
      if (projectResult.status === "fulfilled") {
        setProjects(
          [...projectResult.value.projects].sort(
            (a, b) => updatedTime(b) - updatedTime(a),
          ),
        );
      } else {
        setProjectError(
          projectResult.reason?.message || "Could not load projects.",
        );
      }
      if (experimentResult.status === "fulfilled") {
        const frozen = (experiment: LabExperiment) => (experiment.status === "frozen" ? 1 : 0);
        setExperiments(experimentResult.value.experiments
          .map((experiment) => ({ ...experiment, name: experimentName(experiment.id, experiment.name) }))
          .sort((a, b) => frozen(a) - frozen(b)));
      } else {
        setExperimentError(
          experimentResult.reason?.message || "Could not load experiments.",
        );
      }
      setLoading(false);
    });
    return () => controller.abort();
  }, [requestVersion]);

  const editor = experiments.find((experiment) => experiment.id === EDITOR_EXPERIMENT);
  const query = filter.trim().toLowerCase();
  const visibleProjects = query
    ? projects.filter((project) =>
      `${project.name} ${project.document?.track?.name ?? ""}`.toLowerCase().includes(query))
    : projects;
  const deleteProject = async (project: ListedProject): Promise<boolean> => {
    const result = await labRequest<LabProjectDeletion>(
      `/projects/${project.id}?base_revision=${project.revision}`,
      { method: "DELETE" },
    );
    try { window.localStorage.removeItem(`lab-job:${project.id}`); }
    catch { /* Browser storage cannot undo a successful server deletion. */ }
    setProjects((current) => current.filter((item) => item.id !== project.id));
    setDeletionNotice(result.cleanup_pending ? cleanupNotice : "");
    return true;
  };

  return (
    <main className={styles.home}>
      <nav className={styles.nav} aria-label="Breadcrumb">
        <Link href="/">scene recall</Link>
        <span aria-hidden="true">/</span>
        <span aria-current="page">Labs</span>
      </nav>

      <header className={styles.header}>
        <h1>Labs</h1>
        <p>Try an experiment, or pick up an edit where you left it.</p>
      </header>

      <section aria-labelledby="experiments-heading">
        <div className={styles.sectionHeading}>
          <h2 id="experiments-heading">Experiments</h2>
          <span>Work in progress</span>
        </div>
        {experimentError && (
          <p className={styles.error} role="alert">
            {experimentError}
            <button
              type="button"
              onClick={() => setRequestVersion((value) => value + 1)}
            >
              Try again
            </button>
          </p>
        )}
        {loading && !experiments.length ? (
          <p className={styles.empty} role="status">
            Loading experiments…
          </p>
        ) : !experiments.length && !experimentError ? (
          <p className={styles.empty}>No experiments are available yet.</p>
        ) : (
          <ul className={styles.experiments}>
            {experiments.map((experiment) => (
              <li key={experiment.id}>
                <Link
                  className={`${styles.experiment}${experiment.status === "frozen" ? ` ${styles.frozen}` : ""}`}
                  href={experiment.route || `/lab/${experiment.id}`}
                  aria-label={`Open ${experiment.name}`}
                >
                  <span className={styles.glyph}><ExperimentGlyph id={experiment.id} /></span>
                  <h3>
                    {experiment.name}
                    {experiment.status === "frozen" && (
                      <span className={styles.frozenTag} title="Still runnable, but no new work is planned">Frozen</span>
                    )}
                  </h3>
                  <p>{experiment.description}</p>
                  <span className={styles.open} aria-hidden="true">
                    {experiment.id === EDITOR_EXPERIMENT && projects.length > 0
                      ? `${projects.length} saved edit${projects.length === 1 ? "" : "s"}`
                      : "Open"}
                    <DirectionIcon name="arrow-right" />
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className={styles.projects} aria-labelledby="projects-heading" id="edits">
        <div className={styles.sectionHeading}>
          <h2 id="projects-heading">
            Saved edits
            {editor && <span className={styles.headingNote}>from {editor.name}</span>}
          </h2>
          <div className={styles.projectTools}>
            {projects.length > 8 && (
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
        {projectError && (
          <p className={styles.error} role="alert">
            {projectError}
            <button
              type="button"
              onClick={() => setRequestVersion((value) => value + 1)}
            >
              Try again
            </button>
          </p>
        )}
        {loading && !projects.length ? (
          <p className={styles.empty} role="status">
            Loading projects…
          </p>
        ) : !projects.length && !projectError ? (
          <p className={styles.empty}>Edits you save in AI Music Video appear here.</p>
        ) : !visibleProjects.length ? (
          <p className={styles.empty}>No edit matches “{filter.trim()}”.</p>
        ) : (
          <ul className={styles.projectGrid}>
            {visibleProjects.map((project) => {
              const experiment = experiments.find(
                (item) => item.id === project.experiment_id,
              );
              const route = experiment?.project_route || experiment?.route || `/lab/${project.experiment_id}`;
              const clipCount = project.document?.clips?.length ?? 0;
              const song = project.document?.track?.name?.replace(/\.(wav|mp3|m4a|flac|aac|ogg|aiff?)$/i, "");
              const updated = new Date(updatedTime(project));
              return (
                <li key={project.id} className={styles.projectCard}>
                  <Link
                    href={`${route}?project=${encodeURIComponent(project.id)}`}
                    className={styles.project}
                  >
                    <ProjectSheet project={project} />
                    <strong>{project.name}</strong>
                    {song && <span className={styles.projectSong} title={song}>{song}</span>}
                    <span className={styles.projectMeta}>
                      {project.experiment_id !== EDITOR_EXPERIMENT && <span>{experimentName(project.experiment_id, experiment?.name)}</span>}
                      {clipCount > 0 && <span>{clipCount} clip{clipCount === 1 ? "" : "s"}</span>}
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
          </ul>
        )}
      </section>
    </main>
  );
}
