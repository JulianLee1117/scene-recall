"use client";
import {
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type DragEvent,
  type ReactNode,
} from "react";
import FacetIcon from "./FacetIcon";
import DirectionIcon from "./DirectionIcon";
import { formatTime } from "@/lib/format";
import { setNativeDragPreview } from "@/lib/nativeDragPreview";
import { referenceGateCopy, referenceReading, SEARCH_CLUE_COPY } from "@/lib/searchClues";
import {
  SCENE_POINTER_EVENT,
  useScenePointerDrag,
  type ScenePointerDetail,
} from "@/hooks/useScenePointerDrag";
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
const PLACEHOLDERS: Record<TextMatchFacet, string> = {
  scene: "A person running through a city…",
  words: "Something someone says…",
  look: "Blue light, warm grain, silhouettes…",
  mood: "Dreamlike, tense, joyful…",
};
interface MatchByRailProps {
  clauseCount: number;
  drafts: MatchDrafts;
  image?: RecipeImageInput | null;
  sourceEvidence?: Partial<Record<RecipeMatchFacet, ResolvedSourceEvidence>>;
  /** The Framing/Look shortlist note only makes sense once a description orders it. */
  hasMainText?: boolean;
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
  referencePicker?: ReactNode;
  referenceHasResults?: boolean;
  /** Beside Refine: what else narrows the search (the Filter control). */
  filter?: ReactNode;
  /** After the active refinements, in the same row: active filters. */
  activeExtra?: ReactNode;
  controls?: ReactNode;
  idleContent?: ReactNode;
  onCloseReference?: () => void;
}
function imageFacet(facet: RecipeMatchFacet): facet is RecipeImageFacet {
  return facet === "look" || facet === "composition";
}
interface ClueDragSource {
  draft: MatchDraft | undefined;
  image: boolean;
  facet: RecipeMatchFacet;
  title: string;
  thumbnail: string | null;
  /** A native drag that ended on no drop target, at the release point. */
  onDropNowhere?: (x: number, y: number) => void;
}
function useClueSourceDrag({
  draft,
  image,
  facet,
  title,
  thumbnail,
  onDropNowhere,
}: ClueDragSource) {
  const [dragging, setDragging] = useState(false);
  const source = !image && draft?.kind === "source" ? draft : null;
  const pointer = useScenePointerDrag(source, {
    originFacet: facet,
    onDragging: setDragging,
  });
  return {
    ...pointer,
    draggable: image || Boolean(source),
    "data-dragging": dragging || undefined,
    onDragStart(event: DragEvent<HTMLElement>) {
      pointer.onDragStart(event);
      if (event.defaultPrevented) return;
      if (image) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData(IMAGE_SOURCE_MIME, facet);
      } else if (
        !source ||
        !writeFacetSourceDrag(event.dataTransfer, source, facet)
      ) {
        event.preventDefault();
        return;
      }
      setNativeDragPreview(event.dataTransfer, {
        eyebrow: `Moving ${FACET_LABELS[facet]}`,
        title,
        detail: source && typeof source.display?.timestamp === "number"
          ? formatTime(source.display.timestamp)
          : undefined,
        imageUrl: thumbnail ?? undefined,
      });
      setDragging(true);
    },
    onDragEnd(event: DragEvent<HTMLElement>) {
      setDragging(false);
      if (event.dataTransfer?.dropEffect === "none") onDropNowhere?.(event.clientX, event.clientY);
    },
  };
}
function ClueChip({
  source,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { source: ClueDragSource }) {
  const drag = useClueSourceDrag(source);
  return <button {...props} {...drag} />;
}
function ClueSource(source: ClueDragSource) {
  const drag = useClueSourceDrag(source);
  const { draft, title, thumbnail } = source;
  return (
    <div
      className="clue-source"
      title="Drag to another category"
      {...drag}
    >
      {thumbnail && <img src={thumbnail} alt="" draggable={false} />}
      <span>
        <strong>{title}</strong>
        <small>
          {draft?.kind === "source" &&
          typeof draft.display?.timestamp === "number"
            ? formatTime(draft.display.timestamp)
            : "Image reference"}
        </small>
      </span>
    </div>
  );
}
export default function MatchByRail({
  clauseCount,
  drafts,
  image,
  sourceEvidence = {},
  hasMainText = false,
  onCommitText,
  onRemove,
  onBrowse,
  onSource,
  onImageFile,
  onMoveImage,
  onRemoveImage,
  onLimit,
  targetFacet,
  referencePicker,
  referenceHasResults = false,
  filter,
  activeExtra,
  controls,
  idleContent,
  onCloseReference,
}: MatchByRailProps) {
  const [refineOpen, setRefineOpen] = useState(false);
  const [editorFacet, setEditorFacet] = useState<RecipeMatchFacet | null>(null);
  const [editorText, setEditorText] = useState("");
  const [editorMode, setEditorMode] = useState<"text" | "reference">("text");
  const [aspectTarget, setAspectTarget] = useState<RecipeMatchFacet | null>(null);
  const railRef = useRef<HTMLElement>(null);
  const refineButtonRef = useRef<HTMLButtonElement>(null);
  const textRef = useRef<HTMLInputElement>(null);
  const aspectRef = useRef<HTMLSelectElement>(null);
  const aspectButtonRef = useRef<HTMLButtonElement>(null);
  const [dragActive, setDragActive] = useState(false);
  const [dragFloating, setDragFloating] = useState(false);
  const [dragKind, setDragKind] = useState<"scene" | "image">("scene");
  const [movingReference, setMovingReference] = useState(false);
  const [dragOver, setDragOver] = useState<RecipeMatchFacet | null>(null);
  // Dragging a reference out of the Refine area removes it, with an undo.
  const [dragOrigin, setDragOrigin] = useState<RecipeMatchFacet | null>(null);
  const [dragRemoving, setDragRemoving] = useState(false);
  const [removed, setRemoved] = useState<{ facet: RecipeMatchFacet; draft?: MatchDraft; file?: File } | null>(null);
  const panelId = useId();
  const openFacet = targetFacet ?? editorFacet;
  const panelOpen = refineOpen || Boolean(openFacet) || dragActive;
  const activeFacets = MATCH_FACETS.filter((facet) =>
    image?.facet === facet || matchDraftHasClause(drafts[facet]));
  const canUse = (facet: RecipeMatchFacet) =>
    Boolean(
      image?.facet === facet ||
      matchDraftHasClause(drafts[facet]) ||
      clauseCount < MAX_RECIPE_CLAUSES,
    );
  const outsideRail = (x: number, y: number) => {
    const bounds = railRef.current?.getBoundingClientRect();
    return !bounds || x < bounds.left || x > bounds.left + bounds.width || y < bounds.top || y > bounds.bottom;
  };
  const removeByDrag = (facet: RecipeMatchFacet) => {
    const removedImage = image?.facet === facet ? image : null;
    const draft = drafts[facet];
    if (!removedImage && draft?.kind !== "source") return;
    setRefineOpen(false);
    setEditorFacet(null);
    setAspectTarget(null);
    setRemoved({ facet, draft: removedImage ? undefined : draft, file: removedImage?.file });
    if (removedImage) onRemoveImage?.();
    else onRemove?.(facet);
  };
  const undoRemove = () => {
    if (!removed) return;
    setRemoved(null);
    if (removed.file && imageFacet(removed.facet)) onImageFile?.(removed.file, removed.facet);
    else if (removed.draft) onSource?.(removed.facet, removed.draft);
  };
  useEffect(() => {
    if (!removed) return;
    const timer = window.setTimeout(() => setRemoved(null), 8000);
    return () => window.clearTimeout(timer);
  }, [removed]);
  useEffect(() => {
    if (editorFacet && editorMode === "text") textRef.current?.focus();
  }, [editorFacet, editorMode]);
  useEffect(() => {
    if (aspectTarget) aspectRef.current?.focus();
  }, [aspectTarget]);
  useEffect(() => {
    if (!panelOpen) return;
    const dismiss = (event: PointerEvent) => {
      if (railRef.current?.contains(event.target as Node)) return;
      if (
        event.target instanceof Element &&
        event.target.closest(".modal-backdrop")
      )
        return;
      setRefineOpen(false);
      setEditorFacet(null);
      setAspectTarget(null);
      if (targetFacet) onCloseReference?.();
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, [panelOpen, targetFacet, onCloseReference]);
  useEffect(() => {
    const start = (event: globalThis.DragEvent) => {
      const bounds = railRef.current?.getBoundingClientRect();
      setDragFloating(
        Boolean(
          bounds && (bounds.top < 48 || bounds.bottom > window.innerHeight),
        ),
      );
      const types = event.dataTransfer?.types ?? [];
      if (types.includes(SCENE_SOURCE_MIME)) {
        setMovingReference(event.dataTransfer?.effectAllowed === "move");
        setDragKind("scene");
        setDragActive(true);
      } else if (types.includes("Files") || types.includes(IMAGE_SOURCE_MIME)) {
        setMovingReference(types.includes(IMAGE_SOURCE_MIME));
        setDragKind("image");
        setDragActive(true);
      }
    };
    const finish = () => {
      setDragActive(false);
      setDragOver(null);
    };
    const leave = (event: globalThis.DragEvent) => {
      if (
        !event.relatedTarget &&
        (event.clientX <= 0 ||
          event.clientY <= 0 ||
          event.clientX >= window.innerWidth ||
          event.clientY >= window.innerHeight)
      )
        finish();
    };
    document.addEventListener("dragstart", start);
    document.addEventListener("dragenter", start);
    document.addEventListener("dragleave", leave);
    document.addEventListener("dragend", finish);
    document.addEventListener("drop", finish);
    return () => {
      document.removeEventListener("dragstart", start);
      document.removeEventListener("dragenter", start);
      document.removeEventListener("dragleave", leave);
      document.removeEventListener("dragend", finish);
      document.removeEventListener("drop", finish);
    };
  }, []);
  useEffect(() => {
    const handlePointerScene = (event: Event) => {
      const detail = (event as CustomEvent<ScenePointerDetail>).detail;
      if (detail.phase === "end") {
        setDragActive(false);
        setDragOver(null);
        setDragOrigin(null);
        setDragRemoving(false);
        return;
      }
      if (detail.phase === "start") {
        const bounds = railRef.current?.getBoundingClientRect();
        setDragFloating(
          Boolean(
            bounds && (bounds.top < 48 || bounds.bottom > window.innerHeight),
          ),
        );
        setDragKind("scene");
        setMovingReference(Boolean(detail.originFacet));
        setDragOrigin(detail.originFacet ?? null);
        setDragActive(true);
        return;
      }
      const target = document
        .elementFromPoint(detail.x, detail.y)
        ?.closest<HTMLElement>(".clue-card");
      const facet = target?.dataset.facet as RecipeMatchFacet | undefined;
      const valid =
        facet && MATCH_FACETS.includes(facet) && facet !== detail.originFacet;
      const allowed = valid && (canUse(facet) || Boolean(detail.originFacet));
      const removing = Boolean(detail.originFacet) && !facet && outsideRail(detail.x, detail.y);
      if (detail.phase === "move") {
        setDragOver(allowed ? facet : null);
        setDragRemoving(removing);
        document.querySelector?.(".scene-pointer-ghost")?.classList.toggle("is-removing", removing);
        return;
      }
      setDragActive(false);
      setDragOver(null);
      setDragRemoving(false);
      if (allowed) {
        setRefineOpen(false);
        setEditorFacet(null);
        setAspectTarget(null);
        if (targetFacet) onCloseReference?.();
        onSource?.(facet, detail.draft, detail.originFacet);
      } else if (removing && detail.originFacet) removeByDrag(detail.originFacet);
      else if (valid) onLimit?.();
    };
    document.addEventListener(SCENE_POINTER_EVENT, handlePointerScene);
    return () =>
      document.removeEventListener(SCENE_POINTER_EVENT, handlePointerScene);
  }, [clauseCount, drafts, image, onSource, onLimit, onRemove, onRemoveImage, targetFacet, onCloseReference]);
  const accepts = (facet: RecipeMatchFacet, transfer: DataTransfer) => {
    if (transfer.types.includes(SCENE_SOURCE_MIME))
      return canUse(facet) || transfer.effectAllowed === "move";
    return (
      imageFacet(facet) &&
      (transfer.types.includes(IMAGE_SOURCE_MIME) ||
        (!targetFacet && transfer.types.includes("Files"))) &&
      (canUse(facet) || Boolean(image))
    );
  };
  const drop = (event: DragEvent<HTMLDivElement>, facet: RecipeMatchFacet) => {
    event.preventDefault();
    event.stopPropagation();
    const transfer = event.dataTransfer;
    setDragActive(false);
    setDragOver(null);
    if (!accepts(facet, transfer)) {
      onLimit?.();
      return;
    }
    if (transfer.types.includes(SCENE_SOURCE_MIME)) {
      const source = readSceneSourceDrag(transfer, facet);
      const origin = readFacetSourceDragOrigin(transfer);
      if (source && origin !== facet) {
        closeEditor();
        onSource?.(facet, source, origin ?? undefined);
      }
    } else if (imageFacet(facet)) {
      if (transfer.types.includes(IMAGE_SOURCE_MIME)) {
        if (transfer.getData(IMAGE_SOURCE_MIME) !== facet) {
          closeEditor();
          onMoveImage?.(facet);
        }
      } else {
        const file = transfer.files.item(0);
        if (file) {
          closeEditor();
          onImageFile?.(file, facet);
        }
      }
    }
  };
  const closeEditor = () => {
    setRefineOpen(false);
    setEditorFacet(null);
    setAspectTarget(null);

    if (targetFacet) onCloseReference?.();
  };

  const browse = (facet: RecipeMatchFacet) => {
    if (!canUse(facet)) {
      onLimit?.();
      return;
    }

    setRefineOpen(true);
    setEditorFacet(null);
    setAspectTarget(null);

    onBrowse?.(facet);
  };

  const selectFacet = (facet: RecipeMatchFacet) => {
    if (openFacet === facet) {
      closeEditor();
      return;
    }

    if (!canUse(facet)) {
      onLimit?.();
      return;
    }

    const draft = drafts[facet];
    const hasReference = draft?.kind === "source" || image?.facet === facet;
    if (facet === "composition" && !hasReference) {
      browse(facet);
      return;
    }

    if (targetFacet) onCloseReference?.();

    setEditorText(draft?.kind === "text" ? draft.text : "");
    setRefineOpen(true);
    setAspectTarget(null);
    setEditorMode(hasReference ? "reference" : "text");
    setEditorFacet(facet);
  };

  const commit = () => {
    if (!editorFacet || editorFacet === "composition" || !editorText.trim()) return;

    onCommitText?.(editorFacet, editorText);

    setRefineOpen(false);
    setEditorFacet(null);
    window.requestAnimationFrame(() => refineButtonRef.current?.focus());
  };

  const editorDraft = editorFacet ? drafts[editorFacet] : undefined;

  const editorImage = image?.facet === editorFacet ? image : null;

  const editorSource = editorDraft?.kind === "source" ? editorDraft : undefined;
  const aspectOptions: readonly RecipeMatchFacet[] = editorImage ? MATCH_FACETS.filter(imageFacet) : MATCH_FACETS;
  const replacingAspect = Boolean(aspectTarget && aspectTarget !== editorFacet
    && (matchDraftHasClause(drafts[aspectTarget]) || image?.facet === aspectTarget));
  const aspectMoveFits = clauseCount - (replacingAspect ? 1 : 0) <= MAX_RECIPE_CLAUSES;
  const commitAspect = () => {
    if (!editorFacet || !aspectTarget || aspectTarget === editorFacet
      || !aspectOptions.includes(aspectTarget) || (!editorImage && !editorSource)) return;
    if (!aspectMoveFits) { onLimit?.(); return; }
    const target = aspectTarget;
    closeEditor();
    if (editorImage && imageFacet(target)) onMoveImage?.(target);
    else if (editorSource) onSource?.(target, { ...editorSource, facet: target }, editorFacet);
    window.requestAnimationFrame(() => refineButtonRef.current?.focus());
  };
  const gatedReferences = MATCH_FACETS.flatMap((facet) => {
    const kind = image?.facet === facet ? "image" : drafts[facet]?.kind === "source" ? "source" : null;
    const copy = kind && referenceGateCopy(facet, kind);
    return copy ? [{ facet, kind, ...copy }] : [];
  });

  // The API reports what each category took from its scene; only trust it
  // while it still describes the reference currently in that category.
  const evidenceFor = (facet: RecipeMatchFacet) => {
    const draft = drafts[facet];
    const evidence = sourceEvidence[facet];
    if (draft?.kind !== "source" || !evidence || !("unit_id" in evidence.source)) return undefined;
    return evidence.source.unit_id === draft.source.unit_id && evidence.source.frame_index === draft.source.frame_index
      ? evidence
      : undefined;
  };
  const editorReading = editorFacet ? referenceReading(editorFacet, evidenceFor(editorFacet)) : null;

  return (
    <section
      ref={railRef}
      className={`match-rail search-clues${dragActive ? " is-drag-active" : ""}${dragActive && dragFloating ? " is-drag-floating" : ""}`}
      aria-label="Search details"
      onKeyDown={(event) => {
        if (railRef.current?.querySelector(".movie-scope-popover")) return;
        if (event.key === "Escape" && panelOpen) {
          event.preventDefault();
          event.stopPropagation();
          closeEditor();
          refineButtonRef.current?.focus();
        }
      }}
    >
      <div className="clues-toolbar">
        <button
          ref={refineButtonRef}
          type="button"
          className="clues-refine-toggle"
          aria-expanded={panelOpen}
          aria-controls={`${panelId}-panel`}
          onClick={() => {
            if (panelOpen) closeEditor();
            else setRefineOpen(true);
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" aria-hidden="true">
            <path d="M4 7h4m4 0h8M4 17h8m4 0h4" />
            <circle cx="10" cy="7" r="2" /><circle cx="14" cy="17" r="2" />
          </svg>
          <span>Refine</span>
          {activeFacets.length > 0 && <span className="clues-refine-count">{activeFacets.length}</span>}
          <DirectionIcon name="chevron-down" className="clues-refine-chevron" size={12} />
        </button>
        {filter && <div className="clues-filter" onFocusCapture={closeEditor}>{filter}</div>}
        {controls && (
          <div className="clues-tools" onFocusCapture={closeEditor}>
            {controls}
          </div>
        )}
      </div>

      {(activeFacets.length > 0 || activeExtra) && (
        <div className="clues-active" aria-label="Active refinements and filters">
          {activeFacets.map((facet) => {
            const draft = drafts[facet];
            const hasImage = image?.facet === facet;
            const film = draft?.kind === "source" ? draft.display?.filmTitle || "Reference scene" : "";
            const reading = draft?.kind === "source" ? referenceReading(facet, evidenceFor(facet)).summary : undefined;
            const title = hasImage ? image.display.label
              : draft?.kind === "source" ? reading ?? film
                : draft?.kind === "text" ? draft.text : "";
            const thumbnail = hasImage ? image.display.previewUrl
              : draft?.kind === "source" && draft.display?.keyframeUrl ? `${API_URL}${draft.display.keyframeUrl}` : null;
            return <ClueChip
              key={facet}
              source={{ draft, image: hasImage, facet, title, thumbnail, onDropNowhere: (x, y) => { if (outsideRail(x, y)) removeByDrag(facet); } }}
              type="button"
              className="clue-summary"
              aria-label={`Edit ${FACET_LABELS[facet]}: ${title}`}
              aria-expanded={openFacet === facet}
              aria-controls={`${panelId}-panel`}
              title={`${FACET_LABELS[facet]}: ${title}${reading ? ` (from ${film})` : ""}`}
              onClick={() => selectFacet(facet)}
            >
              {thumbnail && <img src={thumbnail} alt="" draggable={false} />}
              <span className="clue-summary-label">{FACET_LABELS[facet]}</span>
              <span className="clue-summary-value">{title}</span>
            </ClueChip>;
          })}
          {activeExtra}
        </div>
      )}

      {removed && (
        <p className="clues-removed" role="status">
          <span>{FACET_LABELS[removed.facet]} reference removed</span>
          <button type="button" onClick={undoRemove}>Undo</button>
        </p>
      )}

      <div
        id={`${panelId}-panel`}
        className={`clues-panel${targetFacet && referenceHasResults ? " has-reference-results" : ""}`}
        hidden={!panelOpen}
      >
      <div className="clues-strip" aria-label="Match by">

        {MATCH_FACETS.map((facet) => {
          const draft = drafts[facet];

          const hasImage = image?.facet === facet;

          const hasSource = draft?.kind === "source";

          const active = matchDraftHasClause(draft) || hasImage;

          const canDrop =
            (canUse(facet) || movingReference) &&
            (dragKind === "scene" ||
              (imageFacet(facet) && (!targetFacet || movingReference)));

          const title = hasImage
            ? image.display.label
            : hasSource
              ? draft.display?.filmTitle || "Reference scene"
              : draft?.kind === "text"
                ? draft.text
                : "";

          const thumbnail = hasImage
            ? image.display.previewUrl
            : hasSource && draft.display?.keyframeUrl
              ? `${API_URL}${draft.display.keyframeUrl}`
              : null;

          return (
            <div
              key={facet}
              data-facet={facet}

              className={`match-tile clue-card${active ? " is-active" : ""}${openFacet === facet ? " is-open" : ""}${dragOver === facet ? " is-drag-over" : ""}${dragActive && !canDrop ? " is-disabled" : ""}`}

              onDragOver={(event) => {
                if (!accepts(facet, event.dataTransfer)) return;

                event.preventDefault();
                event.stopPropagation();

                event.dataTransfer.dropEffect =
                  event.dataTransfer.effectAllowed === "move" ? "move" : "copy";

                setDragOver(facet);
              }}

              onDragLeave={(event) => {
                if (
                  !event.currentTarget.contains(
                    event.relatedTarget as Node | null,
                  )
                )
                  setDragOver(null);
              }}
              onDrop={(event) => drop(event, facet)}
            >
              <ClueChip
                source={{ draft, image: hasImage, facet, title, thumbnail, onDropNowhere: (x, y) => { if (outsideRail(x, y)) removeByDrag(facet); } }}
                type="button"
                className="clue-chip"
                data-browse-facet={facet}

                aria-label={`${active ? "Edit" : "Add"} ${FACET_LABELS[facet]}${title ? `: ${title}` : ""}`}

                aria-expanded={openFacet === facet}
                aria-controls={panelId}

                title={
                  hasSource || hasImage
                    ? `${FACET_LABELS[facet]}: ${title} · Drag to another category`
                    : title
                      ? `${FACET_LABELS[facet]}: ${title}`
                      : SEARCH_CLUE_COPY[facet].description
                }

                onClick={() => selectFacet(facet)}
              >
                <span className="clue-visual">
                  {thumbnail ? <img src={thumbnail} alt="" draggable={false} /> : <FacetIcon facet={facet} size={14} />}
                </span>

                <span>{FACET_LABELS[facet]}</span>

              </ClueChip>

            </div>
          );
        })}

        {dragActive && (
          <span className="clues-drag-status" role="status">
            {dragOver
              ? `${matchDraftHasClause(drafts[dragOver]) || image?.facet === dragOver ? "Replace" : movingReference ? "Move to" : "Use for"} ${FACET_LABELS[dragOver]}`
              : ""}
          </span>
        )}
      </div>

      {dragActive && movingReference ? (
        <p className={`clues-panel-hint clues-drag-hint${dragRemoving ? " is-removing" : ""}`} aria-hidden="true">
          {dragRemoving
            ? `Release to remove ${dragOrigin ? FACET_LABELS[dragOrigin] : "this reference"}`
            : "Drop on another category to move it, or outside this panel to remove it."}
        </p>
      ) : !openFacet && (
        <p className="clues-panel-hint">Describe a detail, or drag a scene onto a category. Drag a reference out to remove it.</p>
      )}

      {openFacet && (
        <div
          id={panelId}
          className={`clue-editor${targetFacet ? " is-reference-picker" : ""}${targetFacet && referenceHasResults ? " has-reference-results" : ""}`}
        >
          {targetFacet
            ? referencePicker
            : editorFacet && (
                <>
                  <header className="clue-editor-heading">
                    <div>
                      <strong>{editorMode === "reference" ? `${FACET_LABELS[editorFacet]} reference` : SEARCH_CLUE_COPY[editorFacet].description}</strong>
                    </div>

                    <button
                      type="button"
                      className="clue-editor-close"
                      aria-label="Close detail editor"
                      onClick={() => {
                        closeEditor();
                        refineButtonRef.current?.focus();
                      }}
                    >
                      ×
                    </button>
                  </header>

                  {editorMode === "reference" && (editorSource || editorImage) && (
                    <div className="clue-current-reference">
                      <ClueSource
                        draft={editorDraft}
                        image={Boolean(editorImage)}
                        facet={editorFacet}

                        title={
                          editorImage?.display.label ||
                          editorSource?.display?.filmTitle ||
                          "Reference scene"
                        }

                        thumbnail={
                          editorImage?.display.previewUrl ||
                          (editorSource?.display?.keyframeUrl
                            ? `${API_URL}${editorSource.display.keyframeUrl}`
                            : null)
                        }
                      />

                      {editorReading && (
                        <div className="clue-reading" aria-live="polite">
                          <strong>{editorReading.heading}</strong>
                          {editorReading.parts.map((part) => (
                            <p key={part.label}><span>{part.label}</span>{part.text}</p>
                          ))}
                          {editorSource && editorFacet !== "look" && editorFacet !== "composition" && editorReading.parts.length === 0 && (
                            <p className="clue-reading-pending">Shown after the search runs.</p>
                          )}
                        </div>
                      )}
                    </div>
                  )}

                  {editorMode === "reference" && (editorSource || editorImage) && aspectTarget && (
                    <div className="clue-aspect-editor">
                      <label htmlFor={`${panelId}-aspect`}>Match aspect</label>
                      <select id={`${panelId}-aspect`} ref={aspectRef} className="clue-input"
                        value={aspectTarget} onChange={(event) => setAspectTarget(event.target.value as RecipeMatchFacet)}>
                        {aspectOptions.map((facet) => <option value={facet} key={facet}>{FACET_LABELS[facet]}</option>)}
                      </select>
                      <p>{referenceGateCopy(aspectTarget, editorImage ? "image" : "source")?.description
                        ?? SEARCH_CLUE_COPY[aspectTarget].description}</p>
                      <div className="clue-aspect-actions">
                        <button type="button" className="clue-apply"
                          disabled={aspectTarget === editorFacet || !aspectMoveFits}
                          onClick={commitAspect}>
                          {replacingAspect ? "Replace" : "Move to"} {FACET_LABELS[aspectTarget]}
                        </button>
                        <button type="button" className="clue-text-button" onClick={() => {
                          setAspectTarget(null);
                          aspectButtonRef.current?.focus();
                        }}>Cancel</button>
                      </div>
                      {!aspectMoveFits && <p role="status">Remove a detail to stay within three matches.</p>}
                    </div>
                  )}

                  {editorMode === "text" && editorFacet !== "composition" && (
                    <div className="clue-text-editor">
                      <input
                        ref={textRef}
                        type="text"
                        className="clue-input"
                        value={editorText}
                        maxLength={500}

                        placeholder={PLACEHOLDERS[editorFacet]}
                        aria-label={`${FACET_LABELS[editorFacet]} clue`}

                        onChange={(event) => setEditorText(event.target.value)}

                        onKeyDown={(event) => {
                          if (event.nativeEvent?.isComposing || event.keyCode === 229) return;
                          if (event.key === "Enter") {
                            event.preventDefault();
                            commit();
                          }
                        }}
                      />

                      <button
                        type="button"
                        className="clue-apply"
                        disabled={!editorText.trim()}
                        onClick={commit}
                      >
                        Apply
                      </button>
                    </div>
                  )}

                  <div className="clue-editor-footer">
                    {editorMode === "reference" && (editorSource || editorImage) && (
                      <button type="button" className="clue-text-button" ref={aspectButtonRef}
                        aria-expanded={aspectTarget !== null} aria-controls={`${panelId}-aspect`}
                        onClick={() => setAspectTarget(aspectTarget ? null : editorFacet)}>
                        Change aspect
                      </button>
                    )}
                    <button
                      type="button"
                      className="clue-text-button"
                      onClick={() => browse(editorFacet)}
                    >
                      {editorMode === "reference" ? "Change scene" : "Choose a scene"}
                    </button>

                    {(editorSource || editorImage) && editorFacet !== "composition" && (
                      <button type="button" className="clue-text-button" onClick={() => {
                        setAspectTarget(null);
                        setEditorMode(editorMode === "reference" ? "text" : "reference");
                        setEditorText("");
                      }}>{editorMode === "reference" ? "Use text instead" : "Keep reference"}</button>
                    )}
                    {(editorDraft || editorImage) && (
                      <button type="button" className="clue-text-button clue-clear"
                        aria-label={`Remove ${FACET_LABELS[editorFacet]} clue`}
                        onClick={() => {
                          const facet = editorFacet;
                          closeEditor();
                          editorImage ? onRemoveImage?.() : onRemove?.(facet);
                        }}>Remove</button>
                    )}
                  </div>

                </>
              )}
        </div>
      )}
      </div>

      {!panelOpen && idleContent}

      {!dragActive && hasMainText && gatedReferences.map((gate) => (
        <p className="clues-framing-note" key={gate.facet}>
          {gate.description}{" "}
          <button
            type="button"
            onClick={() => {
              if (openFacet === gate.facet) closeEditor();
              gate.kind === "image" ? onRemoveImage?.() : onRemove?.(gate.facet);
            }}
          >
            {gate.removeLabel}
          </button>
        </p>
      ))}
    </section>
  );
}
