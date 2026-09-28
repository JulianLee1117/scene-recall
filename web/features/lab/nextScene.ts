import type {
  Crop, LabClip, LabJob, MusicSlot, NextSceneAdjustment, NextSceneCandidate, NextScenePreviewResult,
  NextSceneResult, NextSceneScope,
} from "@/types/lab";

export function nextScenePair(slots: MusicSlot[], clips: LabClip[], selectedId: string | null) {
  const selectedIndex = slots.findIndex((slot) => slot.id === selectedId);
  const selected = slots[selectedIndex];
  const byId = new Map(clips.map((clip) => [clip.id, clip]));
  const anchorIndex = selected?.clip_id && byId.has(selected.clip_id)
    ? selectedIndex : selectedIndex - 1;
  const anchorSlot = slots[anchorIndex];
  const nextSlot = slots[anchorIndex + 1];
  const anchor = byId.get(anchorSlot?.clip_id ?? "");
  const next = byId.get(nextSlot?.clip_id ?? "");
  let problem: string | null = null;
  if (!selected || !anchorSlot || !anchor) {
    problem = "Select a placed scene, or the empty placeholder immediately after it. Place a scene from the library or generate an edit first.";
  } else if (!nextSlot) {
    problem = "This is the last scene. Split it or clear a following scene to make room for an alternative.";
  } else if (Math.abs(anchorSlot.end - nextSlot.start) > 1e-6) {
    problem = "The next scene must directly follow the anchor. Choose an adjacent pair on the timeline.";
  } else if (next?.locked) {
    problem = "Unlock the following scene before finding a replacement.";
  }
  return {
    anchorSlot, nextSlot, anchor, next, problem,
    canFlex: !problem && !anchor?.locked && !nextSlot?.clip_id,
  };
}

export function nextSceneResult(job: LabJob | null): NextSceneResult | null {
  if (job?.kind !== "next-scene" || job.status !== "completed") return null;
  const result = job.result as unknown as NextSceneResult | null;
  return result?.scope && Array.isArray(result.candidates) ? result : null;
}

export function nextScenePreview(job: LabJob | null, parentId: string): NextScenePreviewResult | null {
  if (job?.kind !== "next-scene-preview" || job.status !== "completed") return null;
  const result = job.result as unknown as NextScenePreviewResult | null;
  return result?.next_scene_job_id === parentId && result.candidate ? result : null;
}

export function sameNextSceneTiming(candidate: NextSceneCandidate, sourceStart: number, cut: number) {
  return Math.abs(candidate.incoming.source_start - sourceStart) < 1e-6 &&
    Math.abs(candidate.cut - cut) < 1e-6;
}

const fullFrame: Crop = { x: 0, y: 0, width: 1, height: 1 };
const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

export function sameSceneCrop(left: Crop | null | undefined, right: Crop | null | undefined) {
  const a = left ?? fullFrame;
  const b = right ?? fullFrame;
  return (["x", "y", "width", "height"] as const).every((key) => Math.abs(a[key] - b[key]) < 1e-6);
}

export function sameNextSceneDraft(candidate: NextSceneCandidate, draft: NextSceneAdjustment) {
  const preparedCrop = candidate.incoming.crop ?? null;
  // Manifest proof compares the saved representation, including an explicit
  // full-frame rectangle versus null. Visual equivalence is not enough here.
  const cropMatches = preparedCrop === null || draft.crop === null
    ? preparedCrop === draft.crop
    : (["x", "y", "width", "height"] as const).every((key) => preparedCrop[key] === draft.crop![key]);
  return sameNextSceneTiming(candidate, draft.source_start, draft.cut_time) &&
    cropMatches;
}

/** Magnify the fitted image; first remove its bars, then crop both dimensions. */
export function nextSceneZoomCrop(zoom: number, sourceRatio: number, outputRatio: number, previous: Crop | null): Crop | null {
  if (zoom <= 1 + 1e-6) return null;
  const width = sourceRatio >= outputRatio ? 1 / zoom : Math.min(1, outputRatio / sourceRatio / zoom);
  const height = sourceRatio >= outputRatio ? Math.min(1, sourceRatio / outputRatio / zoom) : 1 / zoom;
  const centerX = previous ? previous.x + previous.width / 2 : 0.5;
  const centerY = previous ? previous.y + previous.height / 2 : 0.5;
  return { x: clamp(centerX - width / 2, 0, 1 - width), y: clamp(centerY - height / 2, 0, 1 - height), width, height };
}

export function nextSceneCropZoom(crop: Crop | null, sourceRatio: number, outputRatio: number) {
  return crop ? 1 / (sourceRatio >= outputRatio ? crop.width : crop.height) : 1;
}

/** Shared with the sequence renderer: crop in source space, then contain in output. */
export function nextSceneCropGeometry(crop: Crop | null, sourceRatio: number, outputRatio: number) {
  const value = crop ?? fullFrame;
  const ratio = sourceRatio * value.width / value.height;
  return { crop: value, width: Math.min(1, ratio / outputRatio), height: Math.min(1, outputRatio / ratio) };
}

export function nextScenePanCrop(crop: Crop, dx: number, dy: number): Crop {
  return { ...crop, x: clamp(crop.x + dx, 0, 1 - crop.width), y: clamp(crop.y + dy, 0, 1 - crop.height) };
}

/** The next scene must fit entirely inside its frozen source shot. */
export function nextSceneBounds(scope: NextSceneScope, candidate: NextSceneCandidate, cut: number) {
  const authority = candidate.incoming_authority;
  const lower = Math.max(scope.cut_min, scope.t2 - (authority.t_end - authority.t_start));
  const fixed = Math.abs(scope.cut_min - scope.cut_max) <= 1e-6;
  const origin = scope.passage_start;
  // Match the renderer's passage-relative 24fps grid, including its legal
  // endpoints. The existing saved boundary is also legal when it fits; keeping
  // it must not force an otherwise unnecessary move onto the output grid.
  let cutMin = fixed ? scope.current_cut : origin + Math.ceil((lower - origin) * 24 - 1e-6) / 24;
  let cutMax = fixed ? scope.current_cut : origin + Math.floor((scope.cut_max - origin) * 24 + 1e-6) / 24;
  const currentFits = lower - 1e-6 <= scope.current_cut && scope.current_cut <= scope.cut_max + 1e-6;
  if (!fixed && currentFits) {
    if (cutMin > cutMax + 1e-6) cutMin = cutMax = scope.current_cut;
    else {
      cutMin = Math.min(cutMin, scope.current_cut);
      cutMax = Math.max(cutMax, scope.current_cut);
    }
  }
  const boundedCut = Math.abs(cutMax - cutMin) <= 1e-6 ? cutMin
    : currentFits && Math.abs(cut - scope.current_cut) <= 1e-6 ? scope.current_cut
      : Math.max(cutMin, Math.min(cutMax, origin + Math.round((cut - origin) * 24) / 24));
  return {
    cutMin, cutMax, cut: boundedCut,
    sourceMin: authority.t_start,
    sourceMax: Math.max(authority.t_start, authority.t_end - (scope.t2 - boundedCut)),
  };
}
