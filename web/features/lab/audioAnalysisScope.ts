import type { LabDocument } from "@/types/lab";

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

/** Larger display limits apply only to the server's scoped composition contract. */
export function audioAnalysisPartCount(document: LabDocument, evidenceProfile: unknown): number | null {
  const analysis = document.analysis ?? {};
  const provenance = record(analysis.provenance);
  const profile = record(evidenceProfile);
  if (!("parts" in analysis) && provenance.contract !== "bounded-audio-parts-v1" && !profile.aggregation_contract) return 1;
  const parts = analysis.parts;
  const manifest = provenance.parts;
  const references = profile.parts;
  if (provenance.contract !== "bounded-audio-parts-v1" || profile.aggregation_contract !== "bounded-audio-parts-v1" ||
      !Array.isArray(parts) || parts.length < 1 || parts.length > 7 ||
      !Array.isArray(manifest) || manifest.length !== parts.length ||
      !Array.isArray(references) || references.length !== parts.length) return null;
  let previous: number | null = null;
  for (let index = 0; index < parts.length; index++) {
    const part = record(parts[index]);
    const scope = record(part.passage);
    const original = record(part.analysis);
    const source = record(original.provenance);
    const reference = record(manifest[index]);
    const profileReference = record(references[index]);
    const heard = record(original[profile.source === "ai-observed" ? "events_provenance" : "song_meaning_provenance"]);
    const sameScope = (value: unknown) => record(value).start === scope.start && record(value).end === scope.end;
    if (typeof scope.start !== "number" || typeof scope.end !== "number" || !Number.isFinite(scope.start) || !Number.isFinite(scope.end) ||
        scope.start < 0 || scope.end <= scope.start || scope.end - scope.start > 90 ||
        scope.start >= document.passage.end || scope.end <= document.passage.start ||
        (previous !== null && Math.abs(scope.start - previous) > 0.000001) || "parts" in original ||
        source.track !== document.track?.id || !sameScope(source.passage) ||
        heard.track !== document.track?.id || heard.source !== profile.source || !sameScope(heard.passage) ||
        !sameScope(reference.passage) || !sameScope(profileReference.passage) ||
        typeof reference.interpretation_id !== "string" || !reference.interpretation_id ||
        reference.interpretation_id !== profileReference.interpretation_id || reference.interpretation_id !== heard.interpretation_id) return null;
    if (index === 0 && scope.start > document.passage.start) return null;
    previous = scope.end;
  }
  return previous !== null && previous >= document.passage.end ? parts.length : null;
}
