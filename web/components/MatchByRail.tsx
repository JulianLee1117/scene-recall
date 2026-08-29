"use client";

import { useState } from "react";
import FacetIcon from "./FacetIcon";
import { formatTime } from "@/lib/format";
import {
  FACET_LABELS,
  MATCH_FACETS,
  MAX_RECIPE_CLAUSES,
  SCENE_SOURCE_MIME,
  matchDraftHasClause,
  readSceneSourceDrag,
  recipeClauseCount,
  type MatchDraft,
  type MatchDrafts,
  type TextMatchFacet,
} from "@/lib/searchRecipe";
import type { RecipeMatchFacet } from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

const FACET_HELP: Record<
  RecipeMatchFacet,
  { description: string; placeholder?: string }
> = {
  scene: {
    description: "What is happening",
    placeholder: "what happens…",
  },
  words: {
    description: "Dialogue or on-screen words",
    placeholder: "words you remember…",
  },
  look: {
    description: "Objects, color, and light",
    placeholder: "visual details…",
  },
  composition: {
    description: "Framing and arrangement",
  },
  mood: {
    description: "Feeling and atmosphere",
    placeholder: "the feeling…",
  },
};

interface MatchByRailProps {
  mainText: string;
  drafts: MatchDrafts;
  onActivateText: (facet: TextMatchFacet) => void;
  onTextChange: (facet: TextMatchFacet, text: string) => void;
  onRemove: (facet: RecipeMatchFacet) => void;
  onSource: (facet: RecipeMatchFacet, draft: MatchDraft) => void;
  onLimit: () => void;
}

function sourceLabel(draft: MatchDraft): string {
  if (draft.kind !== "source") return "";
  return draft.display?.filmTitle || `Scene …${draft.source.unit_id.slice(-8)}`;
}

function sourceDetail(draft: MatchDraft): string {
  if (draft.kind !== "source") return "";
  if (typeof draft.display?.timestamp === "number") {
    return formatTime(draft.display.timestamp);
  }
  return `frame ${(draft.source.frame_index ?? 0) + 1}`;
}

export default function MatchByRail({
  mainText,
  drafts,
  onActivateText,
  onTextChange,
  onRemove,
  onSource,
  onLimit,
}: MatchByRailProps) {
  const [dragFacet, setDragFacet] = useState<RecipeMatchFacet | null>(null);
  const clauseCount = recipeClauseCount(mainText, drafts);
  const overLimit = clauseCount > MAX_RECIPE_CLAUSES;

  const canUseFacet = (facet: RecipeMatchFacet) =>
    matchDraftHasClause(drafts[facet]) || clauseCount < MAX_RECIPE_CLAUSES;

  return (
    <section className="match-rail" aria-labelledby="match-rail-label">
      <div className="match-rail-heading">
        <span id="match-rail-label">Match by</span>
        {clauseCount > 0 && (
          <span
            className={overLimit ? "is-over-limit" : undefined}
            title="The main search counts as one"
          >
            {clauseCount}/{MAX_RECIPE_CLAUSES}
          </span>
        )}
      </div>

      <div className="match-tiles">
        {MATCH_FACETS.map((facet) => {
          const draft = drafts[facet];
          const canUse = canUseFacet(facet) && !overLimit;
          const isDragOver = dragFacet === facet;
          const tileClass = [
            "match-tile",
            draft ? "is-expanded" : "",
            draft?.kind === "source" ? "has-source" : "",
            isDragOver ? "is-drag-over" : "",
            !canUse && !matchDraftHasClause(draft) ? "is-disabled" : "",
          ]
            .filter(Boolean)
            .join(" ");

          return (
            <div
              key={facet}
              className={tileClass}
              onDragEnter={(event) => {
                if (!event.dataTransfer.types.includes(SCENE_SOURCE_MIME)) return;
                event.preventDefault();
                setDragFacet(facet);
              }}
              onDragOver={(event) => {
                if (!event.dataTransfer.types.includes(SCENE_SOURCE_MIME)) return;
                event.preventDefault();
                event.dataTransfer.dropEffect = canUse ? "copy" : "none";
              }}
              onDragLeave={(event) => {
                if (
                  !event.currentTarget.contains(event.relatedTarget as Node | null)
                ) {
                  setDragFacet((current) => (current === facet ? null : current));
                }
              }}
              onDrop={(event) => {
                event.preventDefault();
                setDragFacet(null);
                if (!canUse) {
                  onLimit();
                  return;
                }
                const source = readSceneSourceDrag(event.dataTransfer, facet);
                if (source) onSource(facet, source);
              }}
            >
              {draft?.kind === "text" ? (
                <>
                  <div className="match-tile-header">
                    <span>
                      <FacetIcon facet={facet} />
                      {FACET_LABELS[facet]}
                    </span>
                    <button
                      type="button"
                      onClick={() => onRemove(facet)}
                      aria-label={`Remove ${FACET_LABELS[facet]} match`}
                      title="Remove"
                    >
                      ×
                    </button>
                  </div>
                  <input
                    type="text"
                    value={draft.text}
                    maxLength={500}
                    autoFocus
                    disabled={!canUse}
                    placeholder={FACET_HELP[facet].placeholder}
                    aria-label={`${FACET_LABELS[facet]} match`}
                    onChange={(event) =>
                      onTextChange(draft.facet, event.target.value)
                    }
                    onKeyDown={(event) => {
                      if (event.key === "Escape" && !draft.text.trim()) {
                        onRemove(facet);
                      }
                    }}
                  />
                </>
              ) : draft?.kind === "source" ? (
                <>
                  {draft.display?.keyframeUrl && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={`${API_URL}${draft.display.keyframeUrl}`}
                      alt=""
                      draggable={false}
                    />
                  )}
                  <span className="match-source-copy">
                    <span>
                      <FacetIcon facet={facet} />
                      {FACET_LABELS[facet]}
                    </span>
                    <strong>{sourceLabel(draft)}</strong>
                    <small>{sourceDetail(draft)}</small>
                  </span>
                  <button
                    type="button"
                    className="match-source-remove"
                    onClick={() => onRemove(facet)}
                    aria-label={`Remove ${FACET_LABELS[facet]} source`}
                    title="Remove"
                  >
                    ×
                  </button>
                </>
              ) : facet === "composition" ? (
                <div
                  className="match-tile-empty"
                  title="Drop a scene here or choose Use in search on a result"
                >
                  <FacetIcon facet={facet} />
                  <span>{FACET_LABELS[facet]}</span>
                  <small>Drop scene</small>
                </div>
              ) : (
                <button
                  type="button"
                  className="match-tile-empty"
                  disabled={!canUse}
                  title={FACET_HELP[facet].description}
                  onClick={() => {
                    if (!canUse) {
                      onLimit();
                      return;
                    }
                    onActivateText(facet as TextMatchFacet);
                  }}
                >
                  <FacetIcon facet={facet} />
                  <span>{FACET_LABELS[facet]}</span>
                  <small>{FACET_HELP[facet].description}</small>
                </button>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
