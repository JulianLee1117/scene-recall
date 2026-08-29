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
    placeholder: "Describe what happens…",
  },
  words: {
    description: "Dialogue or on-screen words",
    placeholder: "Enter words you remember…",
  },
  look: {
    description: "Objects, color, and light",
    placeholder: "Describe the visual details…",
  },
  composition: {
    description: "Framing and arrangement",
  },
  mood: {
    description: "Feeling and atmosphere",
    placeholder: "Describe the feeling…",
  },
};

interface MatchByRailProps {
  mainText: string;
  drafts: MatchDrafts;
  onActivateText?: (facet: TextMatchFacet) => void;
  onTextChange?: (facet: TextMatchFacet, text: string) => void;
  onSubmitText?: (facet: TextMatchFacet, text: string) => void;
  onRemove?: (facet: RecipeMatchFacet) => void;
  onBrowse?: (facet: RecipeMatchFacet) => void;
  onSource?: (
    facet: RecipeMatchFacet,
    draft: MatchDraft,
    originFacet?: RecipeMatchFacet,
  ) => void;
  onLimit?: () => void;
  frozen?: boolean;
  label?: string;
  targetFacet?: RecipeMatchFacet;
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
  return `Frame ${(draft.source.frame_index ?? 0) + 1}`;
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
  onSubmitText,
  onRemove,
  onBrowse,
  onSource,
  onLimit,
  frozen = false,
  label = "Match by",
  targetFacet,
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
    if (frozen && editingFacet) {
      setEditingFacet(null);
      return;
    }
    if (!editingFacet || drafts[editingFacet]?.kind === "text") return;
    setEditingFacet(null);
  }, [drafts, editingFacet, frozen]);

  const canUseFacet = (facet: RecipeMatchFacet) =>
    !frozen &&
    (matchDraftHasClause(drafts[facet]) ||
      (!overLimit && clauseCount < MAX_RECIPE_CLAUSES));

  const canAcceptDrop = (facet: RecipeMatchFacet) => {
    if (frozen || dragSourceFacet === facet) return false;
    if (dragSourceFacet) return true;
    return canUseFacet(facet);
  };

  const clearDragState = () => {
    setDragOverFacet(null);
    setDragSourceFacet(null);
  };

  const activateText = (facet: TextMatchFacet) => {
    if (!canUseFacet(facet)) {
      onLimit?.();
      return;
    }
    setEditingFacet(facet);
    onActivateText?.(facet);
  };

  const browse = (facet: RecipeMatchFacet) => {
    if (!canUseFacet(facet)) {
      onLimit?.();
      return;
    }
    setEditingFacet(null);
    onBrowse?.(facet);
  };

  return (
    <section
      className={`match-rail${dragSourceFacet ? " is-moving-source" : ""}${frozen ? " is-frozen" : ""}`}
      aria-labelledby="match-rail-label"
      aria-disabled={frozen || undefined}
    >
      <div className="match-rail-heading">
        <span id="match-rail-label">{label}</span>
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
          const hasClause = matchDraftHasClause(draft);
          const canUse = canUseFacet(facet);
          const canDrop = canAcceptDrop(facet);
          const isDragOver = dragOverFacet === facet;
          const isEditing =
            !frozen && draft?.kind === "text" && editingFacet === facet;
          const tileClass = [
            "match-tile",
            isEditing ? "is-editing" : "",
            draft?.kind === "text" && !isEditing ? "has-text" : "",
            draft?.kind === "source" ? "has-source" : "",
            dragSourceFacet && canDrop ? "is-drop-ready" : "",
            isDragOver && canDrop ? "is-drag-over" : "",
            dragSourceFacet === facet ? "is-dragging-source" : "",
            targetFacet === facet ? "is-picker-target" : "",
            !canUse && !hasClause && !(dragSourceFacet && canDrop)
              ? "is-disabled"
              : "",
          ]
            .filter(Boolean)
            .join(" ");

          return (
            <div
              key={facet}
              className={tileClass}
              data-facet={facet}
              onDragEnter={(event) => {
                if (frozen || !containsSceneSource(event.dataTransfer)) return;
                event.preventDefault();
              }}
              onDragOver={(event) => {
                if (frozen || !containsSceneSource(event.dataTransfer)) return;
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
                if (frozen) return;
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
                      : canUseFacet(facet);
                const source = allowed
                  ? readSceneSourceDrag(event.dataTransfer, facet)
                  : null;
                clearDragState();
                if (!allowed) {
                  if (originFacet !== facet) onLimit?.();
                  return;
                }
                if (source) onSource?.(facet, source, originFacet);
              }}
            >
              <div className="match-tile-header">
                <span>
                  <FacetIcon facet={facet} />
                  {FACET_LABELS[facet]}
                </span>
                {draft && !frozen && (
                  <button
                    type="button"
                    className="match-inline-remove"
                    onClick={() => onRemove?.(facet)}
                    aria-label={`Remove ${FACET_LABELS[facet]} match`}
                    title="Remove"
                  >
                    {"\u00d7"}
                  </button>
                )}
              </div>

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
                      if (!draft.text.trim()) onRemove?.(facet);
                    }}
                  >
                    <input
                      type="text"
                      value={draft.text}
                      maxLength={500}
                      autoFocus
                      placeholder={FACET_HELP[facet].placeholder}
                      aria-label={`${FACET_LABELS[facet]} match`}
                      onChange={(event) =>
                        onTextChange?.(draft.facet, event.target.value)
                      }
                      onKeyDown={(event) => {
                        if (event.key === "Escape") {
                          event.preventDefault();
                          setEditingFacet(null);
                          if (!draft.text.trim()) onRemove?.(facet);
                        } else if (event.key === "Enter") {
                          event.preventDefault();
                          setEditingFacet(null);
                          if (draft.text.trim()) {
                            onSubmitText?.(draft.facet, draft.text);
                          } else {
                            onRemove?.(facet);
                          }
                        }
                      }}
                    />
                    <div className="match-tile-footer">
                      <span>Enter to search</span>
                      <button
                        type="button"
                        data-browse-facet={facet}
                        aria-label={`Find a scene for ${FACET_LABELS[facet]}`}
                        onClick={() => {
                          if (!draft.text.trim()) onRemove?.(facet);
                          browse(facet);
                        }}
                      >
                        Find scene
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <button
                      type="button"
                      className="match-text-edit"
                      disabled={frozen}
                      onClick={() => activateText(draft.facet)}
                      aria-label={`Edit ${FACET_LABELS[facet]} match: ${draft.text}`}
                      title={`Edit ${FACET_LABELS[facet]} match`}
                    >
                      <strong>{draft.text || FACET_HELP[facet].description}</strong>
                    </button>
                    <div className="match-tile-footer">
                      <button
                        type="button"
                        disabled={frozen}
                        aria-label={`Edit ${FACET_LABELS[facet]} text`}
                        onClick={() => activateText(draft.facet)}
                      >
                        Edit text
                      </button>
                      <button
                        type="button"
                        disabled={frozen}
                        data-browse-facet={facet}
                        aria-label={`Find a scene for ${FACET_LABELS[facet]}`}
                        onClick={() => browse(facet)}
                      >
                        Find scene
                      </button>
                    </div>
                  </>
                )
              ) : draft?.kind === "source" ? (
                <>
                  <div
                    className={`match-source-drag${draft.display?.keyframeUrl ? " has-thumbnail" : ""}`}
                    draggable={!frozen}
                    role="group"
                    aria-label={`${FACET_LABELS[facet]} scene source: ${sourceLabel(draft)}${frozen ? "" : ". Drag to move it to another category."}`}
                    title={frozen ? undefined : "Drag to move this scene"}
                    onDragStart={(event) => {
                      if (
                        frozen ||
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
                      <strong>{sourceLabel(draft)}</strong>
                      <small>{sourceDetail(draft)}</small>
                    </span>
                  </div>
                  <div className="match-tile-footer">
                    <span>{frozen ? "Scene reference" : "Drag to move"}</span>
                    <button
                      type="button"
                      disabled={frozen}
                      data-browse-facet={facet}
                      aria-label={`Find another scene for ${FACET_LABELS[facet]}`}
                      onClick={() => browse(facet)}
                    >
                      Replace
                    </button>
                  </div>
                </>
              ) : facet === "composition" ? (
                <button
                  type="button"
                  className="match-empty-primary"
                  disabled={!canUse}
                  data-browse-facet={facet}
                  aria-label={`Find a scene for ${FACET_LABELS[facet]}`}
                  title="Find a scene or drop one here"
                  onClick={() => browse(facet)}
                >
                  <strong>Choose a scene</strong>
                  <small>or drop one here</small>
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    className="match-empty-primary"
                    disabled={!canUse}
                    aria-label={`Add ${FACET_LABELS[facet]} text`}
                    title={FACET_HELP[facet].description}
                    onClick={() => activateText(facet as TextMatchFacet)}
                  >
                    <strong>{FACET_HELP[facet].description}</strong>
                    <small>Describe it or use a scene</small>
                  </button>
                  <div className="match-tile-footer">
                    <button
                      type="button"
                      disabled={!canUse}
                      aria-label={`Add ${FACET_LABELS[facet]} text`}
                      onClick={() => activateText(facet as TextMatchFacet)}
                    >
                      Add text
                    </button>
                    <button
                      type="button"
                      disabled={!canUse}
                      data-browse-facet={facet}
                      aria-label={`Find a scene for ${FACET_LABELS[facet]}`}
                      onClick={() => browse(facet)}
                    >
                      Find scene
                    </button>
                  </div>
                </>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
