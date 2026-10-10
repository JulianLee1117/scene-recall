"use client";

import { seconds } from "@/lib/lab";
import type { MusicCue } from "./musicCues";
import EditorPopover from "@/features/lab/EditorPopover";
import styles from "./musicalCues.module.css";

export default function MusicalCues({ cues, start, end, selectedId, onSelect, onClose }: {
  cues: MusicCue[];
  start: number;
  end: number;
  selectedId: string | null;
  onSelect: (cue: MusicCue) => void;
  onClose: (id: string) => void;
}) {
  const duration = Math.max(0.001, end - start);
  return <div className={styles.lane} aria-label="Musical moments and supplied lyric lines">
    {!cues.length && <span className={styles.empty}>Analyze music for audible cues · Add lyrics in Planner settings</span>}
    {cues.map((cue) => <EditorPopover
      key={cue.id}
      title={cue.source === "user" ? "Your lyric / meaning" : "AI audio observation"}
      label={<span>{cue.label}</span>}
      triggerClassName={`${styles.cue} ${cue.source === "user" ? styles.lyric : ""}`}
      triggerStyle={{ left: `${((cue.start - start) / duration) * 100}%`, width: `${((cue.end - cue.start) / duration) * 100}%` }}
      triggerLabel={`${cue.source === "user" ? "Your lyric" : "AI audio cue"}, ${seconds(cue.start - start)}: ${cue.label}`}
      triggerTitle={`${seconds(cue.start - start)}–${seconds(cue.end - start)} · ${cue.source === "user" ? "Your lyric / meaning" : "AI audio observation (approximate)"}\n${cue.label}`}
      open={selectedId === cue.id}
      onOpenChange={(open) => open ? onSelect(cue) : onClose(cue.id)}
      align="start"
    >
      <div className={styles.detail}>
        <div className={styles.detailText}>
          <span className={styles.badge}>
            {seconds(cue.start - start)}–{seconds(cue.end - start)}
            {cue.confidence && ` · ${cue.confidence} model confidence`}
          </span>
          <b>{cue.label}</b>
          <p>{cue.detail}{cue.source === "audio" && " Listen to verify; confidence is the model’s own estimate."}</p>
        </div>
        <button onClick={() => onSelect(cue)}>Seek to cue</button>
      </div>
    </EditorPopover>)}
  </div>;
}
