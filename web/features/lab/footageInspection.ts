type Value = Record<string, unknown>;
const record = (value: unknown): Value => value && typeof value === "object" && !Array.isArray(value) ? value as Value : {};
const text = (value: unknown): string => typeof value === "string" ? value.trim() : "";
const count = (value: unknown): number | null => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : null;
const rows = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

export interface FootageObservation {
  artifact_id: string;
  unit_id: string;
  summary: string;
  uncertainty: string;
  events: { start: number; end: number; completion: string; before: string; after: string }[];
}

export interface FootageInspection {
  status: "completed" | "partial" | "unavailable" | "not-needed";
  eligible_count: number | null;
  inspected_count: number | null;
  window_count: number | null;
  cache_hits: number | null;
  changed_count: number | null;
  warning: string;
  targets: {
    slot_id: string;
    position: number;
    hint: "action_timing" | "visual_fit";
    status: "reviewed" | "unavailable";
    reason: string;
    changed: boolean | null;
    observations: FootageObservation[];
  }[];
}

/** Optional job receipt. Missing fields stay unknown rather than implying successful inspection. */
export function readFootageInspection(value: unknown): FootageInspection | null {
  const row = record(value), status = text(row.status);
  if (row.contract !== "targeted-footage-inspection-v1" ||
      !["completed", "partial", "unavailable", "not-needed"].includes(status)) return null;
  const eligible = count(row.eligible_count), inspected = count(row.inspected_count), changed = count(row.changed_count);
  const validCoverage = eligible !== null && inspected !== null && inspected <= eligible;
  return {
    status: status as FootageInspection["status"],
    eligible_count: eligible,
    inspected_count: validCoverage ? inspected : null,
    changed_count: validCoverage && changed !== null && changed <= inspected ? changed : null,
    window_count: count(row.window_count), cache_hits: count(row.cache_hits), warning: text(row.warning),
    targets: rows(row.targets).flatMap((value) => {
      const target = record(value), position = count(target.position);
      if (!text(target.slot_id) || position === null || position < 1 ||
          (target.hint !== "action_timing" && target.hint !== "visual_fit") ||
          (target.status !== "reviewed" && target.status !== "unavailable")) return [];
      const observations: FootageObservation[] = rows(target.observations).flatMap((value) => {
        const observation = record(value);
        if (!text(observation.artifact_id) || !text(observation.unit_id) ||
            (!text(observation.summary) && !text(observation.uncertainty))) return [];
        return [{ artifact_id: text(observation.artifact_id), unit_id: text(observation.unit_id),
          summary: text(observation.summary), uncertainty: text(observation.uncertainty),
          events: rows(observation.events).flatMap((value) => {
            const event = record(value);
            if (!finite(event.start) || !finite(event.end) || event.start < 0 || event.end < event.start) return [];
            return [{ start: event.start, end: event.end, completion: text(event.completion), before: text(event.before), after: text(event.after) }];
          }),
        }];
      });
      return [{ slot_id: text(target.slot_id), position, hint: target.hint, status: target.status,
        reason: text(target.reason), changed: typeof target.changed === "boolean" ? target.changed : null, observations }];
    }),
  };
}
