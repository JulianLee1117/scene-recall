"use client";

import { useEffect, useRef, useState, type DragEvent } from "react";
import FacetIcon from "./FacetIcon";
import { formatTime } from "@/lib/format";
import { setNativeDragPreview } from "@/lib/nativeDragPreview";
import {
  FACET_LABELS,
  MATCH_FACETS,
  MAX_RECIPE_CLAUSES,
  SCENE_SOURCE_MIME,
  matchDraftHasClause,
  readFacetSourceDragOrigin,
  readSceneSourceDrag,
  writeFacetSourceDrag,
  type MatchDraft,
  type MatchDrafts,
  type RecipeImageInput,
  type TextMatchFacet,
} from "@/lib/searchRecipe";
import type {
  RecipeImageFacet,
  RecipeMatchFacet,
  ResolvedSourceEvidence,
} from "@/types/api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";
const IMAGE_SOURCE_MIME = "application/x-scene-recall-image-source";
const IMAGE_FACETS: readonly RecipeImageFacet[] = ["look", "composition"];

const FACET_PLACEHOLDERS: Record<TextMatchFacet, string> = {
  scene: "People, objects, actions, setting…",
  words: "Spoken or visible text…",
  look: "Color, light, texture, style…",
  mood: "Feeling or energy…",
};

interface MatchByRailProps {
  clauseCount: number;
  drafts: MatchDrafts;
  image?: RecipeImageInput | null;
  sourceEvidence?: Partial<Record<RecipeMatchFacet, ResolvedSourceEvidence>>;
  debug?: boolean;
  onActivateText?: (facet: TextMatchFacet) => void;
  onTextChange?: (facet: TextMatchFacet, text: string) => void;
  onCommitText?: (facet: TextMatchFacet, text: string) => void;
  onRemove?: (facet: RecipeMatchFacet) => void;
  onBrowse?: (facet: RecipeMatchFacet) => void;
  onSource?: (
    facet: RecipeMatchFacet,
    draft: MatchDraft,
    originFacet?: RecipeMatchFacet,
  ) => void;
  onImageFile?: (file: File, facet: RecipeImageFacet) => void;
  onMoveImage?: (facet: RecipeImageFacet) => void;
  onRemoveImage?: () => void;
  onLimit?: () => void;
  targetFacet?: RecipeMatchFacet;
}

interface SourceInputCopy {
  text: string;
  adapter: string;
  debugDetail?: string;
}

const SOURCE_TEXT_VIEW_LABELS: Record<string, string> = {
  caption: "Scene description",
  dialogue: "Dialogue",
  ocr: "On-screen text",
  facets: "Scene detail",
  mood: "Mood",
};

function sourceEvidenceMatches(
  draft: Extract<MatchDraft, { kind: "source" }>,
  evidence: ResolvedSourceEvidence | undefined,
): evidence is ResolvedSourceEvidence {
  if (!evidence || !("unit_id" in evidence.source)) return false;
  return (
    evidence.source.unit_id === draft.source.unit_id &&
    evidence.source.frame_index === draft.source.frame_index
  );
}

function sourceInputCopy(
  evidence: ResolvedSourceEvidence | undefined,
): SourceInputCopy | null {
  const text = evidence?.effective_text?.trim();
  if (!text || !evidence) return null;
  const adapter =
    evidence.adapter === "dialogue+ocr"
      ? "dialogue + visible text"
      : evidence.adapter === "caption"
        ? "generated scene description"
        : evidence.adapter === "mood"
          ? "mood + energy"
          : evidence.adapter;
  const debugDetail = evidence.evidence
    .filter((item) => item.type === "text")
    .map(
      (item) =>
        `${SOURCE_TEXT_VIEW_LABELS[item.view] ?? item.view}: ${item.text}`,
    )
    .join(" · ");
  return { text, adapter, debugDetail: debugDetail || undefined };
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
      <rect x="3" y="6" width="13" height="12" rx="2" />
      <path d="M7 6V4M12 6V4M7 18v2M12 18v2" />
      <circle cx="18" cy="16" r="3" />
      <path d="m20.2 18.2 2 2" />
    </svg>
  );
}

function SourceInfoIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6" />
      <path d="M12 7h.01" />
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

