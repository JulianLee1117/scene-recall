"use client";

import { useEffect, useRef, useState } from "react";
import type { LabDialogueClip } from "@/types/lab";
import { createDialogueTransport } from "./dialogueTransport";
import { releaseDialogueAudio } from "./dialogueGain";
import EditorIcon from "@/features/lab/EditorIcon";
import styles from "./dialogue.module.css";

/** Audition just the source voice, with the sequence paused. */
export default function DialoguePreview({ clip, disabled, suspended, onPlayingChange }: {
  clip: LabDialogueClip; disabled: boolean; suspended: boolean;
  onPlayingChange: (value: boolean) => void;
}) {
  const audio = useRef<HTMLAudioElement>(null);
  const command = useRef<{ toggle: () => void; stop: () => void } | null>(null);
  const latest = useRef(onPlayingChange); latest.current = onPlayingChange;
  const [playing, setPlaying] = useState(false), [loading, setLoading] = useState(false), [error, setError] = useState("");
  useEffect(() => {
    const media = audio.current;
    if (!media) return;
    const voice = createDialogueTransport(media, [clip]);
    let wanted = false, starting = false, hasStarted = false;
    function stop() {
      wanted = false; voice.pause(); setPlaying(false); setLoading(false); latest.current(false);
    }
    command.current = { stop, toggle() {
      if (wanted) { stop(); return; }
      setError(""); voice.retry(); voice.activate(); voice.prepare(clip.start, true);
      wanted = true; hasStarted = false; setPlaying(true); setLoading(true); latest.current(true);
    } };
    const timer = window.setInterval(() => {
      if (!wanted) return;
      if (hasStarted && (media.ended || media.currentTime >= clip.source_end - clip.source_start)) { stop(); return; }
      const time = media.paused ? clip.start : clip.start + media.currentTime;
      const ready = voice.prepare(time);
      if (voice.error) { setError(voice.error); stop(); return; }
      if (!ready || starting || !media.paused) return;
      starting = true;
      void voice.play().then(() => { if (wanted) { hasStarted = true; setLoading(false); } }).catch(() => {
        if (wanted) { setError("The dialogue could not play. Try again or choose another source."); stop(); }
      }).finally(() => { starting = false; });
    }, 16);
    const hide = () => { if (window.document.hidden) stop(); };
    window.document.addEventListener("visibilitychange", hide);
    return () => { stop(); voice.dispose(); window.clearInterval(timer); window.document.removeEventListener("visibilitychange", hide); command.current = null; };
  }, [clip]);
  useEffect(() => { if (suspended || disabled) command.current?.stop(); }, [suspended, disabled]);
  useEffect(() => {
    const media = audio.current;
    return () => { if (media) releaseDialogueAudio(media); };
  }, []);
  return <div className={styles.preview}>
    <audio ref={audio} crossOrigin="anonymous" preload="auto" aria-label="Source dialogue audition" />
    <button type="button" disabled={disabled || suspended} onClick={() => command.current?.toggle()}>
      <EditorIcon name={playing ? "pause" : "play"} size={13} /> {loading ? "Loading voice…" : playing ? "Stop voice" : "Listen to voice"}
    </button>
    {error && <span role="alert" className={styles.error}>{error}</span>}
  </div>;
}
