import { MAX_LAB_SAVED_CLIPS, MAX_MUSIC_TIMELINE_SLOTS } from "@/lib/labLimits";
import { trimDialogueToPassage } from "./dialogueAudio";
import type {
  LabClip,
  LabDocument,
  MusicDirection,
  MusicPlan,
  MusicSection,
  MusicSlot,
  MusicSearchPlan,
  MusicRecipeFacet,
  MusicSearchCapabilities,
  MusicMatchEvidence,
  MusicFeedback,
} from "@/types/lab";

/** Explicit shot work keeps these cuts authoritative for later generation. */
export function keepCutPlan(document: LabDocument): LabDocument {
  if (!document.music_timeline?.provisional_timing) return document;
  return { ...document, music_timeline: { ...document.music_timeline, provisional_timing: null } };
}

/** Explain non-executable visible search inputs before starting a worker job. */
export function searchInputProblem(
  direction: MusicDirection | null,
  clips: LabClip[],
  capabilities: MusicSearchCapabilities | null,
): string | null {
  if (!direction?.query.trim()) return "Describe a scene before searching.";
  const clauses = direction.search_plan?.clauses ?? [
    { kind: "text" as const, facet: direction.search_facet, text: direction.query, reference_id: null },
  ];
  for (const [index, clause] of clauses.entries()) {
    if (clause.kind === "text" && !clause.text?.trim()) return `Write clue ${index + 1} or remove it before searching.`;
    const capability = capabilities?.facets.find((item) => item.facet === clause.facet);
    if (capability?.[clause.kind === "text" ? "text_available" : "source_available"] === false)
      return `${capability.label} is unavailable. Change or remove that search clue.`;
    if (clause.kind === "source") {
      const reference = direction.search_plan?.references?.find((item) => item.reference_id === clause.reference_id);
      const clip = clips.find((item) => item.id === reference?.clip_id);
      if (!reference || !clip || clip.film_id !== reference.film_id ||
        Math.abs(clip.source_start - reference.source_start) > .001 ||
        Math.abs(clip.source_end - reference.source_end) > .001)
        return "A reference changed or is missing. Remove its clue or rewrite the prompt.";
    }
  }
  return null;
}

export function setSlotFeedback(
  document: LabDocument,
  id: string,
  feedback: MusicFeedback | null,
): LabDocument {
  const plan = planOf(document);
  const slot = plan?.slots.find((item) => item.id === id);
  if (!plan || !slot || (slot.feedback ?? null) === feedback ||
    document.clips.some((clip) => clip.id === slot.clip_id && clip.locked)) return document;
  return {
    ...document,
    music_timeline: { ...plan, provisional_timing: null, slots: plan.slots.map((item) =>
      item.id === id ? { ...item, feedback, needs_direction: true } : item) },
  };
}

export function changeMusicPassage(
  document: LabDocument,
  passage: { start: number; end: number },
  fade: number,
): LabDocument {
  const changed = Math.abs(document.passage.start - passage.start) > 0.001 ||
    Math.abs(document.passage.end - passage.end) > 0.001;
  if (!changed && (document.audio_fade_in_seconds ?? 0) === fade) return document;
  return {
    ...document,
    passage,
    audio_fade_in_seconds: fade,
    ...(changed ? {
      analysis: null, rhythm: null, clips: [], music_timeline: null,
      dialogue_clips: trimDialogueToPassage(document.dialogue_clips ?? [], passage),
      audio_fade_out_seconds: Math.min(document.audio_fade_out_seconds ?? 0, passage.end - passage.start),
      direction_plan: null,
      visual_plan: document.visual_plan?.source === "user" ? document.visual_plan : null,
    } : {}),
  };
}

export function sectionsOf(document: LabDocument): MusicSection[] {
  return Array.isArray(document.analysis?.segments)
    ? (document.analysis.segments as unknown as MusicSection[])
    : [];
}

export function directionOf(
  document: LabDocument,
  slot: MusicSlot,
): MusicDirection {
  if (slot.direction) return slot.direction;
  const section = sectionsOf(document)[slot.section_index];
  return {
    query: section?.query ?? "",
    search_facet: section?.search_facet ?? "all",
    purpose: "",
    music_cue: "",
    timing_note: "",
  };
}

