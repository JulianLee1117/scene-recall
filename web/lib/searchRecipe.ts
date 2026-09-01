import type {
  RecipeImageFacet,
  RecipeMatchFacet,
  RecipeSource,
  SearchFacet,
  SearchRecipeClause,
  SearchResult,
} from "@/types/api";

export const MAX_RECIPE_CLAUSES = 3;
export const SCENE_SOURCE_MIME = "application/x-scene-recall-source";
const SCENE_SOURCE_META_MIME = "application/x-scene-recall-source-meta";
const SCENE_SOURCE_ORIGIN_MIME = "application/x-scene-recall-source-origin";

export const MATCH_FACETS: readonly RecipeMatchFacet[] = [
  "scene",
  "words",
  "look",
  "composition",
  "mood",
];

export const TEXT_MATCH_FACETS = ["scene", "words", "look", "mood"] as const;
export type TextMatchFacet = (typeof TEXT_MATCH_FACETS)[number];

export const FACET_LABELS: Record<SearchFacet | RecipeImageFacet, string> = {
  all: "Search",
  scene: "Scene",
  words: "Words",
  look: "Look",
  composition: "Framing",
  mood: "Mood",
};

export interface RecipeSourceDisplay {
  filmTitle?: string;
  timestamp?: number;
  keyframeUrl?: string;
}

export interface RecipeImageDisplay {
  label: string;
  previewUrl: string;
}

export interface RecipeImageInput {
  file: File;
  facet: RecipeImageFacet;
  display: RecipeImageDisplay;
}

export type MatchDraft =
  | {
      kind: "text";
      facet: TextMatchFacet;
      text: string;
    }
  | {
      kind: "source";
      facet: RecipeMatchFacet;
      source: RecipeSource;
      display?: RecipeSourceDisplay;
    };

export type MatchDrafts = Partial<Record<RecipeMatchFacet, MatchDraft>>;

interface SceneSourceMeta extends RecipeSourceDisplay {}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isRecipeSource(value: unknown): value is RecipeSource {
  if (!value || typeof value !== "object") return false;
  const source = value as Partial<RecipeSource>;
  return (
    typeof source.unit_id === "string" &&
    source.unit_id.length > 0 &&
    isNonNegativeInteger(source.frame_index)
  );
}

export function sourceDraftFromShot(
  facet: RecipeMatchFacet,
  shot: SearchResult,
): MatchDraft | null {
  if (!isNonNegativeInteger(shot.keyframe_index)) return null;
  return {
    kind: "source",
    facet,
    source: {
      unit_id: shot.unit_id,
      frame_index: shot.keyframe_index,
    },
    display: {
      filmTitle: shot.film_title,
      timestamp: shot.matched_frame_timestamp ?? shot.t_start,
      keyframeUrl: shot.keyframe_url,
    },
  };
}

export function writeSceneSourceDrag(
  transfer: DataTransfer,
  shot: SearchResult,
): boolean {
  const draft = sourceDraftFromShot("scene", shot);
  if (!draft || draft.kind !== "source") return false;

  transfer.effectAllowed = "copy";
  transfer.setData(SCENE_SOURCE_MIME, JSON.stringify(draft.source));
  transfer.setData(SCENE_SOURCE_META_MIME, JSON.stringify(draft.display ?? {}));
  return true;
}

export function writeFacetSourceDrag(
  transfer: DataTransfer,
  draft: MatchDraft,
  originFacet: RecipeMatchFacet,
): boolean {
  if (draft.kind !== "source") return false;

  transfer.effectAllowed = "move";
  transfer.setData(SCENE_SOURCE_MIME, JSON.stringify(draft.source));
  transfer.setData(SCENE_SOURCE_META_MIME, JSON.stringify(draft.display ?? {}));
  transfer.setData(SCENE_SOURCE_ORIGIN_MIME, originFacet);
  return true;
}

export function readFacetSourceDragOrigin(
  transfer: DataTransfer,
): RecipeMatchFacet | null {
  const facet = transfer.getData(SCENE_SOURCE_ORIGIN_MIME);
  return MATCH_FACETS.includes(facet as RecipeMatchFacet)
    ? (facet as RecipeMatchFacet)
    : null;
}

export function readSceneSourceDrag(
  transfer: DataTransfer,
  facet: RecipeMatchFacet,
): MatchDraft | null {
  try {
    const source = JSON.parse(transfer.getData(SCENE_SOURCE_MIME)) as unknown;
    if (!isRecipeSource(source)) return null;

    let display: RecipeSourceDisplay | undefined;
    const encodedMeta = transfer.getData(SCENE_SOURCE_META_MIME);
    if (encodedMeta) {
      const parsed = JSON.parse(encodedMeta) as SceneSourceMeta;
      display = {
        filmTitle:
          typeof parsed.filmTitle === "string" ? parsed.filmTitle : undefined,
        timestamp:
          typeof parsed.timestamp === "number" ? parsed.timestamp : undefined,
        keyframeUrl:
          typeof parsed.keyframeUrl === "string" ? parsed.keyframeUrl : undefined,
      };
    }

    return { kind: "source", facet, source, display };
  } catch {
    return null;
  }
}

export function matchDraftHasClause(draft: MatchDraft | undefined): boolean {
  return Boolean(
    draft && (draft.kind === "source" || draft.text.trim().length > 0),
  );
}

export function recipeClauseCount(
  mainText: string,
  drafts: MatchDrafts,
  mainImage?: RecipeImageInput | null,
): number {
  return (
    (mainText.trim() ? 1 : 0) +
    (mainImage ? 1 : 0) +
    MATCH_FACETS.filter(
      (facet) =>
        facet !== mainImage?.facet && matchDraftHasClause(drafts[facet]),
    ).length
  );
}

export function buildRecipeClauses(
  mainText: string,
  drafts: MatchDrafts,
  mainImage?: RecipeImageInput | null,
): SearchRecipeClause[] {
  const clauses: SearchRecipeClause[] = [];
  const trimmedMain = mainText.trim();
  if (trimmedMain) {
    clauses.push({ id: "main", kind: "text", facet: "all", text: trimmedMain });
  }
  if (mainImage) {
    clauses.push({
      id: `facet-${mainImage.facet}`,
      kind: "image",
      facet: mainImage.facet,
    });
  }

  MATCH_FACETS.forEach((facet) => {
    if (facet === mainImage?.facet) return;
    const draft = drafts[facet];
    if (!draft) return;
    if (draft.kind === "source") {
      clauses.push({
        id: `facet-${facet}`,
        kind: "source",
        facet,
        source: draft.source,
      });
      return;
    }

    const text = draft.text.trim();
    if (text) {
      clauses.push({
        id: `facet-${facet}`,
        kind: "text",
        facet: draft.facet,
        text,
      });
    }
  });

  return clauses;
}
