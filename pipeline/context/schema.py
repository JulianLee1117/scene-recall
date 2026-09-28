"""Evidence-linked source context, separate from clip eligibility and song intent."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


SCHEMA_VERSION = 1
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
MAX_ACTIVE_ARTIFACTS = 128
SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
ProfileId = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class TimeRange(StrictModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)

    @field_validator("start", "end", mode="before")
    @classmethod
    def numeric(cls, value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Times must be finite numbers in source-player seconds")
        return value

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("A range must have positive duration")
        return self


class SourceFingerprint(StrictModel):
    film_id: SHA256
    content_hash_profile: Literal["sha256-head-tail-4MiB"]
    content_hash: SHA256
    size: int = Field(gt=0, strict=True)
    mtime_ns: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def identity(self):
        if self.content_hash != self.film_id:
            raise ValueError("Source fingerprint must match the indexed film identity")
        return self


class ContextSource(StrictModel):
    film_id: SHA256
    path: str = Field(min_length=1, max_length=4096)
    duration: float = Field(gt=0)
    fingerprint: SourceFingerprint
    timeline: Literal["source-player-seconds"] = "source-player-seconds"

    @field_validator("duration", mode="before")
    @classmethod
    def numeric_duration(cls, value):
        return TimeRange.numeric(value)

    @model_validator(mode="after")
    def identity(self):
        if self.film_id != self.fingerprint.film_id:
            raise ValueError("Source and fingerprint film identities differ")
        return self

    def identity_key(self) -> dict:
        # Paths are provenance: a verified relink preserving content and mtime
        # can reuse context. A changed mtime is conservatively stale.
        return {"film_id": self.film_id, "duration": self.duration,
                "fingerprint": self.fingerprint.model_dump(mode="json"), "timeline": self.timeline}


class Evidence(StrictModel):
    evidence_id: Identifier
    kind: Literal["caption", "dialogue", "ocr", "keyframe", "frame_observation", "subtitle"]
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str | None = Field(default=None, max_length=16000)
    locator: str | None = Field(default=None, max_length=4096)
    sha256: SHA256 | None = None

    @field_validator("start", "end", mode="before")
    @classmethod
    def numeric(cls, value):
        return TimeRange.numeric(value)

    @model_validator(mode="after")
    def evidence_bounds(self):
        if self.end < self.start:
            raise ValueError("Supporting evidence cannot have reversed time bounds")
        if not (self.text and self.text.strip()) and self.sha256 is None:
            raise ValueError("Evidence needs retained text or a content hash")
        return self


class ContextClaim(StrictModel):
    claim_id: Identifier
    kind: Literal["observed", "narrative"]
    text: str = Field(min_length=1, max_length=4000)
    status: Literal["supported", "uncertain"]
    evidence_refs: list[Identifier] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def citations(self):
        if not self.text.strip():
            raise ValueError("A claim cannot be blank")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("Duplicate evidence citations")
        if self.status == "supported" and not self.evidence_refs:
            raise ValueError("A supported claim requires supporting evidence")
        return self


class ContextRecord(StrictModel):
    record_id: Identifier
    level: Literal["film", "sequence", "local"]
    applicability: list[TimeRange] = Field(min_length=1, max_length=64)
    parent_refs: list[Identifier] = Field(default_factory=list, max_length=8)
    claims: list[ContextClaim] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def unique(self):
        if len({claim.claim_id for claim in self.claims}) != len(self.claims):
            raise ValueError("Duplicate claim IDs within a record")
        if len(set(self.parent_refs)) != len(self.parent_refs) or self.record_id in self.parent_refs:
            raise ValueError("Invalid or duplicate parent reference")
        bounds = [(item.start, item.end) for item in self.applicability]
        if len(set(bounds)) != len(bounds):
            raise ValueError("Duplicate applicability ranges")
        return self


class Derivation(StrictModel):
    prompt_id: Identifier
    prompt_sha256: SHA256
    model_id: str = Field(min_length=1, max_length=240)
    model_revision: str | None = Field(default=None, min_length=1, max_length=240)
    input_sha256: SHA256
    input_dependencies: dict[Identifier, SHA256] = Field(max_length=256)
    settings: dict[str, JsonValue] = Field(default_factory=dict, max_length=128)

    def profile_key(self) -> dict:
        return {"schema_version": SCHEMA_VERSION,
                **self.model_dump(mode="json", exclude={"input_sha256", "input_dependencies"})}


class ContextArtifact(StrictModel):
    schema_version: Literal[1] = SCHEMA_VERSION
    profile_id: ProfileId
    source: ContextSource
    evidence: list[Evidence] = Field(max_length=512)
    records: list[ContextRecord] = Field(max_length=128)
    derivation: Derivation
    coverage: list[TimeRange] = Field(min_length=1, max_length=64)
    legal_clip_authority: Literal[False] = False

    @model_validator(mode="after")
    def validate_links_and_bounds(self):
        evidence_ids = {item.evidence_id for item in self.evidence}
        if len(evidence_ids) != len(self.evidence):
            raise ValueError("Duplicate evidence IDs")
        if len({record.record_id for record in self.records}) != len(self.records):
            raise ValueError("Duplicate record IDs")
        for item in [*self.evidence, *self.coverage,
                     *(bounds for record in self.records for bounds in record.applicability)]:
            if item.end > self.source.duration:
                raise ValueError("Context or evidence bounds exceed the source duration")
        coverage = merge_ranges(self.coverage)
        for record in self.records:
            if subtract_ranges(record.applicability, coverage):
                raise ValueError("Applicability must be inside declared context coverage")
            for claim in record.claims:
                if not set(claim.evidence_refs).issubset(evidence_ids):
                    raise ValueError("Claim cites missing evidence")
        return self


def merge_ranges(ranges) -> list[TimeRange]:
    values = sorted((TimeRange.model_validate(item) for item in ranges), key=lambda item: (item.start, item.end))
    merged: list[TimeRange] = []
    for value in values:
        if merged and value.start <= merged[-1].end:
            merged[-1] = TimeRange(start=merged[-1].start, end=max(merged[-1].end, value.end))
        else:
            merged.append(value)
    return merged


def intersect_ranges(left, right) -> list[TimeRange]:
    return merge_ranges([TimeRange(start=max(a.start, b.start), end=min(a.end, b.end))
                         for a in merge_ranges(left) for b in merge_ranges(right)
                         if max(a.start, b.start) < min(a.end, b.end)])


def subtract_ranges(left, right) -> list[TimeRange]:
    result = []
    for item in merge_ranges(left):
        cursor = item.start
        for cut in merge_ranges(right):
            if cut.end <= cursor or cut.start >= item.end:
                continue
            if cut.start > cursor:
                result.append(TimeRange(start=cursor, end=min(item.end, cut.start)))
            cursor = max(cursor, cut.end)
        if cursor < item.end:
            result.append(TimeRange(start=cursor, end=item.end))
    return result


def ranges_json(ranges) -> list[dict]:
    return [item.model_dump(mode="json") for item in merge_ranges(ranges)]
