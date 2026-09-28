"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import EditorIcon from "./EditorIcon";
import ProjectActions, { useProjectExit, type ProjectActionsProps, type ProjectExitState } from "./ProjectActions";
import styles from "./labWorkspaceHeader.module.css";

type ProjectHeaderState = ProjectActionsProps & ProjectExitState & {
  setName: (name: string) => void;
};

export default function LabWorkspaceHeader({ title, project, tools, trailingTools, className = "" }: {
  title: string;
  project?: ProjectHeaderState;
  tools?: ReactNode;
  trailingTools?: ReactNode;
  className?: string;
}) {
  const { onExit, dialog } = useProjectExit(project);
  return <>
    <header className={`${styles.header} ${className}`}>
      <nav className={styles.navigation} aria-label="Lab navigation">
        {project ? <button type="button" className={styles.back} disabled={project.busy} onClick={onExit}
          title={project.activeJob ? "Return to Labs. Your job will continue in the background." : "Return to Labs"} aria-label="Back to Labs">
          <EditorIcon name="back" size={14} /> Labs
        </button> : <Link href="/lab" className={styles.back} aria-label="Back to Labs"><EditorIcon name="back" size={14} /> Labs</Link>}
        <span className={styles.divider} aria-hidden="true" />
        <h1>{title}</h1>
      </nav>
      {project && <div className={styles.projectRow}>
        <div className={styles.identity}>
          <input aria-label="Project name" value={project.name} maxLength={120} disabled={project.busy || project.activeJob}
            onChange={(event) => project.setName(event.target.value)} />
          <span className={styles.status}>{project.dirty ? "Unsaved changes" : project.project.revision === 0 ? "Not saved" : "Saved"}</span>
        </div>
        <div className={styles.tools}>{tools}<ProjectActions {...project} compact />{trailingTools}</div>
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
