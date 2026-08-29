"use client";

import { useEffect, useState, type DragEvent } from "react";
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

const FACET_PLACEHOLDERS: Record<TextMatchFacet, string> = {
  scene: "Describe what happens…",
  words: "Type dialogue or visible text…",
  look: "Describe objects, color, or light…",
  mood: "Describe the feeling…",
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
  targetFacet?: RecipeMatchFacet;
}

function SourceReferenceIcon() {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <circle cx="9" cy="10" r="1.5" />
      <path d="m4 17 4.5-4 3.5 3 2.5-2 5.5 4.5" />
    </svg>
  );
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
    if (!editingFacet || drafts[editingFacet]?.kind === "text") return;
    setEditingFacet(null);
  }, [drafts, editingFacet]);

  const canUseFacet = (facet: RecipeMatchFacet) =>
    matchDraftHasClause(drafts[facet]) ||
    (!overLimit && clauseCount < MAX_RECIPE_CLAUSES);

  const canAcceptDrop = (facet: RecipeMatchFacet) => {
    if (dragSourceFacet === facet) return false;
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
    const draft = drafts[facet];
    if (draft?.kind === "text" && !draft.text.trim()) onRemove?.(facet);
    onBrowse?.(facet);
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
          const hasClause = matchDraftHasClause(draft);
          const canUse = canUseFacet(facet);
          const canDrop = canAcceptDrop(facet);
          const isDragOver = dragOverFacet === facet;
          const isEditing =
            draft?.kind === "text" && editingFacet === facet;
          const dropCopy = isDragOver
            ? draft && hasClause
              ? `Replace ${FACET_LABELS[facet]}`
              : dragSourceFacet
                ? `Move to ${FACET_LABELS[facet]}`
                : `Use for ${FACET_LABELS[facet]}`
            : null;
          const tileClass = [
            "match-tile",
            isEditing ? "is-editing" : "",
            draft?.kind === "text" && !isEditing ? "has-text" : "",
            draft?.kind === "source" ? "has-source" : "",
            dragSourceFacet && canDrop ? "is-drop-ready" : "",
            isDragOver && canDrop ? "is-drag-over" : "",
            dragSourceFacet === facet ? "is-dragging-source" : "",
            targetFacet === facet ? "is-reference-target" : "",
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
                <span>{FACET_LABELS[facet]}</span>
                <span className="match-tile-actions">
                  <button
                    type="button"
                    className="match-reference-button"
                    disabled={!canUse}
                    data-browse-facet={facet}
                    onClick={() => browse(facet)}
                    aria-label={`Use a scene for ${FACET_LABELS[facet]}`}
                    title={`Use a scene for ${FACET_LABELS[facet]}`}
                  >
                    <SourceReferenceIcon />
                  </button>
                  {hasClause && (
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
                </span>
              </div>

              <div className="match-tile-body">
                {dropCopy ? (
                  <span className="match-drop-copy">{dropCopy}</span>
                ) : draft?.kind === "text" ? (
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
                        placeholder={FACET_PLACEHOLDERS[draft.facet]}
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
                    </div>
                  ) : (
                    <button
                      type="button"
                      className="match-text-affordance has-value"
                      onClick={() => activateText(draft.facet)}
                      aria-label={`Edit ${FACET_LABELS[facet]} match: ${draft.text}`}
                    >
                      {draft.text}
                    </button>
                  )
                ) : draft?.kind === "source" ? (
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
                      <strong>{sourceLabel(draft)}</strong>
                      <small>{sourceDetail(draft)}</small>
                    </span>
                  </div>
                ) : facet === "composition" ? (
                  <button
                    type="button"
                    className="match-source-affordance"
                    disabled={!canUse}
                    onClick={() => browse(facet)}
                    aria-label="Choose or drop a scene for Framing"
                  >
                    Drop a scene here
                  </button>
                ) : (
                  <button
                    type="button"
                    className="match-text-affordance"
                    disabled={!canUse}
                    onClick={() => activateText(facet as TextMatchFacet)}
                    aria-label={`Describe ${FACET_LABELS[facet]}`}
                  >
                    {FACET_PLACEHOLDERS[facet as TextMatchFacet]}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
