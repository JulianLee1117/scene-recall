"use client";

import Link from "next/link";
import DirectionIcon from "@/components/DirectionIcon";
import { useEffect, useState } from "react";
import { experimentName, labRequest } from "@/lib/lab";
import type { LabExperiment, LabProject, LabProjectDeletion } from "@/types/lab";
import { DeleteProjectButton } from "./ProjectActions";
import styles from "./labHome.module.css";

type ListedProject = LabProject & { active_job_count?: number };
const cleanupNotice = "Project deleted. Some generated files are still awaiting cleanup. Your original music and films are kept.";

const updatedTime = (project: LabProject) =>
  typeof project.updated_at === "number"
    ? project.updated_at * 1000
    : new Date(project.updated_at).getTime();

export default function LabHome() {
  const [projects, setProjects] = useState<ListedProject[]>([]);
  const [experiments, setExperiments] = useState<LabExperiment[]>([]);
  const [projectError, setProjectError] = useState("");
  const [deletionNotice, setDeletionNotice] = useState("");
  const [experimentError, setExperimentError] = useState("");
  const [loading, setLoading] = useState(true);
  const [showAllProjects, setShowAllProjects] = useState(false);
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

  const visibleProjects = showAllProjects ? projects : projects.slice(0, 6);
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
        <p>Open an experiment, or continue a saved project.</p>
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
            {experiments.map((experiment, index) => (
              <li key={experiment.id}>
                <Link
                  className={`${styles.experiment}${experiment.status === "frozen" ? ` ${styles.frozen}` : ""}`}
                  href={experiment.route || `/lab/${experiment.id}`}
                  aria-label={`Open ${experiment.name}`}
                >
                  <span className={styles.number} aria-hidden="true">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  <div className={styles.experimentDescription}>
                    <h3>
                      {experiment.name}
                      {experiment.status === "frozen" && (
                        <span className={styles.frozenTag} title="Still runnable, but no new work is planned">Frozen</span>
                      )}
                    </h3>
                    <p>{experiment.description}</p>
                  </div>
                  <span className={styles.open} aria-hidden="true">
                    Open <DirectionIcon name="arrow-right" />
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className={styles.projects} aria-labelledby="projects-heading">
        <div className={styles.sectionHeading}>
          <h2 id="projects-heading">Recent projects</h2>
          {projects.length > 6 && (
            <button
              className={styles.showAll}
              type="button"
              aria-expanded={showAllProjects}
              onClick={() => setShowAllProjects((value) => !value)}
            >
              {showAllProjects ? "Show recent" : `View all ${projects.length}`}
            </button>
          )}
        </div>
        {deletionNotice && <p className={styles.empty} role="status">{deletionNotice}</p>}
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
          <p className={styles.empty}>Your saved projects will appear here.</p>
        ) : (
          <ul className={styles.projectList}>
            {visibleProjects.map((project) => {
              const experiment = experiments.find(
                (item) => item.id === project.experiment_id,
              );
              const route = experiment?.project_route || experiment?.route || `/lab/${project.experiment_id}`;
              return (
                <li key={project.id} className={styles.projectRow}>
                  <Link
                    href={`${route}?project=${encodeURIComponent(project.id)}`}
                    className={styles.project}
                  >
                    <div>
                      <strong>{project.name}</strong>
                      <span>
                        {experimentName(project.experiment_id, experiment?.name)}
                        {!!project.active_job_count && " · In progress — open to cancel"}
                      </span>
                    </div>
                    <time
                      dateTime={new Date(updatedTime(project)).toISOString()}
                    >
                      {new Date(updatedTime(project)).toLocaleDateString(
                        undefined,
                        {
                          month: "short",
                          day: "numeric",
                        },
                      )}
                    </time>
                    <span className={styles.resume}>
                      Resume <DirectionIcon name="arrow-right" />
                    </span>
                  </Link>
                  <DeleteProjectButton
                    name={project.name}
                    disabled={!!project.active_job_count}
                    disabledReason="Open the project and cancel its active job before deleting"
                    onDelete={() => deleteProject(project)}
                  />
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </main>
  );
}
