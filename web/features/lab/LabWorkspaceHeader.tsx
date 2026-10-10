"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import AppBar from "@/components/AppBar";
import ProjectActions, { useProjectExit, type ProjectActionsProps, type ProjectExitState } from "./ProjectActions";
import styles from "./labWorkspaceHeader.module.css";

type ProjectHeaderState = ProjectActionsProps & ProjectExitState & {
  setName: (name: string) => void;
};

/**
 * The app's chrome over a lab screen: the app bar, then one row with the Labs
 * return, the experiment's title and, for a project editor, the project's
 * name, save state and tools. Render it first in <main>, before any container
 * of your own, so it spans the page like everywhere else; everything below it
 * is the experiment's to lay out. Every way out passes the unsaved-edits guard.
 */
export default function LabWorkspaceHeader({ title, project, tools, trailingTools }: {
  title: string;
  project?: ProjectHeaderState;
  tools?: ReactNode;
  trailingTools?: ReactNode;
}) {
  const { onExit, dialog } = useProjectExit(project);
  return <>
    <AppBar active="lab" onNavigate={project ? (href) => onExit(href) : undefined} />
    <header className={styles.header}>
      <nav className={styles.crumbs} aria-label="Lab navigation">
        {project ? <button type="button" className={styles.labs} disabled={project.busy} onClick={(event) => onExit("/lab", event)}
          title={project.activeJob ? "Return to Labs. Your job will continue in the background." : "Return to Labs"} aria-label="Back to Labs">
          Labs
        </button> : <Link href="/lab" className={styles.labs} aria-label="Back to Labs">Labs</Link>}
        <span className={styles.divider} aria-hidden="true" />
        <h1>{title}</h1>
      </nav>
      {project && <div className={styles.identity}>
        <input aria-label="Project name" value={project.name} maxLength={120} disabled={project.busy || project.activeJob}
          onChange={(event) => project.setName(event.target.value)} />
        <span className={styles.status}>{project.dirty ? "Unsaved changes" : project.project.revision === 0 ? "Not saved" : "Saved"}</span>
      </div>}
      {(project || tools || trailingTools) && <div className={styles.tools}>
        {tools}{project && <ProjectActions {...project} compact />}{trailingTools}
      </div>}
    </header>
    {dialog}
  </>;
}

export function LabEmptyState({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return <section className={styles.empty}>
    <h2>{title}</h2><p>{description}</p>{children}
  </section>;
}
