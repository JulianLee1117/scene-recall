import type { RecipeMatchFacet } from "@/types/api";

// Product copy only: one short description per category, shared by the Refine
// panel and the Related menu. It must not alter recipe adapters or ranking.
export const SEARCH_CLUE_COPY: Record<RecipeMatchFacet, { description: string }> = {
  scene: { description: "People, action and place" },
  words: { description: "Dialogue and on-screen text" },
  look: { description: "Color, light and texture" },
  composition: { description: "Layout and where subjects sit" },
  mood: { description: "Feeling and energy" },
};

/** Uploads gate Look as well as Framing; indexed Look remains a preference. */
export function referenceGateCopy(facet: RecipeMatchFacet, kind: "source" | "image") {
  if (facet === "composition") return {
    description: "Framing picks the visual shortlist; your description orders it.",
    removeLabel: "Search without Framing",
  };
  if (facet === "look" && kind === "image") return {
    description: "The Look image picks the visual shortlist; your description orders it.",
    removeLabel: "Search without Look image",
  };
  return null;
}
