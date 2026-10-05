"""Versioned, source-backed project and job interfaces."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pipeline.lab.limits import MAX_AUDIO_PARTS, MAX_PASSAGE_SECONDS, MAX_SAVED_CLIPS, MAX_TIMELINE_SLOTS


class LabModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Crop(LabModel):
    """Normalized crop of the whole source image; no object compositing."""

    x: float = Field(default=0, ge=0, lt=1)
    y: float = Field(default=0, ge=0, lt=1)
    width: float = Field(default=1, gt=0, le=1)
    height: float = Field(default=1, gt=0, le=1)

    @model_validator(mode="after")
    def contained(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("Crop must stay inside the source picture")
        return self


class Passage(LabModel):
    start: float = Field(default=0, ge=0)
    end: float = Field(default=30, gt=0)

    @model_validator(mode="after")
    def bounded(self):
        if not 0 < self.end - self.start <= MAX_PASSAGE_SECONDS:
            raise ValueError("Choose a passage longer than zero and at most 10 minutes")
        return self


class Track(LabModel):
    id: str
    name: str
    duration: float = Field(gt=0)


class ClipSelection(LabModel):
    id: str = Field(min_length=1, max_length=100)
    film_id: str = Field(min_length=1, max_length=200)
    unit_id: str | None = None
    title: str = ""
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)
    locked: bool = False
    crop: Crop | None = None
    region: Crop | None = None
    reference_time: float | None = Field(default=None, ge=0)
    window_start: float | None = Field(default=None, ge=0)
    window_end: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def valid_range(self):
        if self.source_end <= self.source_start:
            raise ValueError("Clip end must follow its start")
        if (self.window_start is None) != (self.window_end is None):
            raise ValueError("Both reference window bounds are required")
        if self.window_start is not None and not (
            self.source_start <= self.window_start < self.window_end <= self.source_end
        ):
            raise ValueError("Reference window must stay inside the selected clip")
        if self.reference_time is not None and not (
            self.source_start <= self.reference_time <= self.source_end
        ):
            raise ValueError("Reference instant must stay inside the selected clip")
        return self


class EffectSource(LabModel):
    """The source window an overlay effect plays (from ``source_start``, for the effect's length)."""

    film_id: str = Field(min_length=1, max_length=200)
    unit_id: str | None = Field(default=None, max_length=240)
    source_start: float = Field(ge=0)
    crop: Crop | None = None


class Effect(LabModel):
    """A render-time effect on the song clock (ADR-0106, ADR-0107).

    ``overlay``, ``fill`` and ``panel`` need ``source``; ``strips`` needs two or
    more ``sources``; ``lock_cut`` and ``zoom_through`` need ``at``, the cut they
    cross, inside ``start``..``end``. ``align`` pins an overlay or zoom to the
    picture's eyes (two eyes when shown) or main subject. ``region`` limits an
    overlay to a hard patch around its own eyes, mouth or face, or to its
    segmented subject (``classes``, COCO names, default person); ``fill`` shows
    its source inside the picture's own segmented subject; ``panel`` sets the
    source into ``rect`` (output fractions), turned by ``turn`` degrees.
    """

    id: str = Field(min_length=1, max_length=100)
    kind: Literal["overlay", "lock_cut", "zoom_through", "punch", "flash", "echo", "fill", "panel", "strips"]
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    at: float | None = Field(default=None, ge=0)
    source: EffectSource | None = None
    sources: list[EffectSource] = Field(default_factory=list, max_length=12)
    align: Literal["eyes", "subject", "none"] = "eyes"
    track: bool = True
    region: Literal["full", "eyes", "mouth", "face", "subject"] = "full"
    classes: list[str] = Field(default_factory=list, max_length=12)
    edge: Literal["soft", "hard"] | None = None
    matte: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    rect: Crop | None = None
    turn: float = Field(default=0.0, ge=-45, le=45)
    settle: bool = False
    blend: Literal["normal", "screen", "lighten", "multiply", "difference", "luma"] = "normal"
    opacity: float = Field(default=1.0, ge=0, le=1)
    attack: float = Field(default=0.0, ge=0, le=10)
    release: float = Field(default=0.0, ge=0, le=10)
    zoom: float = Field(default=1.3, ge=1, le=8)
    strength: float = Field(default=0.5, ge=0, le=0.95)
    title: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def consistent(self):
        if self.end <= self.start:
            raise ValueError("Effect end must follow its start")
        if self.end - self.start > 60:
            raise ValueError("An effect spans at most 60 seconds")
        needs_source = ("overlay", "fill", "panel")
        if self.kind in needs_source and self.source is None:
            raise ValueError("Overlay, fill and panel effects need a source window")
        if self.kind not in needs_source and self.source is not None:
            raise ValueError("Only overlay, fill and panel effects take a source window")
        if (self.kind == "strips") != bool(self.sources):
            raise ValueError("Strips, and only strips, take a list of sources")
        if self.kind == "strips" and len(self.sources) < 2:
            raise ValueError("Strips need at least two sources")
        if self.kind == "panel" and self.rect is None:
            raise ValueError("Panels need a rectangle")
        if self.kind in ("lock_cut", "zoom_through"):
            if self.at is None or not self.start < self.at < self.end:
                raise ValueError("Cut effects need a cut time inside their span")
        return self


class MusicMatchEvidence(LabModel):
    rank: int = Field(ge=1)
    matched_text: str = Field(default="", max_length=1500)
    matched_text_view: str | None = None
    matched_frame_index: int | None = Field(default=None, ge=0)
    matched_frame_timestamp: float | None = Field(default=None, ge=0)
    suggested_source_start: float | None = Field(default=None, ge=0)
    matches: list[dict[str, Any]] = Field(default_factory=list, max_length=3)
    channels: dict[str, Any] = Field(default_factory=dict)


class DialogueAudioSource(LabModel):
    """Bounded, path-free request for a shared browser/export audio source."""

    film_id: str = Field(min_length=1, max_length=200)
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)
    source_audio_mode: Literal["original", "voice_focus"] = "original"

    @model_validator(mode="after")
    def bounded_duration(self):
        if not 0 < self.source_end - self.source_start <= MAX_PASSAGE_SECONDS:
            raise ValueError("Dialogue source range must be positive and at most 10 minutes")
        return self