function containsFile(transfer: DataTransfer): boolean {
  return transfer.types.includes("Files");
}

function containsImageSource(transfer: DataTransfer): boolean {
  return transfer.types.includes(IMAGE_SOURCE_MIME);
}

function isImageFacet(facet: RecipeMatchFacet): facet is RecipeImageFacet {
  return IMAGE_FACETS.includes(facet as RecipeImageFacet);
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
  clauseCount,
  drafts,
  image,
  sourceEvidence = {},
  debug = false,
  onActivateText,
  onTextChange,
  onCommitText,
  onRemove,
  onBrowse,
  onSource,
  onImageFile,
  onMoveImage,
  onRemoveImage,
  onLimit,
  targetFacet,
}: MatchByRailProps) {
  const [editingFacet, setEditingFacet] =
    useState<TextMatchFacet | null>(null);
  const editingInitialTextRef = useRef("");
  const closingFacetRef = useRef<TextMatchFacet | null>(null);
  const [dragOverFacet, setDragOverFacet] =
    useState<RecipeMatchFacet | null>(null);
  const [dragSourceFacet, setDragSourceFacet] =
    useState<RecipeMatchFacet | null>(null);
  const [dragImageFacet, setDragImageFacet] =
    useState<RecipeImageFacet | null>(null);
  const [pageSceneDragActive, setPageSceneDragActive] = useState(false);
  const [inspectingFacet, setInspectingFacet] =
    useState<RecipeMatchFacet | null>(null);
  const overLimit = clauseCount > MAX_RECIPE_CLAUSES;

  useEffect(() => {
    if (!editingFacet || drafts[editingFacet]?.kind === "text") return;
    setEditingFacet(null);
  }, [drafts, editingFacet]);

  useEffect(() => {
    if (!inspectingFacet || drafts[inspectingFacet]?.kind === "source") return;
    setInspectingFacet(null);
  }, [drafts, inspectingFacet]);

  const canUseFacet = (facet: RecipeMatchFacet) =>
    image?.facet === facet ||
    matchDraftHasClause(drafts[facet]) ||
    (!overLimit && clauseCount < MAX_RECIPE_CLAUSES);

  const canAcceptSceneDrop = (facet: RecipeMatchFacet) => {
    if (dragSourceFacet === facet) return false;
    if (dragSourceFacet) return true;
    return canUseFacet(facet);
  };

  const canAcceptImageDrop = (facet: RecipeMatchFacet) => {
    if (targetFacet) return false;
    if (!isImageFacet(facet)) return false;
    if (dragImageFacet === facet) return false;
    if (image || matchDraftHasClause(drafts[facet])) return true;
    return !overLimit && clauseCount < MAX_RECIPE_CLAUSES;
  };

  const clearDragState = () => {
    setDragOverFacet(null);
    setDragSourceFacet(null);
    setDragImageFacet(null);
    setPageSceneDragActive(false);
  };

  useEffect(() => {
    const handleDocumentDragStart = (event: globalThis.DragEvent) => {
      if (event.dataTransfer && containsSceneSource(event.dataTransfer)) {
        setPageSceneDragActive(true);
      }
    };
    const handleDocumentDragFinish = () => {
      setPageSceneDragActive(false);
      setDragOverFacet(null);
    };

    document.addEventListener("dragstart", handleDocumentDragStart);
    document.addEventListener("dragend", handleDocumentDragFinish);
    document.addEventListener("drop", handleDocumentDragFinish);
    return () => {
      document.removeEventListener("dragstart", handleDocumentDragStart);
      document.removeEventListener("dragend", handleDocumentDragFinish);
      document.removeEventListener("drop", handleDocumentDragFinish);
    };
  }, []);

  useEffect(() => {
    const outsideTiles = (target: EventTarget | null) =>
      !(target instanceof Element && target.closest(".match-tile"));
    const handleDocumentDragOver = (event: globalThis.DragEvent) => {
      if (!outsideTiles(event.target) || !event.dataTransfer) return;
      const movingScene = containsSceneSource(event.dataTransfer);
      const movingImage = containsImageSource(event.dataTransfer);
      if (!movingScene && !movingImage) return;
      event.preventDefault();
      event.dataTransfer.dropEffect =
        event.dataTransfer.effectAllowed === "move" ? "move" : "copy";
    };
    const handleDocumentDrop = (event: globalThis.DragEvent) => {
      if (!outsideTiles(event.target) || !event.dataTransfer) return;
      const movingScene = containsSceneSource(event.dataTransfer);
      const movingImage = containsImageSource(event.dataTransfer);
      if (!movingScene && !movingImage) return;
      event.preventDefault();
      if (movingImage) {
        onRemoveImage?.();
      } else {
        const originFacet = readFacetSourceDragOrigin(event.dataTransfer);
        if (originFacet) onRemove?.(originFacet);
      }
      setDragOverFacet(null);
      setDragSourceFacet(null);
      setDragImageFacet(null);
    };

    document.addEventListener("dragover", handleDocumentDragOver);
    document.addEventListener("drop", handleDocumentDrop);
    return () => {
      document.removeEventListener("dragover", handleDocumentDragOver);
      document.removeEventListener("drop", handleDocumentDrop);
    };
  }, [onRemove, onRemoveImage]);

  const activateText = (facet: TextMatchFacet) => {
    if (!canUseFacet(facet)) {
      onLimit?.();
      return;
    }
    const draft = drafts[facet];
    editingInitialTextRef.current = draft?.kind === "text" ? draft.text : "";
    closingFacetRef.current = null;
    setEditingFacet(facet);
    onActivateText?.(facet);
  };

  const finishTextEdit = (
    facet: TextMatchFacet,
    text: string,
    action: "blur" | "commit" | "cancel",
  ) => {
    const initialText = editingInitialTextRef.current;
    closingFacetRef.current = facet;
    setEditingFacet(null);

    if (action === "cancel") {
      if (text !== initialText) onCommitText?.(facet, initialText);
      else if (!initialText.trim()) onRemove?.(facet);
      return;
    }
    if (action === "blur" && text === initialText) {
      if (!text.trim()) onRemove?.(facet);
      return;
    }
    onCommitText?.(facet, text);
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

  const sceneDragActive = pageSceneDragActive || Boolean(dragSourceFacet);
  const railDragActive = sceneDragActive || Boolean(dragImageFacet);

  return (
    <section
      className={`match-rail${railDragActive ? " is-drag-active" : ""}`}
      aria-labelledby="match-rail-label"
    >
      <div className="match-rail-heading">
        <span id="match-rail-label">Match by</span>
      </div>

      <div className="match-tiles">
        {MATCH_FACETS.map((facet) => {
          const draft = drafts[facet];
          const hasImage = image?.facet === facet;
          const hasClause = hasImage || matchDraftHasClause(draft);
          const canUse = canUseFacet(facet);
          const canDropScene = canAcceptSceneDrop(facet);
          const canDropImage = canAcceptImageDrop(facet);
          const canDropActiveSource = dragImageFacet
            ? canDropImage
            : sceneDragActive
              ? canDropScene
              : false;
          const isDragOver = dragOverFacet === facet;
          const isEditing =
            draft?.kind === "text" && editingFacet === facet;
          const resolvedSourceEvidence =
            draft?.kind === "source" &&
            sourceEvidenceMatches(draft, sourceEvidence[facet])
              ? sourceEvidence[facet]
              : undefined;
          const sourceInput = sourceInputCopy(resolvedSourceEvidence);
          const sourceEvidenceId = `source-input-${facet}`;
          const dropCopy = isDragOver && (canDropScene || canDropImage)
            ? hasClause
              ? `Replace ${FACET_LABELS[facet]}`
              : dragSourceFacet || dragImageFacet
                ? `Move to ${FACET_LABELS[facet]}`
                : `Use for ${FACET_LABELS[facet]}`
            : null;
          const tileClass = [
            "match-tile",
            isEditing ? "is-editing" : "",
            draft?.kind === "text" && !isEditing ? "has-text" : "",
            draft?.kind === "source" ? "has-source" : "",
            hasImage ? "has-image" : "",
            railDragActive && canDropActiveSource
              ? "is-drop-ready"
              : "",
            isDragOver && (canDropScene || canDropImage)
              ? "is-drag-over"
              : "",
            dragSourceFacet === facet ? "is-dragging-source" : "",
            dragImageFacet === facet ? "is-dragging-source" : "",
            targetFacet === facet ? "is-reference-target" : "",
            !canUse &&
            !hasClause &&
            !(
              railDragActive && canDropActiveSource
            )
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
                const acceptsScene =
                  containsSceneSource(event.dataTransfer) && canDropScene;
                const acceptsImage =
                  (containsFile(event.dataTransfer) ||
                    containsImageSource(event.dataTransfer)) &&
                  canDropImage;
                if (acceptsScene || acceptsImage) event.preventDefault();
              }}
              onDragOver={(event) => {
                const movingImage = containsImageSource(event.dataTransfer);
                const droppingFile = containsFile(event.dataTransfer);
                const droppingScene = containsSceneSource(event.dataTransfer);
                if (!movingImage && !droppingFile && !droppingScene) return;
                const allowed =
                  movingImage || droppingFile
                    ? canAcceptImageDrop(facet)
                    : canAcceptSceneDrop(facet);
                event.dataTransfer.dropEffect = allowed
                  ? dragSourceFacet || movingImage
                    ? "move"
                    : "copy"
                  : "none";
                if (!allowed) {
                  setDragOverFacet(null);
                  return;
                }
                event.preventDefault();
                event.stopPropagation();
                setDragOverFacet((current) =>
                  current === facet ? current : facet,
                );
              }}
              onDragLeave={(event) => {
                if (!dragIsOutside(event)) return;
                setDragOverFacet((current) =>
                  current === facet ? null : current,
                );
              }}
              onDrop={(event) => {
                if (containsFile(event.dataTransfer)) {
                  event.preventDefault();
                  event.stopPropagation();
                  const allowed = canAcceptImageDrop(facet);
                  const file = allowed ? event.dataTransfer.files.item(0) : null;
                  clearDragState();
                  if (!allowed) {
                    if (isImageFacet(facet) && !targetFacet) onLimit?.();
                    return;
                  }
                  if (file && isImageFacet(facet)) onImageFile?.(file, facet);
                  return;
                }
                if (containsImageSource(event.dataTransfer)) {
                  event.preventDefault();
                  event.stopPropagation();
                  const allowed = canAcceptImageDrop(facet);
                  clearDragState();
                  if (!allowed) return;
                  if (isImageFacet(facet)) onMoveImage?.(facet);
                  return;
                }
                if (!containsSceneSource(event.dataTransfer)) return;
                event.preventDefault();
                event.stopPropagation();
                const originFacet =
                  readFacetSourceDragOrigin(event.dataTransfer) ??
                  dragSourceFacet ??
                  undefined;
                const allowed =
                  originFacet === facet
                    ? false
                    : originFacet
                      ? true
                      : canAcceptSceneDrop(facet);
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
                <span className="match-tile-label">
                  <FacetIcon facet={facet} size={14} />
                  <span>{FACET_LABELS[facet]}</span>
                </span>
                <span className="match-tile-actions">
                  <button
                    type="button"
                    className="match-reference-button"
                    disabled={!canUse}
                    data-browse-facet={facet}
                    onClick={() => browse(facet)}
                    aria-label={`Find a scene for ${FACET_LABELS[facet]}`}
                    title={`Find a scene for ${FACET_LABELS[facet]}`}
                  >
                    <SourceReferenceIcon />
                  </button>
                  {hasClause && (
                    <button
                      type="button"
                      className="match-inline-remove"
                      onClick={() =>
                        hasImage ? onRemoveImage?.() : onRemove?.(facet)
                      }
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
                ) : hasImage && image ? (
                  <div
                    className="match-source-drag match-image-source has-thumbnail"
                    data-source-facet={facet}
                    title="Drag between Look and Framing; drop outside to remove"
                  >
                    <div
                      className="match-source-drag-handle"
                      draggable
                      onDragStart={(event) => {
                        event.dataTransfer.effectAllowed = "move";
                        event.dataTransfer.setData(IMAGE_SOURCE_MIME, facet);
                        setNativeDragPreview(event.dataTransfer, {
                          eyebrow: FACET_LABELS[facet],
                          title: image.display.label,
                          detail:
                            image.facet === "look"
                              ? "Appearance reference"
                              : "Layout reference",
                          imageUrl: image.display.previewUrl,
                        });
                        setInspectingFacet(null);
                        setDragImageFacet(image.facet);
                        setDragOverFacet(null);
                      }}
                      onDragEnd={clearDragState}
                    >
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img
                        src={image.display.previewUrl}
                        alt=""
                        draggable={false}
                      />
                      <span className="match-source-copy">
                        <strong title={image.display.label}>
                          {image.display.label}
                        </strong>
                        <small>
                          {image.facet === "look"
                            ? "Appearance reference"
                            : "Layout reference"}
                        </small>
                      </span>
                    </div>
                  </div>
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
                        if (closingFacetRef.current === facet) {
                          closingFacetRef.current = null;
                          return;
                        }
                        finishTextEdit(draft.facet, draft.text, "blur");
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
                            finishTextEdit(draft.facet, draft.text, "cancel");
                          } else if (event.key === "Enter") {
                            event.preventDefault();
                            finishTextEdit(draft.facet, draft.text, "commit");
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
                    className={`match-source-drag${
                      draft.display?.keyframeUrl ? " has-thumbnail" : ""
                    }`}
                    role="group"
                    data-source-facet={facet}
                    aria-label={`${FACET_LABELS[facet]} scene source: ${sourceLabel(draft)}. Drag to move it, or drop it outside the categories to remove it.`}
                    aria-describedby={sourceInput ? sourceEvidenceId : undefined}
                    title="Drag to move; drop outside the categories to remove"
                    onBlur={(event) => {
                      if (
                        !event.currentTarget.contains(
                          event.relatedTarget as Node | null,
                        )
                      ) {
                        setInspectingFacet((current) =>
                          current === facet ? null : current,
                        );
                      }
                    }}
                    onKeyDown={(event) => {
                      if (event.key === "Escape") setInspectingFacet(null);
                    }}
                  >
                    <div
                      className="match-source-drag-handle"
                      draggable
                      onDragStart={(event) => {
                        if (
                          !writeFacetSourceDrag(
                            event.dataTransfer,
                            draft,
                            facet,
                          )
                        ) {
                          event.preventDefault();
                          return;
                        }
                        setNativeDragPreview(event.dataTransfer, {
                          eyebrow: FACET_LABELS[facet],
                          title: sourceLabel(draft),
                          detail: sourceDetail(draft),
                          imageUrl: draft.display?.keyframeUrl
                            ? `${API_URL}${draft.display.keyframeUrl}`
                            : undefined,
                        });
                        setInspectingFacet(null);
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
                        <strong title={sourceLabel(draft)}>
                          {sourceLabel(draft)}
                        </strong>
                        <small>{sourceDetail(draft)}</small>
                      </span>
                    </div>
                    {sourceInput && (
                      <>
                        <span className="match-source-tools">
                          <button
                            type="button"
                            className="match-source-info"
                            aria-label={`Inspect ${FACET_LABELS[facet]} source input`}
                            aria-describedby={sourceEvidenceId}
                            title={sourceInput.text}
                            onClick={() =>
                              setInspectingFacet((current) =>
                                current === facet ? null : facet,
                              )
                            }
                          >
                            <SourceInfoIcon />
                          </button>
                        </span>
                        <span
                          className={`match-source-input${
                            inspectingFacet === facet ? " is-open" : ""
                          }`}
                          id={sourceEvidenceId}
                          role="tooltip"
                          title={sourceInput.text}
                        >
                          <span>{sourceInput.text}</span>
                          {debug && (
                            <small>Input · {sourceInput.adapter}</small>
                          )}
                          {debug && sourceInput.debugDetail && (
                            <small title={sourceInput.debugDetail}>
                              {sourceInput.debugDetail}
                            </small>
                          )}
                        </span>
                      </>
                    )}
                  </div>
                ) : facet === "composition" ? (
                  <button
                    type="button"
                    className="match-source-affordance"
                    disabled={!canUse}
                    onClick={() => browse(facet)}
                    aria-label="Choose a scene or drop an image for Framing"
                  >
                    Drop a scene or image here
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
