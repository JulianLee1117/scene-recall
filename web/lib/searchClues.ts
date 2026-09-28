import type { RecipeMatchFacet } from "@/types/api";

// Product copy only. These descriptions must not alter recipe adapters or ranking.
export const SEARCH_CLUE_COPY: Record<
  RecipeMatchFacet,
  { related: string; description: string; effect: string }
> = {
  scene: {
    related: "Similar situation",
    description: "People, actions and setting from the scene description.",
    effect: "Preference · scene description",
  },
  words: {
    related: "Related words",
    description: "Spoken dialogue and visible text.",
    effect: "Preference · dialogue and visible text",
  },
  look: {
    related: "Similar appearance",
    description: "Color, light, texture and visual subjects.",
    effect: "Preference · overall appearance",
  },
  composition: {
    related: "Similar framing",
    description: "Visual layout and positions. Limits results to visual matches.",
    effect: "Limits results to visual matches",
  },
  mood: {
    related: "Similar feeling",
    description: "The scene's stored mood and energy.",
    effect: "Preference · mood and energy",
  },
};

/** Uploads gate Look as well as Framing; indexed Look remains a preference. */
export function referenceGateCopy(facet: RecipeMatchFacet, kind: "source" | "image") {
  if (facet === "composition") return {
    description: "Framing keeps a visual shortlist. Your description ranks scenes within it.",
    removeLabel: "Search without Framing",
  };
  if (facet === "look" && kind === "image") return {
    description: "An uploaded Look image keeps a visual shortlist. Your description ranks scenes within it.",
    removeLabel: "Search without Look image",
  };
  return null;
}