class DialogueClip(DialogueAudioSource):
    """Independent original-speed source audio placed on the song clock."""

    id: str = Field(min_length=1, max_length=100)
    unit_id: str | None = None
    title: str = Field(default="", max_length=300)
    text: str = Field(default="", max_length=4000)
    start: float = Field(ge=0)
    gain_db: float = Field(default=0, ge=-60, le=24)
    fade_in_seconds: float = Field(default=.08, ge=0, le=90)
    fade_out_seconds: float = Field(default=.12, ge=0, le=90)
    music_duck_db: float = Field(default=-8, ge=-60, le=0)
    # Omission preserves existing saved edits. New UI clips explicitly choose
    # gentler .6/1.2-second ramps; neither API defaults nor restore retime them.
    duck_attack_seconds: float = Field(default=.25, ge=0, le=5)
    duck_release_seconds: float = Field(default=.5, ge=0, le=5)

    @model_validator(mode="after")
    def positive_duration(self):
        if self.source_end <= self.source_start:
            raise ValueError("Dialogue source end must follow its start")
        return self


class MusicAlternative(LabModel):
    clip: ClipSelection
    film_title: str = Field(default="", max_length=300)
    reason: str | None = Field(default=None, max_length=600)
    search_evidence: MusicMatchEvidence | None = None


class MusicSearchClause(LabModel):
    kind: Literal["text", "source"]
    facet: Literal["all", "scene", "words", "look", "mood", "composition"]
    text: str | None = Field(max_length=400)
    reference_id: str | None = Field(max_length=100)

    @model_validator(mode="after")
    def valid_adapter(self):
        if self.kind == "text":
            if not self.text or not self.text.strip() or self.reference_id is not None or self.facet == "composition":
                raise ValueError("Text clauses need a query and no reference; Framing requires a reference")
            self.text = self.text.strip()
        elif not self.reference_id or self.text is not None or self.facet == "all":
            raise ValueError("Source clauses need an offered reference, no text, and a focused facet")
        return self


class FrozenSearchReference(LabModel):
    reference_id: str
    clip_id: str
    film_id: str
    unit_id: str
    frame_index: int = Field(ge=0)
    timestamp: float = Field(ge=0)
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)


class GeneratedSearchPlan(LabModel):
    clauses: list[MusicSearchClause] = Field(min_length=1, max_length=3)
    unverified_requirements: list[str] = Field(max_length=8)

    @model_validator(mode="after")
    def unique_facets(self):
        if len({clause.facet for clause in self.clauses}) != len(self.clauses):
            raise ValueError("Search recipe facets must be unique")
        if any(not value.strip() or len(value) > 300 for value in self.unverified_requirements):
            raise ValueError("Unverified requirements must be nonempty and at most 300 characters")
        return self


