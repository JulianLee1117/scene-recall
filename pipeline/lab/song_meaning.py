"""Bounded vocal meaning, separate from musical atmosphere and imagined imagery."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pipeline.lab.limits import MAX_AUDIO_PARTS


class MeaningCue(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    paraphrase: str = Field(min_length=1, max_length=600)
    confidence: Literal["low", "medium", "high"]

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("A vocal meaning cue must have positive duration")
        return self


class SongMeaning(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)

    vocal_status: Literal["understood", "partly_understood", "unclear", "no_vocals"]
    summary: str = Field(min_length=1, max_length=1200)
    themes: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(max_length=6)
    cues: list[MeaningCue] = Field(max_length=8)
    uncertainty: str = Field(max_length=600)

    @model_validator(mode="after")
    def evidence_for_meaning(self):
        if self.vocal_status in {"unclear", "no_vocals"} and (self.cues or self.themes):
            raise ValueError("Unclear or absent vocals cannot establish lyric cues or themes")
        if self.vocal_status in {"understood", "partly_understood"} and not self.cues:
            raise ValueError("Understood vocal meaning needs at least one heard paraphrase")
        return self


class WholeSongMeaning(SongMeaning):
    """Server-composed evidence; never used to request a larger audio response."""

    summary: str = Field(min_length=1, max_length=1300 * MAX_AUDIO_PARTS)
    themes: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(max_length=6 * MAX_AUDIO_PARTS)
    cues: list[MeaningCue] = Field(max_length=8 * MAX_AUDIO_PARTS)
    uncertainty: str = Field(max_length=700 * MAX_AUDIO_PARTS)


def validate_song_meaning(value, passage, *, aggregated=False):
    model = WholeSongMeaning if aggregated else SongMeaning
    meaning = model.model_validate(value)
    if any(cue.start < passage["start"] or cue.end > passage["end"] for cue in meaning.cues):
        raise ValueError("Vocal meaning cues must stay inside the selected passage")
    return meaning.model_dump(mode="json")
