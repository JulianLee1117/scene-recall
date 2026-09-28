import type { LabDocument } from "@/types/lab";
import { audioAnalysisPartCount } from "./audioAnalysisScope";

export interface MusicCue {
  id: string;
  start: number;
  end: number;
  label: string;
  detail: string;
  source: "audio" | "user";
  confidence?: "low" | "medium" | "high";
}

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
const finite = (value: unknown): value is number =>
  typeof value === "number" && Number.isFinite(value);

/** Only show observations and supplied words belonging to the current music. */
export function musicCues(document: LabDocument): MusicCue[] {
  if (!document.track) return [];
  const { start, end } = document.passage;
  const result: MusicCue[] = [];
  const analysis = document.analysis ?? {};
  const analysisProfile = record(analysis.provenance);
  const analyzedPassage = record(analysisProfile.passage);
  const provenance = record(analysis.events_provenance);
  const passage = record(provenance.passage);
  const parts = audioAnalysisPartCount(document, provenance);
  if (provenance.source === "ai-observed" &&
      analysisProfile.track === document.track.id &&
      analyzedPassage.start === start && analyzedPassage.end === end &&
      provenance.track === document.track.id &&
      passage.start === start && passage.end === end &&
      parts !== null && Array.isArray(analysis.events)) {
    for (const raw of analysis.events.slice(0, 32 * parts)) {
      const event = record(raw);
      if (!finite(event.start) || !finite(event.end) ||
          event.start < start || event.end > end || event.end <= event.start ||
          typeof event.id !== "string" || typeof event.label !== "string") continue;
      if (result.some((cue) => cue.id === `audio:${event.id}`)) continue;
      result.push({
        id: `audio:${event.id}`, start: event.start, end: event.end,
        label: event.label, detail: "Approximate audio observation, not a required cut.",
        source: "audio",
        confidence: ["low", "medium", "high"].includes(String(event.confidence))
          ? event.confidence as MusicCue["confidence"] : undefined,
      });
    }
  }
  const context = record(document.song_context);
  if (context.track_id === document.track.id && Array.isArray(context.lyrics)) {
    for (const raw of context.lyrics.slice(0, 80)) {
      const line = record(raw);
      if (!finite(line.start) || !finite(line.end) || line.end <= line.start ||
          line.start < 0 || line.end > document.track.duration ||
          line.start >= end || line.end <= start ||
          typeof line.id !== "string" || typeof line.text !== "string") continue;
      result.push({
        id: `lyric:${line.id}`, start: Math.max(start, line.start), end: Math.min(end, line.end),
        label: line.text.trim() || (typeof line.meaning === "string" ? line.meaning : "Supplied meaning"), detail: typeof line.meaning === "string" && line.meaning.trim()
          ? line.meaning : "Your lyric or paraphrase; timing supplied by you.", source: "user",
      });
    }
  }
  return result.sort((a, b) => a.start - b.start || a.end - b.end);
}