class MusicSearchPlan(GeneratedSearchPlan):
    references: list[FrozenSearchReference] = Field(default_factory=list, max_length=3)


class ResolvedMusicSearch(MusicSearchPlan):
    capability_version: str
    min_duration: float = Field(gt=0)


class EditorialDirection(LabModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=400)
    search_facet: Literal["all", "scene", "words", "look", "mood"] = "all"
    purpose: str = Field(default="", max_length=600)
    music_cue: str = Field(default="", max_length=600)
    timing_note: str = Field(default="", max_length=600)


class MusicDirection(EditorialDirection):
    search_plan: MusicSearchPlan | None = None


class PlannerSettings(LabModel):
    pacing: Literal["patient", "balanced", "kinetic", "rapid"] = "balanced"
    lyric_treatment: Literal["ignore", "literal", "metaphorical", "counterpoint"] = "metaphorical"
    # Recognizable <-> fresh footage; values are the search ranking presets.
    footage: Literal["balanced", "famous", "gems"] = "balanced"
    # How strongly cuts prefer frames that match across the cut (harness v2 with a moment index).
    match_cuts: Literal["off", "some", "many"] = "some"
    # Harness v2: the song's treatment chooses pacing, footage and match cuts instead of these values.
    auto: bool = False


class SuppliedLyric(LabModel):
    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(default="", max_length=2000)
    meaning: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def positive_duration(self):
        if self.end <= self.start:
            raise ValueError("A supplied lyric span must have positive duration")
        return self


class SongContext(LabModel):
    track_id: str = Field(min_length=1, max_length=200)
    notes: str = Field(default="", max_length=4000)
    lyrics: list[SuppliedLyric] = Field(default_factory=list, max_length=80)


class VisualPlan(LabModel):
    arc: str = Field(default="", max_length=1200)
    motifs: str = Field(default="", max_length=1000)
    source: Literal["user", "ai"]


class TimedEditorInstruction(LabModel):
    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0, strict=True)
    end: float = Field(gt=0, strict=True)
    instruction: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def positive_duration(self):
        if self.end <= self.start:
            raise ValueError("An editor instruction needs a positive time range")
        return self


class EditorDirection(LabModel):
    instruction: str = Field(default="", max_length=24000)
    ranges: list[TimedEditorInstruction] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def distinct_ranges(self):
        if len({row.id for row in self.ranges}) != len(self.ranges):
            raise ValueError("Editor instruction IDs must be unique")
        ordered = sorted(self.ranges, key=lambda row: (row.start, row.end))
        if any(right.start < left.end for left, right in zip(ordered, ordered[1:])):
            raise ValueError("Editor instruction ranges must not overlap")
        return self


class MusicSlot(LabModel):
    id: str = Field(min_length=1, max_length=100)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    section_index: int = Field(ge=0, lt=8 * MAX_AUDIO_PARTS)
    clip_id: str | None = Field(default=None, min_length=1, max_length=100)
    alternatives: list[MusicAlternative] = Field(default_factory=list, max_length=6)
    reason: str | None = Field(default=None, max_length=600)
    search_error: str | None = Field(default=None, max_length=600)
    direction: MusicDirection | None = None
    direction_source: Literal["user", "ai"] | None = None
    needs_direction: bool = False
    feedback: Literal["too_literal", "too_similar", "wrong_energy", "unfinished_action"] | None = None
    resolved_search: ResolvedMusicSearch | None = None
    search_evidence: MusicMatchEvidence | None = None

    @model_validator(mode="after")
    def positive_duration(self):
        if self.end - self.start < 1 / 24 - 0.000001:
            raise ValueError("Each music slot must contain at least one output frame")
        return self


class ProvisionalTiming(LabModel):
    contract: Literal["local-rhythm-starter-v1"]
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class MusicTimeline(LabModel):
    track_id: str = Field(min_length=1, max_length=200)
    passage: Passage
    slots: list[MusicSlot] = Field(min_length=1, max_length=MAX_TIMELINE_SLOTS)
    provisional_timing: ProvisionalTiming | None = None

    @model_validator(mode="after")
    def complete_timing(self):
        cursor = self.passage.start
        ids, clips = set(), set()
        for slot in self.slots:
            if slot.id in ids or (slot.clip_id and slot.clip_id in clips):
                raise ValueError("Music slots and their selected clip IDs must be unique")
            if abs(slot.start - cursor) > 0.0001:
                raise ValueError("Music slots must cover the passage in order without overlaps or missing time")
            ids.add(slot.id)
            if slot.clip_id:
                clips.add(slot.clip_id)
            cursor = slot.end
        if abs(cursor - self.passage.end) > 0.0001:
            raise ValueError("Music slots must cover the entire selected passage")
        return self


