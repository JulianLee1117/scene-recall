"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { LabProject, LabProjectDeletion } from "@/types/lab";
import EditorIcon from "./EditorIcon";
import EditorPopover from "./EditorPopover";
import styles from "./projectActions.module.css";

function ProjectDialog({
  title,
  busy,
  onClose,
  children,
  returnFocus,
}: {
  title: string;
  busy: boolean;
  onClose: () => void;
  children: ReactNode;
  returnFocus?: HTMLElement | null;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => {
      element?.close();
      if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    };
  }, [returnFocus]);
  return createPortal(
    <dialog
      ref={dialog}
      className={styles.dialog}
      aria-labelledby={titleId}
      aria-busy={busy}
      onKeyDown={(event) => event.stopPropagation()}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <h2 id={titleId}>{title}</h2>
      {children}
    </dialog>,
    window.document.body,
  );
}

export function DeleteProjectButton({
  name,
  disabled = false,
  disabledReason,
  error,
  onDelete,
  onOpen,
}: {
  name: string;
  disabled?: boolean;
  disabledReason?: string;
  error?: string;
  onDelete: () => Promise<boolean>;
  onOpen?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState("");
  const returnFocus = useRef<HTMLElement | null>(null);
  const confirm = async () => {
    if (pending || disabled) return;
    setPending(true);
    setFailure("");
    try {
      if (await onDelete()) setOpen(false);
    } catch (reason) {
      setFailure(reason instanceof Error ? reason.message : "Could not delete this project.");
    } finally {
      setPending(false);
    }
  };
  return (
    <>
      <button
        type="button"
        className={styles.delete}
        disabled={disabled || pending}
        aria-label={`Delete ${name}`}
        title={disabled ? disabledReason : "Delete project"}
        onClick={(event) => {
          setFailure("");
          setOpen(true);
          onOpen?.();
          returnFocus.current = event.currentTarget.ownerDocument.activeElement as HTMLElement;
        }}
      >
        <EditorIcon name="trash" /> Delete
      </button>
      {open && (
        <ProjectDialog title={`Delete “${name}”?`} busy={pending} returnFocus={returnFocus.current} onClose={() => setOpen(false)}>
          <p>This deletes the project, saved versions, job history and receipts, and generated previews and exports. This cannot be undone.</p>
          <p className={styles.detail}>Your original music and film library are kept.</p>
          {(failure || error) && <p className={styles.error} role="alert">{failure || error}</p>}
          <div className={styles.dialogActions}>
            <button type="button" autoFocus disabled={pending} onClick={() => setOpen(false)}>Cancel</button>
            <button type="button" className={styles.confirmDelete} disabled={pending || disabled} onClick={() => void confirm()}>
              <EditorIcon name="trash" /> {pending ? "Deleting…" : "Delete project"}
            </button>
          </div>
        </ProjectDialog>
      )}
    </>
  );
}

export type ProjectExitState = {
  name: string;
  dirty: boolean;
  busy: boolean;
  activeJob: boolean;
  saveForExit: () => Promise<boolean>;
  error?: string;
};

export function useProjectExit(state?: ProjectExitState) {
  const router = useRouter();
  // Where a workspace with unsaved edits is leaving for, while it asks.
  const [leaving, setLeaving] = useState<string | null>(null);
  const [savingExit, setSavingExit] = useState(false);
  const returnFocus = useRef<HTMLElement | null>(null);
  const working = !!state && (state.busy || state.activeJob);
  const exit = () => router.push(leaving ?? "/lab");
  const saveAndExit = async () => {
    if (!state || savingExit || working) return;
    setSavingExit(true);
    try {
      if (await state.saveForExit()) exit();
    } finally {
      setSavingExit(false);
    }
  };
  return {
    /** Leave for `destination` (Labs by default), after Save/Discard/Cancel when there are unsaved edits. */
    onExit: (destination = "/lab", event?: { currentTarget: HTMLElement }) => {
      if (state?.busy) return;
      if (state?.dirty) { returnFocus.current = event?.currentTarget ?? null; setLeaving(destination); }
      else router.push(destination);
    },
    dialog: leaving !== null && state ? (
      <ProjectDialog title="Save before leaving?" busy={savingExit} returnFocus={returnFocus.current} onClose={() => setLeaving(null)}>
        <p>“{state.name}” has unsaved changes.</p>
        {state.activeJob && <p className={styles.detail}>Your job will continue in the background. Wait for it to finish to save, or discard your unsaved changes and exit.</p>}
        {state.error && <p className={styles.error} role="alert">{state.error}</p>}
        <div className={styles.dialogActions}>
          <button type="button" disabled={savingExit} onClick={() => setLeaving(null)}>Cancel</button>
          <button type="button" disabled={savingExit} onClick={exit}>Discard and exit</button>
          <button type="button" className={styles.save} autoFocus disabled={working || savingExit} onClick={() => void saveAndExit()}>
            <EditorIcon name="save" /> {savingExit ? "Saving…" : "Save and exit"}
          </button>
        </div>
      </ProjectDialog>
    ) : null,
  };
}

export type ProjectActionsProps = {
  project: LabProject;
  dirty: boolean;
  busy: boolean;
  activeJob: boolean;
  save: () => Promise<LabProject | null>;
  remove: () => Promise<LabProjectDeletion | false>;
  error?: string;
  compact?: boolean;
};

export default function ProjectActions({ project, dirty, busy, activeJob, save, remove, error, compact = true }: ProjectActionsProps) {
  const router = useRouter();
  const working = busy || activeJob;
  const deleteButton = (onOpen?: () => void) => <DeleteProjectButton
    name={project.name}
    disabled={working}
    disabledReason={activeJob ? "Cancel the active job before deleting this project" : "Wait for the current operation to finish"}
    error={error}
    onOpen={onOpen}
    onDelete={async () => {
      const deleted = await remove();
      if (!deleted) return false;
      router.replace(deleted.cleanup_pending ? "/lab?cleanup=pending" : "/lab");
      return true;
    }}
  />;
  return (
    <div className={`${styles.actions} ${compact ? styles.compact : ""}`} aria-label="Project actions">
      <button type="button" className={styles.save} disabled={!dirty || working} onClick={() => void save()}>
        <EditorIcon name="save" /> Save
      </button>
      {project.revision > 0 && (compact ? <EditorPopover title="Project" label={<>Project <EditorIcon name="more" size={14} /></>}
        triggerLabel="Project menu" triggerClassName={styles.projectMenu} width={260}>
        {(close) => <div className={styles.menuContents}>
          <p className={styles.projectName}>{project.name}</p>
          {deleteButton(close)}
        </div>}
      </EditorPopover> : deleteButton())}
    </div>
  );
}
