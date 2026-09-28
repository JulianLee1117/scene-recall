import { validatePair, type TransitionRequest } from "./transitions";

export interface VariantNote { favorite: boolean; note: string; }
export interface SavedRecipe { id: string; name: string; request: TransitionRequest; source_titles?: { outgoing: string; incoming: string }; saved_at: number; }
export interface Notebook { schema_version: 1; variants: Record<string, VariantNote>; recipes: SavedRecipe[]; }
export const NOTEBOOK_KEY = "scene-recall:transitions:notebook:v1";
export const emptyNotebook = (): Notebook => ({ schema_version: 1, variants: {}, recipes: [] });

/** Browser storage is optional and untrusted; malformed entries cannot break the lab. */
export function parseNotebook(raw: string | null): Notebook {
  if (!raw) return emptyNotebook();
  try {
    const value = JSON.parse(raw);
    if (!value || value.schema_version !== 1) return emptyNotebook();
    const variants: Notebook["variants"] = {};
    if (value.variants && typeof value.variants === "object") for (const [id, record] of Object.entries(value.variants).slice(0, 500)) {
      if (!record || typeof record !== "object" || id.length > 100 || ["__proto__", "constructor", "prototype"].includes(id)) continue;
      const note = record as Partial<VariantNote>;
      variants[id] = { favorite: note.favorite === true, note: typeof note.note === "string" ? note.note.slice(0, 2000) : "" };
    }
    const recipes = (Array.isArray(value.recipes) ? value.recipes : []).filter((item: SavedRecipe) => item && typeof item.id === "string" && item.id.length <= 100 && typeof item.name === "string"
      && Number.isFinite(item.saved_at) && item.request && typeof item.request.recipe?.id === "string" && item.request.outgoing && item.request.incoming && item.request.output
      && ["landscape", "portrait", "square"].includes(item.request.output.aspect) && ["draft", "high", "export"].includes(item.request.output.quality)
      && Object.values(item.request.recipe).every((field) => typeof field === "string" || typeof field === "boolean" || (typeof field === "number" && Number.isFinite(field)))
      && [item.request.outgoing, item.request.incoming].every((source) => typeof source.film_id === "string" && typeof source.source_start === "number" && typeof source.source_end === "number")
      && !validatePair(item.request.outgoing, item.request.incoming, item.request.recipe, 12, item.request.retime))
      .slice(0, 50).map((item: SavedRecipe) => ({ id: item.id, name: item.name.slice(0, 100), request: item.request, saved_at: item.saved_at,
        ...(typeof item.source_titles?.outgoing === "string" && typeof item.source_titles?.incoming === "string" ? { source_titles: item.source_titles } : {}) }));
    return { schema_version: 1, variants, recipes };
  } catch { return emptyNotebook(); }
}