class ProjectDocument(LabModel):
    schema_version: Literal[1] = 1
    track: Track | None = None
    passage: Passage = Field(default_factory=Passage)
    audio_fade_in_seconds: float = Field(default=0, ge=0, le=90)
    audio_fade_out_seconds: float = Field(default=0, ge=0, le=90)
    music_gain_db: float = Field(default=0, ge=-60, le=0)
    dialogue_clips: list[DialogueClip] = Field(default_factory=list, max_length=32)
    effects: list[Effect] = Field(default_factory=list, max_length=600)
    brief: str = Field(default="", max_length=20000)
    planner_settings: PlannerSettings = Field(default_factory=PlannerSettings)
    song_context: SongContext | None = None
    visual_plan: VisualPlan | None = None
    editor_direction: EditorDirection | None = None
    film_ids: list[str] = Field(default_factory=list, max_length=1000)
    analysis: dict[str, Any] | None = None
    rhythm: dict[str, Any] | None = None
    clips: list[ClipSelection] = Field(default_factory=list, max_length=MAX_SAVED_CLIPS)
    music_timeline: MusicTimeline | None = None
    direction_plan: dict[str, Any] | None = None
    aspect_ratio: Literal["16:9", "9:16"] = "16:9"
    fps: Literal[24] = 24

    @model_validator(mode="after")
    def consistent(self):
        if self.audio_fade_in_seconds > self.passage.end - self.passage.start:
            raise ValueError("Audio fade-in must fit inside the selected passage")
        if self.audio_fade_out_seconds > self.passage.end - self.passage.start:
            raise ValueError("Audio fade-out must fit inside the selected passage")
        if self.dialogue_clips and not self.track:
            raise ValueError("Dialogue clips require an imported music track")
        if len({clip.id for clip in self.dialogue_clips}) != len(self.dialogue_clips):
            raise ValueError("Dialogue clip IDs must be unique")
        dialogue = sorted(self.dialogue_clips, key=lambda clip: clip.start)
        for clip in dialogue:
            if clip.start < self.passage.start or clip.start + clip.source_end - clip.source_start > self.passage.end + .000001:
                raise ValueError("Dialogue clips must fit inside the selected passage")
        if len({effect.id for effect in self.effects}) != len(self.effects):
            raise ValueError("Effect IDs must be unique")
        # Effects outside the passage stay saved and are skipped at render, so passage edits never block a save.
        if self.track and self.passage.end > self.track.duration + 0.001:
            raise ValueError("Passage extends beyond the imported track")
        if self.editor_direction and self.editor_direction.ranges:
            if not self.track or any(row.end > self.track.duration for row in self.editor_direction.ranges):
                raise ValueError("Editor instruction ranges must stay inside the imported track")
        if self.song_context:
            if not self.track or self.song_context.track_id != self.track.id:
                raise ValueError("Song context belongs to a different track")
            lyric_ids = [line.id for line in self.song_context.lyrics]
            if len(lyric_ids) != len(set(lyric_ids)):
                raise ValueError("Supplied lyric IDs must be unique")
            if any(line.end > self.track.duration + 0.001 for line in self.song_context.lyrics):
                raise ValueError("Supplied lyric spans must stay inside the imported track")
        ids = [clip.id for clip in self.clips]
        if len(ids) != len(set(ids)):
            raise ValueError("Each clip needs a unique ID")
        if self.music_timeline:
            timeline = self.music_timeline
            if not self.track or timeline.track_id != self.track.id or timeline.passage != self.passage:
                raise ValueError("Music timeline belongs to a different track or passage; update its timing first")
            clips = {clip.id: clip for clip in self.clips}
            segments = (self.analysis or {}).get("segments", [])
            for slot in timeline.slots:
                if segments and slot.section_index >= len(segments):
                    raise ValueError("Music slot refers to an unavailable musical section")
                if slot.clip_id:
                    clip = clips.get(slot.clip_id)
                    if clip is None:
                        raise ValueError("Music slot refers to a missing selected clip")
                    if abs((clip.source_end - clip.source_start) - (slot.end - slot.start)) > 1 / self.fps + 0.000001:
                        raise ValueError("Selected clip duration must fit its music slot")
        return self


class ProjectCreate(LabModel):
    name: str = Field(default="Untitled sketch", min_length=1, max_length=200)
    experiment_id: Literal["music-sketch", "visual-rhymes"] = "music-sketch"
    document: ProjectDocument | None = None


