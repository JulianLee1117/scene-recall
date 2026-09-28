"use client";

import { useEffect, useRef, type ReactNode } from "react";
import MatchIcon from "./MatchIcon";
import styles from "./matchSearch.module.css";

export default function MatchReferenceEditor({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const trigger = document.activeElement as HTMLElement | null;
    const element = dialog.current;
    element?.showModal();
    return () => { element?.close(); trigger?.focus(); };
  }, []);
  function close() { dialog.current?.close(); onClose(); }
  return <dialog ref={dialog} className={styles.referenceDialog} aria-label="Adjust reference" onCancel={(event) => { event.preventDefault(); close(); }} onClick={(event) => { if (event.target === event.currentTarget) close(); }}>
    <header><h2>Reference</h2><button onClick={close} className={styles.iconButton} aria-label="Close reference editor"><MatchIcon name="close" /></button></header>
    <div className={styles.referencePlayer}>{children}</div>
    <footer><button className={styles.secondary} onClick={close}>Done</button></footer>
  </dialog>;
}
