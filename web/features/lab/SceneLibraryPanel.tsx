"use client";

import { useEffect, useState, type ReactNode } from "react";
import type { LabClip, MusicDirection, MusicMatchEvidence, MusicSlot } from "@/types/lab";
import EditorIcon from "./EditorIcon";
import EditorPopover from "./EditorPopover";
import MusicSceneChoices from "./MusicSceneChoices";
import ShotExplanation, { type ShotNeighbor } from "./ShotExplanation";
import styles from "./sceneLibraryPanel.module.css";

type Props = {
  slot: MusicSlot;
  currentClip: LabClip | null;
  direction?: MusicDirection | null;
  previous?: ShotNeighbor | null;
  next?: ShotNeighbor | null;
  savedClips: LabClip[];
  query: string;
  films: Record<string, { title: string }>;
  disabled: boolean;
  searching: boolean;
  searchProblem: string | null;
  searchOptions: ReactNode;
  onQuery: (query: string) => void;
  onSearch: () => void;
  onPlan: () => void;
  onPreview: (clip: LabClip, evidence: MusicMatchEvidence | null) => void;
  onChoose: (clip: LabClip, evidence: MusicMatchEvidence | null) => void;
  onDialogue?: (clip: LabClip, evidence: MusicMatchEvidence | null) => void;
  onDragScene: (clip: LabClip | null, evidence?: MusicMatchEvidence | null) => void;
  onDropScene: (slotId: string) => void;
  onClearSaved: () => void;
};

/** One search for the selected timeline position, with explicit placement. */
export default function SceneLibraryPanel({
  slot, currentClip, direction = slot.direction ?? null, previous, next, savedClips, query, films, disabled, searching, searchProblem,
  searchOptions, onQuery, onSearch, onPlan, onPreview, onChoose, onDialogue, onDragScene, onDropScene, onClearSaved,
}: Props) {
  const [showSaved, setShowSaved] = useState(false);
  useEffect(() => { if (!savedClips.length) setShowSaved(false); }, [savedClips.length]);
  const locked = !!currentClip?.locked;
  const duration = slot.end - slot.start;
  const saved = showSaved && savedClips.length > 0;
  // Preview the exact window that Use will place; retained originals stay intact.
  const alternatives = saved ? savedClips.map((clip) => ({
    clip: { ...clip, source_end: Math.min(clip.source_end, clip.source_start + duration),
      reference_time: null, window_start: null, window_end: null },
    film_title: films[clip.film_id]?.title ?? "Scene", search_evidence: null,
  })) : slot.alternatives;
  return (
    <div className={styles.library}>
      {!currentClip && <div className={styles.emptySlot} role="status">
        <strong>No scene selected</strong>
        <p>{slot.search_error || (alternatives.length
          ? "Preview a suggestion, then choose Use scene."
          : query.trim() ? "Choose Find scenes to fill this clip." : "Describe a scene below, then choose Find scenes.")}</p>
      </div>}
      <form className={styles.search} onSubmit={(event) => { event.preventDefault(); if (!disabled && !locked && !searchProblem) { setShowSaved(false); onSearch(); } }}>
        <label htmlFor="scene-search">Describe a scene</label>
        <textarea id="scene-search" rows={2} maxLength={400} value={query}
          disabled={disabled || locked} placeholder="A face in a passing train, warm evening light…"
          onChange={(event) => { setShowSaved(false); onQuery(event.target.value); }} />
        <div className={styles.searchActions}>
          <EditorPopover title="Search options" label="Search options" align="start" width={360} triggerClassName={styles.quietButton}>
            {searchOptions}
          </EditorPopover>
          <button type="submit" className={styles.find} disabled={disabled || locked || !!searchProblem}>
            <EditorIcon name="search" /> {searching ? "Searching…" : "Find scenes"}
          </button>
        </div>
      </form>
      {locked && <p className={styles.hint}>Unlock this clip to change its scene.</p>}
      {!locked && searchProblem && query.trim() && <p className={styles.hint} role="status">{searchProblem}</p>}
      <div className={styles.context}>
        {savedClips.length > 0 ? <div className={styles.views} role="group" aria-label="Scene sources">
          <button type="button" aria-pressed={!saved} onClick={() => setShowSaved(false)}>Results</button>
          <button type="button" aria-pressed={saved} onClick={() => setShowSaved(true)}>Saved clips · {savedClips.length}</button>
        </div> : <strong>{slot.alternatives.length ? `${slot.alternatives.length} suggestions` : "Scene suggestions"}</strong>}
        {saved ? <button type="button" disabled={disabled || savedClips.every((clip) => clip.locked)}
          onClick={onClearSaved} aria-label="Clear unlocked saved clips"
          title="Remove unused, unlocked saved clips. Undo restores them.">Clear</button>
          : <EditorPopover title="Why this shot" label="Why this shot" width={520} align="end" triggerClassName={styles.quietButton}>
          {(close) => <div className={styles.direction}>
            <ShotExplanation slot={slot} clip={currentClip} direction={direction} query={query} previous={previous} next={next} films={films} />
            <button disabled={disabled || locked} onClick={() => { close(); setShowSaved(false); onPlan(); }}><EditorIcon name="sparkles" /> {query.trim() ? "Rewrite description" : "Suggest description"}</button>
          </div>}
        </EditorPopover>}
      </div>
      {saved && <p className={styles.hint}>Kept from earlier cuts. Preview a clip, then use it or drag it onto the timeline.</p>}
      <div aria-busy={!saved && searching}>
        {alternatives.length > 0 ? <MusicSceneChoices
          alternatives={alternatives} currentClip={currentClip} films={films}
          disabled={disabled} locked={locked} minimumDuration={duration}
          onPreview={(source) => onPreview(source, alternatives.find((item) => item.clip.id === source.id)?.search_evidence ?? null)}
          onChoose={onChoose} onDialogue={onDialogue} onDragScene={onDragScene} onDropScene={onDropScene}
        /> : (currentClip || searching) && <p className={styles.empty}>
          {searching ? "Searching your film library…" : query.trim()
            ? "Find scenes, preview a result, then use it or drag it onto the timeline."
            : "Generate an edit to get started, or describe a scene and search here."}
        </p>}
      </div>
      {currentClip && !saved && slot.search_error && <p className={styles.error} role="status">{slot.search_error}</p>}
    </div>
  );
}
