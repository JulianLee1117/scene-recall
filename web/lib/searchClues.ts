import { readableShotDetails } from "@/lib/matchReasons";
import type { RecipeMatchFacet, ResolvedSourceEvidence } from "@/types/api";

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

/** What a scene reference searches for once placed in a category. */
export interface ReferenceReading {
  heading: string;
  /** The exact text the category takes from the scene, by view. */
  parts: { label: string; text: string }[];
  /** One line for the active chip; absent for visual categories. */
  summary?: string;
}

const READING_HEADINGS: Record<RecipeMatchFacet, string> = {
  scene: "Searching for its description",
  words: "Searching for its words",
  mood: "Searching for its mood",
  look: "Searching by its picture: color, light and texture. No words are used.",
  composition: "Searching by its layout: where people and things sit in the frame. No words are used.",
};

const SOURCE_VIEW_LABELS: Record<string, string> = {
  caption: "Picture",
  dialogue: "Dialogue",
  ocr: "On-screen text",
  mood: "Mood",
};

/** Reads the API's source evidence: the text each category actually uses. */
export function referenceReading(facet: RecipeMatchFacet, evidence?: ResolvedSourceEvidence): ReferenceReading {
  const parts = (evidence?.evidence ?? []).flatMap((item) =>
    item.type === "text" && item.text.trim()
      ? [{ label: SOURCE_VIEW_LABELS[item.view] ?? item.view, text: item.view === "mood" ? readableShotDetails(item.text) : item.text.trim() }]
      : []);
  const summary = parts.length
    ? parts.map((part) => (part.label === "Dialogue" ? `“${part.text}”` : part.text)).join(" · ")
    : undefined;
  return { heading: READING_HEADINGS[facet], parts, summary };
}
