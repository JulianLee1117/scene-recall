"use client";

import { useId } from "react";
import MovieScopeFilter from "@/components/MovieScopeFilter";
import { seconds } from "@/lib/lab";
import type { LabDocument, PlannerSettings, SongContext, SongLyric } from "@/types/lab";
import { appendDirectionExample, changeEditorDirection, effectiveEditorDirection, MAX_DIRECTION_LENGTH } from "./editorDirection";
import MusicDirectionRanges from "./MusicDirectionRanges";
import DirectionTimeInput from "./DirectionTimeInput";
import EditorIcon from "./EditorIcon";
import styles from "./musicDirection.module.css";

interface Props {
  document: LabDocument;
  disabled: boolean;
  active: boolean;
  playhead: number;
  onSeek: (time: number) => void;
  onChange: (update: (document: LabDocument) => LabDocument, group?: string) => void;
  onEndChange: () => void;
  onGenerate: () => void;
  hasEdit: boolean;
  generationProblem?: string;
  waveform?: { peaks: number[]; status: string };
}

const PACING: { value: PlannerSettings["pacing"]; label: string; description: string }[] = [
  { value: "patient", label: "Patient", description: "Sustained, readable images" },
  { value: "balanced", label: "Balanced", description: "A mix of cuts and holds" },
  { value: "kinetic", label: "Energetic", description: "Momentum and contrast" },
  { value: "rapid", label: "Rapid", description: "Fast clusters with room to breathe" },
];
const EXAMPLES = [
  { label: "Color & surrealism", text: "Surreal, colorful images that feel like a vivid dream. Let associations between shapes, light and color carry the edit." },
  { label: "Recurring motif", text: "Return to a recurring visual motif across different films. Use repetition intentionally, with variation as the song develops." },
  { label: "Build & release", text: "Build tension through increasingly fragmented images, then open into sustained, spacious shots when the music releases." },
  { label: "Visual contrast", text: "Alternate intimate, quiet images with vast, busy spaces. Make the contrast between scenes part of the emotional rhythm." },
  { label: "Opening & ending", text: "Open with a striking, immediately readable image. End on a memorable image that resolves or deliberately echoes the opening." },
];

function songContext(document: LabDocument): SongContext | null {
  return document.song_context?.track_id === document.track?.id ? document.song_context ?? null : null;
}

function updateContext(document: LabDocument, update: (context: SongContext) => SongContext): LabDocument {
  if (!document.track) return document;
  return { ...document, song_context: update(songContext(document) ?? { track_id: document.track.id, notes: "", lyrics: [] }) };
}

