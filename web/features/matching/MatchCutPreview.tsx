"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import DirectionIcon from "@/components/DirectionIcon";
import { MATCH_API, primaryMatchCue } from "@/lib/matching";
import type { SceneMatch } from "@/types/matching";
import styles from "./matchSearch.module.css";

export default function MatchCutPreview({ candidate, searchId, preparing, onPrepare, onClose }: {
  candidate: SceneMatch;
  searchId: string;
  preparing: boolean;
  onPrepare: (force?: boolean) => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const cue = primaryMatchCue(candidate);
  const [failed, setFailed] = useState(false);
  useEffect(() => { if (candidate.preview_ready) setFailed(false); }, [candidate.id, candidate.preview_ready, candidate.preview_url]);
  useEffect(() => { const element = dialog.current; element?.showModal(); closeButton.current?.focus(); return () => element?.close(); }, []);
  function close() {
    // Release the native modal's inert background before the parent restores trigger focus.
    dialog.current?.close();
    onClose();
  }
  return createPortal(<dialog className={styles.previewDialog} ref={dialog} aria-label={`Preview cut to ${candidate.film_title}`} onCancel={(event) => { event.preventDefault(); close(); }} onClick={(event) => { if (event.target === event.currentTarget) close(); }}>
    <header><div><span aria-label="A to B">A <DirectionIcon name="arrow-right" size={12} /> B</span><h2>{candidate.film_title}</h2></div><button ref={closeButton} onClick={close} aria-label="Close cut preview">×</button></header>
    {candidate.preview_ready && !failed ? <video key={candidate.preview_url ?? candidate.id} src={`${MATCH_API}${candidate.preview_url ?? `/matching/searches/${searchId}/candidates/${candidate.id}/preview`}`} controls autoPlay loop muted playsInline aria-label="Actual A to B cut" onError={() => setFailed(true)} /> : <div className={styles.previewWaiting}>{candidate.frame_url && <img src={`${MATCH_API}${candidate.frame_url}`} alt={`Incoming frame from ${candidate.film_title}`} />}<div role="status">{preparing ? "Preparing the cut…" : <><p>{failed ? "This preview could not be loaded." : "This cut is ready to prepare."}</p><button className={styles.primary} onClick={() => onPrepare(failed)}>{failed ? "Retry preview" : "Prepare preview"}</button></>}</div></div>}
    <p>{cue?.description ?? candidate.evidence}</p>
    {candidate.preview_warning && <p className={styles.warning}>{candidate.preview_warning}</p>}
  </dialog>, document.body);
}
