import { validatePair, type TransitionRecipe, type TransitionRetime, type TransitionSource } from "./transitions";

export type FastEditTiming =
  | { available: false; reason: string }
  | { available: true; reason: null; recipe: TransitionRecipe; retime: TransitionRetime };

/** An explicit camera-whip timing preset; never extends or replaces source selections. */
export function fastEditTiming(a: TransitionSource | null, b: TransitionSource | null, recipe: TransitionRecipe | null): FastEditTiming {
  if (recipe?.id !== "whip-pan") return { available: false, reason: "Choose Camera whip to use fast edit timing." };
  if (!a || !b) return { available: false, reason: "Choose both clips to use fast edit timing." };
  for (const [label, source] of [["A", a], ["B", b]] as const) {
    if (!Number.isFinite(source.source_start) || !Number.isFinite(source.source_end) || source.source_start < 0 || source.source_end <= source.source_start)
      return { available: false, reason: `Clip ${label} needs valid in and out points.` };
    if (source.source_end - source.source_start < 1 - 1e-8)
      return { available: false, reason: `Clip ${label} needs at least 1 second for this speed ramp.` };
  }
  const nextRecipe = { ...recipe, duration: 8 / 30 };
  const retime: TransitionRetime = { mode: "rush", speed: 2, span: 1, curve: "smooth", interpolation: "nearest" };
  const problem = validatePair(a, b, nextRecipe, 12, retime);
  return problem ? { available: false, reason: problem } : { available: true, reason: null, recipe: nextRecipe, retime };
}