export default function MusicDirectionPanel(props: Props) {
  const { document, disabled, active, playhead, onChange, onEndChange, onGenerate, hasEdit, generationProblem } = props;
  const inputId = useId();
  const hintId = useId();
  const direction = effectiveEditorDirection(document);
  const settings = document.planner_settings ?? { pacing: "balanced", lyric_treatment: "metaphorical" };
  const context = songContext(document);
  const lyrics = context?.lyrics ?? [];
  const track = document.track;
  const block = !track ? "Choose a song before generating an edit." : generationProblem;

  function changeSetting(patch: Partial<PlannerSettings>) {
    onEndChange();
    onChange((current) => ({ ...current, planner_settings: { pacing: "balanced", lyric_treatment: "metaphorical", ...current.planner_settings, ...patch } }));
  }
  function patchCue(id: string, patch: Partial<SongLyric>, group?: string) {
    onChange((current) => updateContext(current, (context) => ({ ...context,
      lyrics: context.lyrics.map((line) => line.id === id ? { ...line, ...patch } : line),
    })), group);
  }
  function addCue() {
    if (!track || disabled || lyrics.length >= 80) return;
    const start = Math.max(0, Math.min(playhead, track.duration - .1));
    const id = crypto.randomUUID();
    onEndChange();
    onChange((current) => updateContext(current, (context) => ({ ...context, lyrics: [...context.lyrics,
      { id, start, end: Math.min(track.duration, start + 4), text: "", meaning: "" }],
    })));
  }

  return <section className={styles.panel} aria-label="AI edit direction">
    <div className={styles.overview}>
      <div className={styles.mainDirection}>
        <label htmlFor={inputId}>What should this edit feel like?</label>
        <p className={styles.intro}>Describe the imagery, mood and progression. Leave it open for the AI, or be specific.</p>
        <textarea id={inputId} rows={6} value={direction.instruction} maxLength={MAX_DIRECTION_LENGTH} disabled={disabled}
          placeholder="Surreal, saturated images. Follow the song’s restless energy, with brief bursts of movement and quieter moments to breathe…"
          onChange={(event) => { const instruction = event.target.value; onChange((current) => changeEditorDirection(current, { instruction }), "editor-instruction"); }}
          onBlur={onEndChange} />
        <details className={styles.examples}><summary>Add an example</summary><div>
          {EXAMPLES.map((example) => <button key={example.label} type="button" disabled={disabled || direction.instruction.length >= MAX_DIRECTION_LENGTH}
            onClick={() => { onEndChange(); onChange((current) => appendDirectionExample(current, example.text)); }}>
            <EditorIcon name="plus" size={12} /> {example.label}
          </button>)}
        </div></details>
        <p className={styles.capabilities}>Themes, colors and visual motifs guide scene search. Cuts can land on frames that match across the cut.</p>
      </div>
      <div className={styles.preferences}>
        <fieldset className={styles.pacing} disabled={disabled}>
          <legend>Visual pace</legend><div>{PACING.map((choice) => <label key={choice.value} title={choice.description}>
            <input type="radio" name={`${inputId}-pacing`} value={choice.value} checked={settings.pacing === choice.value}
              onChange={() => changeSetting({ pacing: choice.value })} /><span>{choice.label}</span>
          </label>)}</div>
          <p>Music guides cuts and holds. Beats are optional anchors.</p>
        </fieldset>
        <label className={styles.field}>Images and lyrics
          <select value={settings.lyric_treatment} disabled={disabled}
            onChange={(event) => changeSetting({ lyric_treatment: event.target.value as PlannerSettings["lyric_treatment"] })}>
            <option value="ignore">Follow the music</option><option value="literal">Illustrate the words</option>
            <option value="metaphorical">Suggest their meaning</option><option value="counterpoint">Create a deliberate contrast</option>
          </select>
        </label>
        <label className={styles.field}>Footage
          <select value={settings.footage ?? "balanced"} disabled={disabled}
            onChange={(event) => changeSetting({ footage: event.target.value as NonNullable<PlannerSettings["footage"]> })}>
            <option value="balanced">Mix recognizable and fresh</option><option value="famous">Recognizable moments</option>
            <option value="gems">Fresh, lesser-known shots</option>
          </select>
        </label>
        <label className={styles.field}>Match cuts
          <select value={settings.match_cuts ?? "some"} disabled={disabled}
            onChange={(event) => changeSetting({ match_cuts: event.target.value as NonNullable<PlannerSettings["match_cuts"]> })}>
            <option value="some">Where they fit</option><option value="many">As often as possible</option>
            <option value="off">Plain cuts</option>
          </select>
        </label>
        <fieldset className={styles.filmScope} disabled={disabled}><legend>Source films</legend>
          <MovieScopeFilter selectedFilmIds={document.film_ids} onChange={(film_ids) => {
            if (disabled) return;
            onEndChange(); onChange((current) => ({ ...current, film_ids }));
          }} />
          <p>Limits future scene searches and generation. Era and visual descriptions in your direction guide the look.</p>
        </fieldset>
      </div>
    </div>

    <MusicDirectionRanges {...props} />

    <details className={styles.songNotes}>
      <summary><span>Song meaning & lyric cues</span><small>{lyrics.length ? `${lyrics.length} cues` : context?.notes ? "Notes added" : "Optional"}</small></summary>
      <div className={styles.notesBody}>
        <label className={styles.field}>What does this song mean to you?
          <textarea rows={2} maxLength={4000} value={context?.notes ?? ""} disabled={disabled || !track}
            placeholder="The melody feels hopeful, but the words are about saying goodbye…"
            onChange={(event) => { const notes = event.target.value; onChange((current) => updateContext(current, (context) => ({ ...context, notes })), "song-notes"); }} onBlur={onEndChange} />
        </label>
        <div className={styles.cueHeading}><p>Optional words or meaning tied to a moment in the song.</p>
          <button type="button" disabled={disabled || !track || lyrics.length >= 80} onClick={addCue}><EditorIcon name="plus" size={13} /> Cue at playhead</button>
        </div>
        <div className={styles.cueList}>{lyrics.map((cue, index) => {
          const outside = cue.end <= document.passage.start || cue.start >= document.passage.end;
          return <div key={cue.id} className={styles.cue}>
            <div className={styles.cueHeading}><strong>Cue {index + 1}</strong><span>{seconds(cue.start)} – {seconds(cue.end)} · song time{outside ? " · outside selected section" : ""}</span>
              <button type="button" aria-label={`Remove lyric cue ${index + 1}`} disabled={disabled} onClick={() => {
                onEndChange(); onChange((current) => updateContext(current, (context) => ({ ...context, lyrics: context.lyrics.filter((line) => line.id !== cue.id) })));
              }}><EditorIcon name="trash" size={13} /></button></div>
            <div className={styles.cueFields}>
              <label className={styles.field}>Words or lyric fragment<textarea rows={2} maxLength={2000} disabled={disabled} value={cue.text}
                onChange={(event) => patchCue(cue.id, { text: event.target.value }, `lyric-text:${cue.id}`)} onBlur={onEndChange} /></label>
              <label className={styles.field}>Meaning or feeling<textarea rows={2} maxLength={2000} disabled={disabled} value={cue.meaning}
                onChange={(event) => patchCue(cue.id, { meaning: event.target.value }, `lyric-meaning:${cue.id}`)} onBlur={onEndChange} /></label>
            </div>
            <details className={styles.precision}><summary>Adjust cue timing</summary><div>
              {(["start", "end"] as const).map((edge) => <DirectionTimeInput key={edge} label={`${edge === "start" ? "In" : "Out"} · song seconds`}
                min={edge === "start" ? 0 : cue.start + .1} max={edge === "start" ? cue.end - .1 : track?.duration ?? cue.end}
                value={cue[edge]} disabled={disabled} onChange={(value) => patchCue(cue.id, { [edge]: value }, `lyric-time:${cue.id}`)}
                onEndChange={onEndChange} />)}
            </div></details>
          </div>;
        })}</div>
      </div>
    </details>
    <footer className={styles.footer}>
      <p id={hintId} className={block ? styles.error : undefined}>{block || (hasEdit
        ? "Changes guide your next generation. Regenerate replaces the scenes and cuts; the current edit stays in History."
        : "Generate builds scenes and cuts around your direction and music. You can refine them in Edit.")}</p>
      <button type="button" className={styles.generate} disabled={disabled || !active || Boolean(block)} aria-describedby={hintId}
        onClick={() => { if (disabled || !active || block) return; onEndChange(); onGenerate(); }}>
        <EditorIcon name="sparkles" size={14} /> {hasEdit ? "Regenerate edit" : "Generate edit"}
      </button>
    </footer>
  </section>;
}
