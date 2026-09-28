"use client";

import type { LabDocument, LabDialogueClip } from "@/types/lab";
import DirectionTimeInput from "./DirectionTimeInput";
import DialoguePreview from "./DialoguePreview";
import { updateDialogue } from "./dialogueAudio";
import styles from "./dialogue.module.css";

export default function DialogueEditor({ document, selectedId, onSelect, filmTitles, durations, disabled, suspended,
  canAddSelected, onAddSelected, onChange, onEndChange, onAuditionChange }: {
  document: LabDocument; selectedId: string | null; filmTitles: Record<string, string>; durations: Record<string, number>;
  onSelect: (id: string) => void;
  disabled: boolean; suspended: boolean; canAddSelected: boolean; onAddSelected: () => void;
  onChange: (update: (value: LabDocument) => LabDocument, group?: string) => void;
  onEndChange: () => void; onAuditionChange: (value: boolean) => void;
}) {
  const clips = document.dialogue_clips ?? [], clip = clips.find((item) => item.id === selectedId);
  const duration = document.passage.end - document.passage.start;
  const patch = (update: Partial<LabDialogueClip>, group: string) => {
    if (clip) onChange((current) => updateDialogue(current, clip.id, update), `dialogue-${clip.id}-${group}`);
  };
  const limit = document.passage.end;
  return <section className={styles.editor} aria-label="Dialogue and audio mix">
    <div className={styles.heading}>
      <div><strong>Dialogue</strong><span>Film voices over your edit</span></div>
      {clips.length > 1 && <select className={styles.voiceSelect} aria-label="Edit dialogue clip" disabled={disabled} value={clip?.id ?? ""} onChange={(event) => onSelect(event.target.value)}>
        <option value="" disabled>Choose a voice</option>
        {clips.map((item, index) => <option key={item.id} value={item.id}>{index + 1}. {item.text || filmTitles[item.film_id] || item.title || "Film voice"}</option>)}
      </select>}
      <button type="button" disabled={disabled || !canAddSelected || clips.length >= 32} onClick={onAddSelected}>Use dialogue from selected scene</button>
      <details className={styles.mix}><summary>Music mix</summary><div className={styles.mixFields}>
        <label>Music volume <span>{document.music_gain_db ?? 0} dB</span><input type="range" min={-60} max={0} step={1} value={document.music_gain_db ?? 0} disabled={disabled}
          onChange={(event) => onChange((current) => ({ ...current, music_gain_db: Number(event.target.value) }), "music-gain")}
          onPointerUp={onEndChange} onBlur={onEndChange} /></label>
        <DirectionTimeInput label="Music fade in (s)" value={document.audio_fade_in_seconds ?? 0} min={0} max={Math.min(90, duration)} disabled={disabled}
          onChange={(value) => onChange((current) => ({ ...current, audio_fade_in_seconds: value }), "music-fade-in")} onEndChange={onEndChange} />
        <DirectionTimeInput label="Music fade out (s)" value={document.audio_fade_out_seconds ?? 0} min={0} max={Math.min(90, duration)} disabled={disabled}
          onChange={(value) => onChange((current) => ({ ...current, audio_fade_out_seconds: value }), "music-fade-out")} onEndChange={onEndChange} />
      </div></details>
    </div>
    {!clip ? <p className={styles.hint}>{clips.length ? "Select a voice in the Dialogue lane to adjust it." : "Choose Use dialogue on any scene, then place and trim its voice independently."}</p> : <div className={styles.controls}>
      <div className={styles.identity}><strong>{filmTitles[clip.film_id] ?? clip.title ?? "Film voice"}</strong>
        <label className={styles.voiceFocus}><input type="checkbox" checked={clip.source_audio_mode === "voice_focus"} disabled={disabled}
          onChange={(event) => { patch({ source_audio_mode: event.target.checked ? "voice_focus" : "original" }, "focus"); onEndChange(); }} /> Voice focus</label>
        <DialoguePreview clip={clip} disabled={disabled} suspended={suspended} onPlayingChange={onAuditionChange} />
        <button type="button" className={styles.remove} disabled={disabled} onClick={() => onChange((current) => ({ ...current, dialogue_clips: (current.dialogue_clips ?? []).filter((item) => item.id !== clip.id) }))}>Remove voice</button>
      </div>
      <div className={styles.fields}>
        <DirectionTimeInput label="Start in edit (s)" value={clip.start - document.passage.start} min={0}
          max={duration - (clip.source_end - clip.source_start)} disabled={disabled} onChange={(value) => patch({ start: value + document.passage.start }, "start")} onEndChange={onEndChange} />
        <DirectionTimeInput label="Source in (s)" value={clip.source_start} min={Math.max(0, clip.source_end - (limit - clip.start))} max={clip.source_end - .01}
          disabled={disabled} onChange={(value) => patch({ source_start: value }, "in")} onEndChange={onEndChange} />
        <DirectionTimeInput label="Source out (s)" value={clip.source_end} min={clip.source_start + .01} max={Math.min(durations[clip.film_id] ?? clip.source_end, clip.source_start + limit - clip.start)}
          disabled={disabled} onChange={(value) => patch({ source_end: value }, "out")} onEndChange={onEndChange} />
        <label>Voice <span>{clip.gain_db > 0 ? "+" : ""}{clip.gain_db} dB</span><input type="range" min={-60} max={24} step={1} value={clip.gain_db} disabled={disabled}
          onChange={(event) => patch({ gain_db: Number(event.target.value) }, "gain")} onPointerUp={onEndChange} onBlur={onEndChange} /></label>
        <label>Music under voice <span>{clip.music_duck_db === 0 ? "Unchanged" : `${clip.music_duck_db} dB`}</span><input type="range" min={-60} max={0} step={1} value={clip.music_duck_db} disabled={disabled}
          onChange={(event) => patch({ music_duck_db: Number(event.target.value) }, "duck")} onPointerUp={onEndChange} onBlur={onEndChange} /></label>
      </div>
      <label className={styles.words}>Words or note <input value={clip.text} maxLength={4000} placeholder="An optional reminder of the line" disabled={disabled}
        onChange={(event) => patch({ text: event.target.value }, "text")} onBlur={onEndChange} /></label>
      <details className={styles.fades}><summary>Fades and music transitions</summary><div className={styles.fadeFields}>
        <DirectionTimeInput label="Fade in (s)" value={clip.fade_in_seconds} min={0} max={Math.min(90, clip.source_end - clip.source_start)} disabled={disabled}
          onChange={(value) => patch({ fade_in_seconds: value }, "fade-in")} onEndChange={onEndChange} />
        <DirectionTimeInput label="Fade out (s)" value={clip.fade_out_seconds} min={0} max={Math.min(90, clip.source_end - clip.source_start)} disabled={disabled}
          onChange={(value) => patch({ fade_out_seconds: value }, "fade-out")} onEndChange={onEndChange} />
        <DirectionTimeInput label="Music eases down (s)" value={clip.duck_attack_seconds ?? .25} min={0} max={5} disabled={disabled}
          onChange={(value) => patch({ duck_attack_seconds: value }, "duck-attack")} onEndChange={onEndChange} />
        <DirectionTimeInput label="Music returns (s)" value={clip.duck_release_seconds ?? .5} min={0} max={5} disabled={disabled}
          onChange={(value) => patch({ duck_release_seconds: value }, "duck-release")} onEndChange={onEndChange} />
      </div></details>
    </div>}
  </section>;
}