/** Editing the visible query must remove any recipe that would override it. */
export function editSlotDirection(
  document: LabDocument,
  id: string,
  patch: Partial<MusicDirection>,
): LabDocument {
  const plan = planOf(document);
  const slot = plan?.slots.find((item) => item.id === id);
  if (!plan || !slot) return document;
  const before = directionOf(document, slot);
  if (Object.entries(patch).every(([key, value]) => before[key as keyof MusicDirection] === value))
    return document;
  const changesSearch = patch.query !== undefined || patch.search_facet !== undefined ||
    patch.search_plan !== undefined;
  const direction: MusicDirection = { ...before, ...patch,
    ...(patch.query !== undefined || patch.search_facet !== undefined ? { search_plan: null } : {}) };
  return {
    ...document,
    music_timeline: { ...plan, provisional_timing: null, slots: plan.slots.map((item) => item.id === id ? {
      ...item, direction, direction_source: "user" as const, needs_direction: false,
      ...(changesSearch ? { alternatives: [], reason: null, search_error: null, resolved_search: null, search_evidence: null } : {}),
    } : item) },
  };
}

/** Keep frozen reference identity; only retained clauses can use it. */
export function editSlotSearchPlan(
  document: LabDocument,
  id: string,
  searchPlan: MusicSearchPlan | null,
): LabDocument {
  const next = searchPlan?.clauses.length ? {
    ...searchPlan,
    references: searchPlan.references?.filter((reference) => searchPlan.clauses.some(
      (clause) => clause.kind === "source" && clause.reference_id === reference.reference_id)),
  } : null;
  return editSlotDirection(document, id, { search_plan: next });
}

function textClueBase(direction: MusicDirection): MusicSearchPlan {
  return direction.search_plan ?? {
    clauses: [{ kind: "text", facet: direction.search_facet, text: direction.query.trim(), reference_id: null }],
    references: [], unverified_requirements: [],
  };
}

/** Offer only ready, unused signals; the main direction remains the first clue. */
export function newTextClueFacets(
  direction: MusicDirection,
  capabilities: MusicSearchCapabilities | null,
): MusicRecipeFacet[] {
  if (!capabilities) return [];
  const plan = textClueBase(direction);
  if (!plan.clauses.length || plan.clauses.length >= Math.min(3, capabilities.recipe.max_clauses) ||
    new Set(plan.clauses.map((clause) => clause.facet)).size !== plan.clauses.length ||
    plan.clauses.some((clause) => clause.kind === "text"
      ? !clause.text?.trim() || clause.text.length > 400 || clause.reference_id !== null || clause.facet === "composition"
      : !clause.reference_id?.trim() || clause.text !== null || clause.facet === "all")) return [];
  // A broad main query can be refined, but adding another broad clue to an
  // existing focused recipe makes the choice less useful and harder to read.
  const preferred: MusicRecipeFacet[] = ["scene", "mood", "look", "words"];
  return preferred.filter((facet) => !plan.clauses.some((clause) => clause.facet === facet) &&
    capabilities.facets.some((item) => item.facet === facet && item.text_available === true));
}

/** A blank local composer never creates an invalid saved clause. */
export function addTextSearchClue(
  direction: MusicDirection,
  facet: MusicRecipeFacet,
  text: string,
  capabilities: MusicSearchCapabilities | null,
): MusicSearchPlan | null {
  const query = text.trim();
  if (!query || query.length > 400 || !newTextClueFacets(direction, capabilities).includes(facet)) return null;
  const base = textClueBase(direction);
  return { ...base, clauses: [...base.clauses, { kind: "text", facet, text: query, reference_id: null }] };
}

function inheritedDirection(document: LabDocument, slot: MusicSlot) {
  const current = directionOf(document, slot);
  const direction =
    slot.direction || current.query.trim() ? { ...current } : null;
  return {
    direction,
    direction_source:
      slot.direction_source ??
      (slot.direction ? null : direction ? ("ai" as const) : null),
    needs_direction: true,
    resolved_search: null,
    search_evidence: null,
  };
}

