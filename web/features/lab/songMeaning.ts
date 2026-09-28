import type { LabDocument, SongMeaning } from "@/types/lab";
import { audioAnalysisPartCount } from "./audioAnalysisScope";

const boundedText = (value: unknown, max: number, required = true) => typeof value === "string" &&
  value.trim().length <= max && (!required || value.trim().length > 0);

/** Older, opaque analysis stays readable without claiming it understood lyrics. */
export function songMeaning(document: LabDocument): SongMeaning | null {
  const analysis = document.analysis;
  const profile = analysis?.song_meaning_provenance as Record<string, unknown> | undefined;
  const source = analysis?.provenance as Record<string, unknown> | undefined;
  const current = (value?: Record<string, unknown>) => value?.track === document.track?.id &&
    (value?.passage as { start?: number; end?: number } | undefined)?.start === document.passage.start &&
    (value?.passage as { start?: number; end?: number } | undefined)?.end === document.passage.end;
  if (!document.track || !current(source) || !current(profile) || profile?.source !== "ai-heard-paraphrase") return null;
  const parts = audioAnalysisPartCount(document, profile);
  if (parts === null) return null;
  const aggregated = Array.isArray(analysis?.parts);
  const value = analysis?.song_meaning as SongMeaning | undefined;
  if (!value || !["understood", "partly_understood", "unclear", "no_vocals"].includes(value.vocal_status) ||
    !boundedText(value.summary, (aggregated ? 1300 : 1200) * parts) || !boundedText(value.uncertainty, (aggregated ? 700 : 600) * parts, false) ||
    !Array.isArray(value.themes) || value.themes.length > 6 * parts || !value.themes.every((theme) => boundedText(theme, 160)) ||
    !Array.isArray(value.cues) || value.cues.length > 8 * parts || !value.cues.every((cue) => cue && Number.isFinite(cue.start) && Number.isFinite(cue.end) &&
      cue.start >= document.passage.start && cue.end <= document.passage.end && cue.end > cue.start &&
      boundedText(cue.paraphrase, 600) && ["low", "medium", "high"].includes(cue.confidence))) return null;
  if ((["unclear", "no_vocals"].includes(value.vocal_status) && (value.cues.length || value.themes.length)) ||
    (["understood", "partly_understood"].includes(value.vocal_status) && !value.cues.length)) return null;
  return value;
}
