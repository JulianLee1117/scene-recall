"use client";

import { useEffect, useState, type DragEvent } from "react";
import FacetIcon from "./FacetIcon";
import { formatTime } from "@/lib/format";
import {
  FACET_LABELS,
  MATCH_FACETS,
  MAX_RECIPE_CLAUSES,
  SCENE_SOURCE_MIME,
  matchDraftHasClause,
  readFacetSourceDragOrigin,
  readSceneSourceDrag,
  recipeClauseCount,
  writeFacetSourceDrag,
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
    placeholder: "what happens\u2026",
  },
  words: {
    description: "Dialogue or on-screen words",
    placeholder: "words you remember\u2026",
  },
  look: {
    description: "Objects, color, and light",
    placeholder: "visual details\u2026",
  },
  composition: {
    description: "Framing and arrangement",
  },
  mood: {
    description: "Feeling and atmosphere",
    placeholder: "the feeling\u2026",
  },
};

interface MatchByRailProps {
  mainText: string;
  drafts: MatchDrafts;
  onActivateText: (facet: TextMatchFacet) => void;
  onTextChange: (facet: TextMatchFacet, text: string) => void;
  onRemove: (facet: RecipeMatchFacet) => void;
  onSource: (
    facet: RecipeMatchFacet,
    draft: MatchDraft,
    originFacet?: RecipeMatchFacet,
  ) => void;
  onLimit: () => void;
}

function sourceLabel(draft: MatchDraft): string {
  if (draft.kind !== "source") return "";
  return draft.display?.filmTitle || `Scene \u2026${draft.source.unit_id.slice(-8)}`;
}

function sourceDetail(draft: MatchDraft): string {
  if (draft.kind !== "source") return "";
  if (typeof draft.display?.timestamp === "number") {
    return formatTime(draft.display.timestamp);
  }
  return `frame ${(draft.source.frame_index ?? 0) + 1}`;
}

function containsSceneSource(transfer: DataTransfer): boolean {
  return transfer.types.includes(SCENE_SOURCE_MIME);
}