// Match the backend's greatest-overlap rule, with center distance breaking ties.
function sectionFor(
  start: number,
  end: number,
  sections: Pick<MusicSection, "start" | "end">[],
): number {
  let selected = 0,
    overlap = -1,
    distance = Infinity;
  sections.forEach((section, index) => {
    const nextOverlap = Math.max(
      0,
      Math.min(end, section.end) - Math.max(start, section.start),
    );
    const nextDistance = Math.abs(
      (section.start + section.end) / 2 - (start + end) / 2,
    );
    if (
      nextOverlap > overlap ||
      (nextOverlap === overlap && nextDistance < distance)
    ) {
      selected = index;
      overlap = nextOverlap;
      distance = nextDistance;
    }
  });
  return selected;
}

export function resetSlotDirection(
  document: LabDocument,
  id: string,
): LabDocument {
  const plan = planOf(document);
  const slot = plan?.slots.find((item) => item.id === id);
  if (!plan || !slot) return document;
  type Moment = MusicDirection & Pick<MusicSection, "start" | "end">;
  const raw = document.analysis?.edit_beats;
  const moments = Array.isArray(raw)
    ? (raw as Moment[]).filter(
        (moment) =>
          typeof moment?.query === "string" &&
          !!moment.query.trim() &&
          Number.isFinite(moment.start) &&
          Number.isFinite(moment.end) &&
          moment.end > moment.start,
      )
    : [];
  const moment = moments[sectionFor(slot.start, slot.end, moments)];
  const inherited = moment
    ? {
        query: moment.query,
        search_facet: moment.search_facet ?? "all",
        purpose: moment.purpose ?? "",
        music_cue: moment.music_cue ?? "",
        timing_note: moment.timing_note ?? "",
      }
    : directionOf(document, { ...slot, direction: null });
  const direction = inherited.query.trim() ? inherited : null;
  return {
    ...document,
    music_timeline: {
      ...plan,
      provisional_timing: null,
      slots: plan.slots.map((item) =>
        item.id === id
          ? {
              ...item,
              direction,
              direction_source: direction ? "ai" : null,
              needs_direction: false,
              alternatives: [],
              reason: null,
              search_error: null,
              resolved_search: null,
              search_evidence: null,
            }
          : item,
      ),
    },
  };
}

export function planOf(document: LabDocument): MusicPlan | null {
  if (!document.track) return null;
  if (document.music_timeline) return document.music_timeline;
  // Read old sequential edits without mutating their saved revision.
  const sections = sectionsOf(document);
  const { start, end } = document.passage;
  if (end - start < 1 / 24) return null;
  let cursor = start;
  const slots: MusicSlot[] = [];
  for (const clip of document.clips) {
    const length = clip.source_end - clip.source_start;
    if (cursor + length > end + 1 / 24) return null;
    slots.push({
      id: `legacy-${clip.id}`,
      start: cursor,
      end: Math.min(end, cursor + length),
      section_index: sectionFor(
        cursor,
        Math.min(end, cursor + length),
        sections,
      ),
      clip_id: clip.id,
      alternatives: [],
      reason: null,
      search_error: null,
    });
    cursor = Math.min(end, cursor + length);
  }
  if (end - cursor > 1 / 24)
    slots.push({
      id: `gap-${Math.round(cursor * 24)}`,
      start: cursor,
      end,
      section_index: sectionFor(cursor, end, sections),
      clip_id: null,
      alternatives: [],
      reason: null,
      search_error: null,
    });
  else if (slots.length) slots[slots.length - 1].end = end;
  if (!slots.length)
    slots.push({
      id: "gap-start",
      start,
      end,
      section_index: 0,
      clip_id: null,
      alternatives: [],
      reason: null,
      search_error: null,
    });
  return {
    track_id: document.track.id,
    passage: { ...document.passage },
    slots,
  };
}

