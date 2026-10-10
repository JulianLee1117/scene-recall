import type { LabClip, LabDialogueClip, LabDocument } from "@/types/lab";

export const MUSIC_DUCK_ATTACK_SECONDS = 0.25;
export const MUSIC_DUCK_RELEASE_SECONDS = 0.5;
const clamp = (value: number, low = 0, high = 1) => Math.max(low, Math.min(high, value));
export const dbAmplitude = (db: number) => 10 ** (clamp(db, -60, 24) / 20);
export const dialogueEnd = (clip: LabDialogueClip) => clip.start + clip.source_end - clip.source_start;
export const dialogueAt = (clips: LabDialogueClip[], time: number) =>
  clips.find((clip) => time >= clip.start && time < dialogueEnd(clip)) ?? null;

export function dialogueVolume(clip: LabDialogueClip, time: number) {
  const duration = clip.source_end - clip.source_start;
  const fadeIn = Math.min(duration, clip.fade_in_seconds ?? 0.08);
  const fadeOut = Math.min(duration, clip.fade_out_seconds ?? 0.12);
  if (time < clip.start || time >= dialogueEnd(clip)) return 0;
  return dbAmplitude(clip.gain_db ?? 0) *
    (fadeIn > 0 ? clamp((time - clip.start) / fadeIn) : 1) *
    (fadeOut > 0 ? clamp((dialogueEnd(clip) - time) / fadeOut) : 1);
}

export function musicVolume(document: LabDocument, time: number) {
  const { start, end } = document.passage;
  const fadeIn = Math.min(end - start, document.audio_fade_in_seconds ?? 0);
  const fadeOut = Math.min(end - start, document.audio_fade_out_seconds ?? 0);
  let duck = 1;
  for (const clip of document.dialogue_clips ?? []) {
    const finish = dialogueEnd(clip), level = dbAmplitude(clip.music_duck_db ?? -8);
    const attack = clip.duck_attack_seconds ?? MUSIC_DUCK_ATTACK_SECONDS;
    const release = clip.duck_release_seconds ?? MUSIC_DUCK_RELEASE_SECONDS;
    const amount = time < clip.start
      ? attack > 0 ? clamp((time - clip.start + attack) / attack) : 0
      : time < finish ? 1 : release > 0 ? clamp(1 - (time - finish) / release) : 0;
    duck = Math.min(duck, 1 - (1 - level) * amount);
  }
  return dbAmplitude(document.music_gain_db ?? 0) * duck *
    (fadeIn > 0 ? clamp((time - start) / fadeIn) : 1) *
    (fadeOut > 0 ? clamp((end - time) / fadeOut) : 1);
}

/** Preserve absolute song placement and the surviving source words when trimming. */
export function trimDialogueToPassage(clips: LabDialogueClip[], passage: LabDocument["passage"]) {
  return clips.flatMap((clip) => {
    const start = Math.max(clip.start, passage.start), end = Math.min(dialogueEnd(clip), passage.end);
    return end <= start ? [] : [{ ...clip, start,
      source_start: clip.source_start + start - clip.start,
      source_end: clip.source_start + end - clip.start }];
  });
}

export function addDialogue(document: LabDocument, source: LabClip, id: string, preferredStart: number, text = ""): LabDocument {
  const clips = document.dialogue_clips ?? [];
  if (clips.length >= 32) throw new Error("This edit already has 32 dialogue clips. Remove one to add another.");
  const duration = source.source_end - source.source_start;
  const start = Math.max(document.passage.start, preferredStart);
  const available = document.passage.end - start;
  if (available < 0.05) throw new Error("There is no room for dialogue here. Move the playhead earlier or remove a dialogue clip.");
  return { ...document, dialogue_clips: [...clips, {
    id, film_id: source.film_id, unit_id: source.unit_id, title: source.title, text,
    source_start: source.source_start, source_end: source.source_start + Math.min(duration, available), start,
    gain_db: 0, fade_in_seconds: 0.08, fade_out_seconds: 0.12, music_duck_db: -8,
    source_audio_mode: "original" as const, duck_attack_seconds: .6, duck_release_seconds: 1.2,
  }].sort((a, b) => a.start - b.start) };
}

export function updateDialogue(document: LabDocument, id: string, patch: Partial<LabDialogueClip>): LabDocument {
  const clips = (document.dialogue_clips ?? []).map((clip) => clip.id === id ? { ...clip, ...patch } : clip)
    .sort((a, b) => a.start - b.start);
  for (let i = 0; i < clips.length; i++) {
    const clip = clips[i];
    if (![clip.start, clip.source_start, clip.source_end].every(Number.isFinite) ||
      clip.source_start < 0 || clip.source_end <= clip.source_start ||
      clip.start < document.passage.start - 1e-6 || dialogueEnd(clip) > document.passage.end + 1e-6)
      throw new Error("Keep the dialogue inside the edit, with its source out after its source in.");
  }
  return { ...document, dialogue_clips: clips };
}

/** Voices can overlap; dragging only clamps to the chosen song passage. */
export function boundedDialogueStart(document: LabDocument, id: string, target: number) {
  const voice = document.dialogue_clips?.find((clip) => clip.id === id);
  if (!voice) return target;
  const duration = voice.source_end - voice.source_start;
  return clamp(target, document.passage.start, Math.max(document.passage.start, document.passage.end - duration));
}
