"""Portable input contract for explicit scene-based Match search."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


def same_pair(first, second):
    """Preview proof belongs to every immutable choice that defines this cut."""
    return all(first.get(key) == second.get(key) for key in
               ("id", "outgoing", "incoming", "reference_frame_pts", "candidate_frame_pts", "crop"))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Point(StrictModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class Region(Point):
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def contained(self):
        if self.x + self.width > 1.0000001 or self.y + self.height > 1.0000001:
            raise ValueError("Choose an area inside the source picture")
        return self


class Reference(StrictModel):
    unit_id: str = Field(min_length=1, max_length=240)
    time: float = Field(ge=0)
    subject_point: Point | None = None
    region: Region | None = None

    @model_validator(mode="after")
    def one_prompt(self):
        if self.subject_point is not None and self.region is not None:
            raise ValueError("Choose a subject point or an area, not both")
        return self


class SearchRequest(StrictModel):
    cohort_id: str = Field(min_length=1, max_length=160, pattern=r"^cohort-[a-zA-Z0-9_-]+$")
    reference: Reference
    focus: Literal["auto", "position", "shape", "subject", "camera"] = "auto"
    timing: Literal["fixed", "nearby"] = "nearby"
    film_ids: list[str] = Field(default_factory=list, max_length=200)
    include_source_film: bool = False
    min_incoming_seconds: float = Field(default=1, ge=.5, le=30)
    allow_reframing: Literal[False] = False

    @model_validator(mode="after")
    def unique_scope(self):
        if any(not isinstance(value, str) or not value or len(value) > 160 for value in self.film_ids):
            raise ValueError("Choose valid library films")
        self.film_ids = list(dict.fromkeys(self.film_ids))
        return self