export function placeClip(
  document: LabDocument,
  slotId: string,
  source: LabClip,
  evidence: MusicMatchEvidence | null = null,
  sourceAdjusted = false,
): LabDocument {
  const plan = planOf(document);
  const slot = plan?.slots.find((item) => item.id === slotId);
  const currentClip = document.clips.find((clip) => clip.id === slot?.clip_id);
  if (!plan || !slot || currentClip?.locked) return document;
  const duration = slot.end - slot.start;
  if (source.source_end - source.source_start < duration - 1e-6)
    throw new Error(
      "This scene is shorter than the section. Move the cut or choose a longer scene.",
    );
  if (document.clips.length + (currentClip ? 0 : 1) > MAX_LAB_SAVED_CLIPS)
    throw new Error(
      `This edit already has ${MAX_LAB_SAVED_CLIPS} saved scenes. Clear unused saved clips before filling another gap.`,
    );
  const clip = {
    ...source,
    id: crypto.randomUUID(),
    source_end: source.source_start + duration,
    reference_time: null,
    window_start: null,
    window_end: null,
    locked: false,
  };
  return {
    ...document,
    clips: [...document.clips.filter((item) => item.id !== slot.clip_id), clip],
    music_timeline: {
      ...plan,
      provisional_timing: null,
      slots: plan.slots.map((item) =>
        item.id === slotId
          ? {
              ...item,
              clip_id: clip.id,
              reason: "Chosen by you",
              search_error: null,
              search_evidence: evidence,
              ...(sourceAdjusted ? { resolved_search: null, needs_direction: true } : {}),
            }
          : item,
      ),
    },
  };
}

export function clearSlot(document: LabDocument, id: string): LabDocument {
  const plan = planOf(document);
  const slot = plan?.slots.find((item) => item.id === id);
  if (
    !plan ||
    !slot ||
    !slot.clip_id ||
    document.clips.find((clip) => clip.id === slot.clip_id)?.locked
  )
    return document;
  return {
    ...document,
    clips: document.clips.filter((clip) => clip.id !== slot.clip_id),
    music_timeline: {
      ...plan,
      provisional_timing: null,
      slots: plan.slots.map((item) =>
        item.id === id ? { ...item, clip_id: null, reason: null, search_evidence: null } : item,
      ),
    },
  };
}

export function moveCut(
  document: LabDocument,
  index: number,
  value: number,
  durations: Record<string, number>,
): LabDocument {
  const plan = planOf(document);
  if (!plan || index < 1 || index >= plan.slots.length) return document;
  const left = plan.slots[index - 1],
    right = plan.slots[index];
  const a = document.clips.find((clip) => clip.id === left.clip_id),
    b = document.clips.find((clip) => clip.id === right.clip_id);
  if (a?.locked || b?.locked) return document;
  let min = left.start + 0.25,
    max = right.end - 0.25;
  if (b) min = Math.max(min, left.end - b.source_start);
  if (a)
    max = Math.min(
      max,
      left.end +
        Math.max(0, (durations[a.film_id] ?? a.source_end) - a.source_end),
    );
  if (min > max) return document;
  const cut = Math.max(
    min,
    Math.min(
      max,
      document.passage.start +
        Math.round((value - document.passage.start) * 24) / 24,
    ),
  );
  const delta = cut - left.end;
  if (Math.abs(delta) < 0.001) return document;
  return {
    ...document,
    clips: document.clips.map((clip) =>
      clip.id === a?.id
        ? {
            ...clip,
            source_end: clip.source_end + delta,
            reference_time: null,
            window_start: null,
            window_end: null,
          }
        : clip.id === b?.id
          ? {
              ...clip,
              source_start: clip.source_start + delta,
              reference_time: null,
              window_start: null,
              window_end: null,
            }
          : clip,
    ),
    music_timeline: {
      ...plan,
      provisional_timing: null,
      slots: plan.slots.map((slot, i) =>
        i === index - 1
          ? {
              ...slot,
              ...inheritedDirection(document, slot),
              end: cut,
              section_index: sectionFor(slot.start, cut, sectionsOf(document)),
              alternatives: [],
              reason: null,
              search_error: null,
            }
          : i === index
            ? {
                ...slot,
                ...inheritedDirection(document, slot),
                start: cut,
                section_index: sectionFor(cut, slot.end, sectionsOf(document)),
                alternatives: [],
                reason: null,
                search_error: null,
              }
            : slot,
      ),
    },
  };
}