class ProjectUpdate(LabModel):
    base_revision: int = Field(ge=1)
    document: ProjectDocument
    name: str | None = Field(default=None, min_length=1, max_length=200)


class RestoreRequest(LabModel):
    base_revision: int = Field(ge=1)
    revision: int = Field(ge=1)


class MatchPoint(LabModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class MatchOptions(LabModel):
    cohort_id: str = Field(pattern=r"^cohort-[a-f0-9]{16}$")
    reference_clip_id: str = Field(min_length=1, max_length=100)
    mode: Literal["image", "movement"] = "image"
    movement: Literal["camera", "subject"] = "camera"
    allow_reframing: bool = False
    # Omission retains the original experiment for saved jobs and evaluation.
    focus: Literal["auto", "subject", "camera", "image"] | None = None
    timing: Literal["nearby", "fixed"] | None = None
    subject_point: MatchPoint | None = None


class MatchApply(LabModel):
    base_revision: int = Field(ge=1)
    candidate_id: str = Field(pattern=r"^match-[a-f0-9]{16}$")


class MatchAdjust(LabModel):
    base_revision: int = Field(ge=1)
    outgoing_time: float | None = Field(default=None, ge=0)
    incoming_time: float | None = Field(default=None, ge=0)


class GenerateOptions(LabModel):
    mode: Literal["fill", "improve", "regenerate"] = "fill"


class NextSceneOptions(LabModel):
    anchor_slot_id: str = Field(min_length=1, max_length=100)
    intent: str = Field(default="", max_length=1000)
    flexible_cut: bool = Field(default=False, strict=True)
    inspect_frames: bool = Field(default=False, strict=True)


class NextSceneAdjust(LabModel):
    source_start: float | None = Field(default=None, ge=0)
    cut_time: float | None = Field(default=None, ge=0)
    crop: Crop | None = None

    def adjustment_payload(self):
        """Omission preserves a crop; explicit null resets it. Null times are no-ops."""
        values = self.model_dump(mode="json", include={"source_start", "cut_time", "crop"} & self.model_fields_set)
        return {key: value for key, value in values.items() if key == "crop" or value is not None}


class NextSceneApply(NextSceneAdjust):
    base_revision: int = Field(ge=1)
    candidate_id: str = Field(pattern=r"^next-[a-f0-9]{16}$")
    preview_job_id: str | None = Field(default=None, min_length=1, max_length=100)


class JobRequest(LabModel):
    kind: Literal["rhythm", "analyze", "plan", "draft", "generate", "render", "match", "next-scene"]
    base_revision: int = Field(ge=1)
    mode: Literal["preview", "export"] = "preview"
    match: MatchOptions | None = None
    slot_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    replan_timing: bool = False
    generate: GenerateOptions | None = None
    suggest_only: bool = Field(default=False, strict=True)
    next_scene: NextSceneOptions | None = None

    @model_validator(mode="after")
    def match_contract(self):
        if (self.kind == "next-scene") != (self.next_scene is not None):
            raise ValueError("Next-scene jobs require next-scene options; other jobs cannot carry them")
        if self.suggest_only and (self.kind != "draft" or self.slot_ids is None or len(self.slot_ids) != 1):
            raise ValueError("Finding alternatives requires a draft job with exactly one selected music slot")
        if self.replan_timing and self.kind not in {"rhythm", "analyze"}:
            raise ValueError("Only rhythm or analyze jobs can explicitly replan musical timing")
        if (self.kind == "match") != (self.match is not None):
            raise ValueError("Match jobs require matching options; other jobs cannot carry them")
        if self.generate is not None and self.kind != "generate":
            raise ValueError("Only generate jobs accept generation options")
        if self.kind == "generate":
            mode = self.generate.mode if self.generate else "fill"
            if mode == "improve" and (self.slot_ids is None or len(self.slot_ids) != 1):
                raise ValueError("A targeted replacement requires exactly one selected music slot")
            if mode != "improve" and self.slot_ids is not None:
                raise ValueError("Whole-edit generation cannot target selected slots; use targeted replacement mode to replace one slot")
        if self.slot_ids is not None:
            if self.kind not in {"plan", "draft", "generate"}:
                raise ValueError("Only music planning, drafting or generation jobs accept selected music slots")
            if len(set(self.slot_ids)) != len(self.slot_ids) or any(not value or len(value) > 100 for value in self.slot_ids):
                raise ValueError("Selected music slot IDs must be unique and nonempty")
        return self