function dragIsOutside(event: DragEvent<HTMLDivElement>): boolean {
  const bounds = event.currentTarget.getBoundingClientRect();
  return (
    event.clientX <= bounds.left ||
    event.clientX >= bounds.right ||
    event.clientY <= bounds.top ||
    event.clientY >= bounds.bottom
  );
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
  const [editingFacet, setEditingFacet] =
    useState<TextMatchFacet | null>(null);
  const [dragOverFacet, setDragOverFacet] =
    useState<RecipeMatchFacet | null>(null);
  const [dragSourceFacet, setDragSourceFacet] =
    useState<RecipeMatchFacet | null>(null);
  const clauseCount = recipeClauseCount(mainText, drafts);
  const overLimit = clauseCount > MAX_RECIPE_CLAUSES;

  useEffect(() => {
    if (!editingFacet || drafts[editingFacet]?.kind === "text") return;
    setEditingFacet(null);
  }, [drafts, editingFacet]);

  const canUseFacet = (facet: RecipeMatchFacet) =>
    matchDraftHasClause(drafts[facet]) || clauseCount < MAX_RECIPE_CLAUSES;

  const canAcceptDrop = (facet: RecipeMatchFacet) => {
    if (dragSourceFacet === facet) return false;
    if (dragSourceFacet) return true;
    return canUseFacet(facet) && !overLimit;
  };

  const clearDragState = () => {
    setDragOverFacet(null);
    setDragSourceFacet(null);
  };

  return (
    <section
      className={`match-rail${dragSourceFacet ? " is-moving-source" : ""}`}
      aria-labelledby="match-rail-label"
    >
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
          const canDrop = canAcceptDrop(facet);
          const isDragOver = dragOverFacet === facet;
          const isEditing = draft?.kind === "text" && editingFacet === facet;
          const tileClass = [
            "match-tile",
            isEditing ? "is-editing" : "",
            draft?.kind === "text" && !isEditing ? "has-text" : "",
            draft?.kind === "source" ? "has-source" : "",
            dragSourceFacet && canDrop ? "is-drop-ready" : "",
            isDragOver && canDrop ? "is-drag-over" : "",
            dragSourceFacet === facet ? "is-dragging-source" : "",
            !canUse &&
            !matchDraftHasClause(draft) &&
            !(dragSourceFacet && canDrop)
              ? "is-disabled"
              : "",
          ]
            .filter(Boolean)
            .join(" ");

          return (
            <div
              key={facet}
              className={tileClass}
              onDragEnter={(event) => {
                if (!containsSceneSource(event.dataTransfer)) return;
                event.preventDefault();
              }}
              onDragOver={(event) => {
                if (!containsSceneSource(event.dataTransfer)) return;
                event.preventDefault();
                const allowed = canAcceptDrop(facet);
                event.dataTransfer.dropEffect = allowed
                  ? dragSourceFacet
                    ? "move"
                    : "copy"
                  : "none";
                setDragOverFacet((current) =>
                  current === facet ? current : facet,
                );
              }}
              onDragLeave={(event) => {
                if (dragIsOutside(event)) {
                  setDragOverFacet((current) =>
                    current === facet ? null : current,
                  );
                }
              }}
              onDrop={(event) => {
                event.preventDefault();
                const originFacet =
                  readFacetSourceDragOrigin(event.dataTransfer) ??
                  dragSourceFacet ??
                  undefined;
                const allowed =
                  originFacet === facet
                    ? false
                    : originFacet
                      ? true
                      : canUseFacet(facet) && !overLimit;
                const source = allowed
                  ? readSceneSourceDrag(event.dataTransfer, facet)
                  : null;
                clearDragState();
                if (!allowed) {
                  if (originFacet !== facet) onLimit();
                  return;
                }
                if (source) onSource(facet, source, originFacet);
              }}
            >
              {draft?.kind === "text" ? (
                isEditing ? (
                  <div
                    className="match-text-editor"
                    onBlur={(event) => {
                      if (
                        event.currentTarget.contains(
                          event.relatedTarget as Node | null,
                        )
                      ) {
                        return;
                      }
                      setEditingFacet(null);
                      if (!draft.text.trim()) onRemove(facet);
                    }}
                  >
                    <span className="match-text-label">
                      <FacetIcon facet={facet} />
                      {FACET_LABELS[facet]}
                    </span>
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
                        if (event.key === "Escape") {
                          event.preventDefault();
                          setEditingFacet(null);
                          if (!draft.text.trim()) onRemove(facet);
                        } else if (event.key === "Enter") {
                          setEditingFacet(null);
                        }
                      }}
                    />
                    <button
                      type="button"
                      className="match-inline-remove"
                      onClick={() => onRemove(facet)}
                      aria-label={`Remove ${FACET_LABELS[facet]} match`}
                      title="Remove"
                    >
                      {"\u00d7"}
                    </button>
                  </div>
                ) : (
                  <div className="match-text-compact">
                    <button
                      type="button"
                      className="match-text-edit"
                      onClick={() => setEditingFacet(draft.facet)}
                      aria-label={`Edit ${FACET_LABELS[facet]} match: ${draft.text}`}
                      title={`Edit ${FACET_LABELS[facet]} match`}
                    >
                      <span className="match-text-label">
                        <FacetIcon facet={facet} />
                        {FACET_LABELS[facet]}
                      </span>
                      <strong>{draft.text}</strong>
                    </button>
                    <button
                      type="button"
                      className="match-inline-remove"
                      onClick={() => onRemove(facet)}
                      aria-label={`Remove ${FACET_LABELS[facet]} match`}
                      title="Remove"
                    >
                      {"\u00d7"}
                    </button>
                  </div>
                )
              ) : draft?.kind === "source" ? (
                <>
                  <div
                    className={`match-source-drag${draft.display?.keyframeUrl ? " has-thumbnail" : ""}`}
                    draggable
                    role="group"
                    aria-label={`${FACET_LABELS[facet]} scene source: ${sourceLabel(draft)}. Drag to move it to another category.`}
                    title="Drag to move this scene"
                    onDragStart={(event) => {
                      if (
                        !writeFacetSourceDrag(event.dataTransfer, draft, facet)
                      ) {
                        event.preventDefault();
                        return;
                      }
                      setDragSourceFacet(facet);
                      setDragOverFacet(null);
                    }}
                    onDragEnd={clearDragState}
                  >
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
                  </div>
                  <button
                    type="button"
                    className="match-source-remove"
                    onClick={() => onRemove(facet)}
                    aria-label={`Remove ${FACET_LABELS[facet]} source`}
                    title="Remove"
                  >
                    {"\u00d7"}
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
                    const textFacet = facet as TextMatchFacet;
                    setEditingFacet(textFacet);
                    onActivateText(textFacet);
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