export function splitSlot(
  document: LabDocument,
  id: string,
  time: number,
): LabDocument {
  const plan = planOf(document);
  const slot = plan?.slots.find((item) => item.id === id);
  const clip = document.clips.find((item) => item.id === slot?.clip_id);
  if (!plan || !slot || clip?.locked || plan.slots.length >= MAX_MUSIC_TIMELINE_SLOTS)
    return document;
  const cut =
    document.passage.start +
    Math.round((time - document.passage.start) * 24) / 24;
  if (cut < slot.start + 0.25 || cut > slot.end - 0.25)
    throw new Error(
      "Place the playhead inside the section, at least a quarter-second from either edge.",
    );
  if (clip && document.clips.length >= MAX_LAB_SAVED_CLIPS)
    throw new Error(
      `This edit already has ${MAX_LAB_SAVED_CLIPS} saved scenes. Clear unused saved clips before splitting this clip.`,
    );
  const nextId = crypto.randomUUID();
  const nextClip = clip
    ? {
        ...clip,
        id: crypto.randomUUID(),
        source_start: clip.source_start + cut - slot.start,
        reference_time: null,
        window_start: null,
        window_end: null,
      }
    : null;
  const clips = document.clips.map((item) =>
    item.id === clip?.id
      ? {
          ...item,
          source_end: item.source_start + cut - slot.start,
          reference_time: null,
          window_start: null,
          window_end: null,
        }
      : item,
  );
  if (nextClip) clips.push(nextClip);
  const slots = plan.slots.flatMap((item) =>
    item.id !== id
      ? [item]
      : [
          {
            ...item,
            ...inheritedDirection(document, item),
            end: cut,
            section_index: sectionFor(item.start, cut, sectionsOf(document)),
            alternatives: [],
            reason: null,
            search_error: null,
          },
          {
            ...item,
            ...inheritedDirection(document, item),
            id: nextId,
            start: cut,
            section_index: sectionFor(cut, item.end, sectionsOf(document)),
            clip_id: nextClip?.id ?? null,
            alternatives: [],
            reason: null,
            search_error: null,
          },
        ],
  );
  return { ...document, clips, music_timeline: { ...plan, provisional_timing: null, slots } };
}

export function joinWithNext(
  document: LabDocument,
  slotId: string,
  durations: Record<string, number>,
): LabDocument {
  const plan = planOf(document);
  const index = plan?.slots.findIndex((slot) => slot.id === slotId) ?? -1;
  if (!plan || index < 0 || index >= plan.slots.length - 1)
    throw new Error("There is no cut after this clip to remove.");

  const left = plan.slots[index],
    right = plan.slots[index + 1];
  const a = document.clips.find((clip) => clip.id === left.clip_id),
    b = document.clips.find((clip) => clip.id === right.clip_id);
  if (a?.locked || b?.locked)
    throw new Error(
      "Unlock the clips on either side before removing this cut.",
    );
  if (!a && b)
    throw new Error(
      "Choose a scene for the left clip before removing this cut, or clear the right clip to join two gaps.",
    );

  let sourceEnd = 0;
  if (a) {
    const knownDuration = durations[a.film_id];
    const hasDuration = Number.isFinite(knownDuration) && knownDuration > 0;
    const availableEnd = hasDuration ? knownDuration : a.source_end;
    sourceEnd = a.source_end + right.end - right.start;
    if (sourceEnd > availableEnd + 1e-6)
      throw new Error(
        hasDuration
          ? "The left scene cannot extend far enough to remove this cut. Choose an earlier source start or a longer source film."
          : "The source film duration is unavailable, so this clip cannot be extended to remove the cut.",
      );
  }

  const joined: MusicSlot = {
    ...left,
    ...inheritedDirection(document, left),
    end: right.end,
    section_index: sectionFor(left.start, right.end, sectionsOf(document)),
    alternatives: [],
    reason: null,
    search_error: null,
  };
  return {
    ...document,
    // Keep the removed right selection in the bin, with its original trim.
    clips: a
      ? document.clips.map((clip) =>
          clip.id === a.id
            ? {
                ...clip,
                source_end: sourceEnd,
                reference_time: null,
                window_start: null,
                window_end: null,
              }
            : clip,
        )
      : document.clips,
    music_timeline: {
      ...plan,
      provisional_timing: null,
      slots: [
        ...plan.slots.slice(0, index),
        joined,
        ...plan.slots.slice(index + 2),
      ],
    },
  };
}
