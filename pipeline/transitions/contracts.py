"""Portable bounded recipes for the standalone transition compositor."""
from __future__ import annotations

import math
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

RENDERER_VERSION = "transitions-rgb-v9"
FPS = 30


def frame_count(seconds):
    """Nearest frame, with half-frame ties rounded up (also JavaScript Math.round)."""
    return math.floor(seconds * FPS + .5)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Framing(StrictModel):
    fit: Literal["fit", "fill"] = "fit"
    anchor_x: float = Field(default=.5, ge=0, le=1)
    anchor_y: float = Field(default=.5, ge=0, le=1)
    zoom: float = Field(default=1, ge=1, le=2)


class SourceClip(StrictModel):
    film_id: str = Field(min_length=1, max_length=240)
    unit_id: str | None = Field(default=None, max_length=240)
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)
    framing: Framing = Field(default_factory=Framing)

    @model_validator(mode="after")
    def window(self):
        if not .2 - 1e-8 <= self.source_end - self.source_start <= 12 + 1e-8:
            raise ValueError("Each source window must be between 0.2 and 12 seconds")
        return self


class Recipe(StrictModel):
    id: Literal["whip-pan", "crash-zoom", "soft-wipe", "luma-reveal", "highlight-bloom", "film-burn", "lens-sweep", "shutter-flash", "afterimage-flash", "light-flash", "dip-to-black", "cross-dissolve", "hard-cut", "focus-pull", "prism-push"] = "whip-pan"
    duration: float = Field(default=.35, ge=0, le=2)
    direction: Literal["left", "right", "up", "down"] = "left"
    easing: Literal["linear", "smooth", "snappy"] = "snappy"
    intensity: float = Field(default=.65, ge=0, le=1)
    softness: float = Field(default=.15, ge=.01, le=.5)
    cut_phase: float = Field(default=.5, ge=.15, le=.85)
    peak_phase: float = Field(default=.5, ge=.15, le=.85)
    attack: float = Field(default=.16, ge=.03, le=.45)
    decay: float = Field(default=.38, ge=.08, le=.6)
    spread: float = Field(default=.45, ge=0, le=1)
    warmth: float = Field(default=.25, ge=-1, le=1)
    threshold: float = Field(default=.6, ge=.1, le=.95)
    texture: float = Field(default=.4, ge=0, le=1)
    seed: int = Field(default=7, ge=0, le=2147483647)
    anchor_ax: float = Field(default=.5, ge=0, le=1)
    anchor_ay: float = Field(default=.5, ge=0, le=1)
    anchor_bx: float = Field(default=.5, ge=0, le=1)
    anchor_by: float = Field(default=.5, ge=0, le=1)
    zoom_amount: float = Field(default=2.4, ge=1.1, le=5)
    rebound: float = Field(default=.15, ge=0, le=1)
    overscan: float = Field(default=.04, ge=0, le=.2)
    luma_order: Literal["bright", "dark"] = "bright"
    luma_source: Literal["outgoing", "incoming"] = "outgoing"
    pulse_gap: float = Field(default=.18, ge=.08, le=.4)
    second_pulse: float = Field(default=.35, ge=0, le=1)
    ghost: float = Field(default=.2, ge=0, le=1)
    chromatic: float = Field(default=.2, ge=0, le=1)

    @model_validator(mode="after")
    def timing(self):
        if self.id == "hard-cut":
            self.duration = 0
        elif self.duration < .1:
            raise ValueError("A transition needs at least 3 frames (0.10 seconds) and may last at most 2 seconds")
        return self


class Output(StrictModel):
    aspect: Literal["landscape", "portrait", "square"] = "landscape"
    quality: Literal["draft", "high", "export"] = "draft"


class Retime(StrictModel):
    mode: Literal["off", "rush", "slow-hit", "pulse"] = "off"
    speed: float = Field(default=1, ge=.25, le=4)
    span: float = Field(default=.5, ge=.1, le=2)
    curve: Literal["smooth", "snappy"] = "smooth"
    interpolation: Literal["nearest", "blend", "flow"] = "nearest"

    @model_validator(mode="after")
    def meaning(self):
        if self.mode == "off" and (self.speed != 1 or self.interpolation != "nearest"):
            raise ValueError("Speed off requires speed 1 and nearest sampling")
        if self.mode in {"rush", "pulse"} and self.speed < 1:
            raise ValueError("Rush and speed pulse require a speed multiplier from 1 to 4")
        if self.mode == "slow-hit" and self.speed > 1:
            raise ValueError("Slow hit requires a speed multiplier from 0.25 to 1")
        return self


class RenderRequest(StrictModel):
    outgoing: SourceClip
    incoming: SourceClip
    recipe: Recipe = Field(default_factory=Recipe)
    output: Output = Field(default_factory=Output)
    retime: Retime = Field(default_factory=Retime)

    @model_validator(mode="after")
    def overlap(self):
        overlap = frame_count(self.recipe.duration)
        from pipeline.transitions.retiming import plan
        for index, clip in enumerate((self.outgoing, self.incoming)):
            if self.retime.mode != "off" and self.retime.span > clip.source_end - clip.source_start + 1e-8:
                raise ValueError("The speed span in source seconds must fit both selected windows")
            timing = plan(clip.model_dump(), self.retime.model_dump(), outgoing=index == 0)
            if overlap >= timing["frame_count"]:
                raise ValueError("The overlap must be shorter than both retimed clips; extend a clip, reduce speed or shorten the transition")
        return self


def _range(key, label, lo, hi, step=.01, *, advanced=False, unit=None):
    return {"key": key, "label": label, "type": "range", "min": lo, "max": hi, "step": step,
            "advanced": advanced, **({"unit": unit} if unit else {})}


def _select(key, label, values, *, advanced=False):
    return {"key": key, "label": label, "type": "select", "advanced": advanced,
            "options": [{"value": value, "label": title} for value, title in values]}


CONTROL_SPECS = {
    row["key"]: row for row in [
        _range("duration", "Duration", .1, 2, 1 / FPS, unit="s"),
        _range("intensity", "Amount", 0, 1), _range("softness", "Feather / crossover", .01, .5),
        _range("cut_phase", "Cut phase", .15, .85, advanced=True),
        _range("peak_phase", "Light peak", .15, .85, advanced=True),
        _range("attack", "Light attack", .03, .45, advanced=True, unit="phase"),
        _range("decay", "Light decay", .08, .6, advanced=True, unit="phase"),
        _range("spread", "Spread", 0, 1), _range("warmth", "Warmth", -1, 1),
        _range("threshold", "Highlight threshold", .1, .95, advanced=True),
        _range("texture", "Organic texture", 0, 1), _range("seed", "Texture seed", 0, 2147483647, 1, advanced=True),
        _range("anchor_ax", "A anchor X", 0, 1, advanced=True), _range("anchor_ay", "A anchor Y", 0, 1, advanced=True),
        _range("anchor_bx", "B anchor X", 0, 1, advanced=True), _range("anchor_by", "B anchor Y", 0, 1, advanced=True),
        _range("zoom_amount", "Peak zoom", 1.1, 5, .1), _range("pulse_gap", "Second hit spacing", .08, .4, advanced=True, unit="phase"),
        _range("rebound", "Settle / rebound", 0, 1), _range("overscan", "Overscan crop", 0, .2, .01, advanced=True),
        _range("second_pulse", "Second hit", 0, 1), _range("ghost", "Afterimage", 0, 1),
        _range("chromatic", "Prismatic color", 0, 1),
        _select("direction", "Direction", [(v, v.title()) for v in ("left", "right", "up", "down")]),
        _select("easing", "Movement curve", [("linear", "Linear"), ("smooth", "Smooth"), ("snappy", "Snappy")], advanced=True),
        _select("luma_order", "Reveal order", [("bright", "Highlights first"), ("dark", "Shadows first")]),
        _select("luma_source", "Luma source", [("outgoing", "Outgoing A"), ("incoming", "Incoming B")], advanced=True),
    ]
}


def catalog():
    motion = ["duration", "intensity", "rebound", "cut_phase", "easing", "overscan"]
    light = ["duration", "intensity", "spread", "warmth", "cut_phase", "peak_phase", "attack", "decay", "softness"]
    specs = [
        ("whip-pan", "Camera whip", "motion", "A brief whole-frame camera smear changes shots through the fastest move, then settles. Protected crop; no subject-motion matching.", motion + ["direction", "spread", "softness"], {"duration": .4, "intensity": .65, "spread": .55, "softness": .04}),
        ("crash-zoom", "Anchored crash zoom", "motion", "Push through corresponding visual anchors with radial smear, then settle into the second shot.", motion + ["zoom_amount", "spread", "softness", "anchor_ax", "anchor_ay", "anchor_bx", "anchor_by"], {"duration": .4, "spread": .3}),
        ("highlight-bloom", "Highlight kiss", "light", "Scene highlights lift into a soft halo while shadow detail stays readable.", light + ["threshold"], {"duration": .3, "intensity": .42, "spread": .4, "warmth": .15, "softness": .04, "cut_phase": .47}),
        ("film-burn", "Organic edge burn", "light", "A textured warm light leak rolls across the frame around a short shot change.", light + ["direction", "texture", "seed", "easing"], {"duration": .4, "intensity": .45, "warmth": .55, "spread": .35, "texture": .6, "softness": .04, "cut_phase": .47}),
        ("lens-sweep", "Prismatic lens sweep", "light", "A soft moving light streak carries a restrained colored halo through the cut.", light + ["direction", "chromatic", "anchor_ay", "easing"], {"duration": .35, "intensity": .35, "spread": .3, "chromatic": .2, "softness": .04, "cut_phase": .47}),
        ("shutter-flash", "Shutter double hit", "light", "A tight exposure snap with an independently timed, softer second pulse.", [key for key in light if key != "spread"] + ["pulse_gap", "second_pulse"], {"duration": .4, "intensity": .45, "attack": .08, "decay": .2, "second_pulse": .3, "softness": .04, "cut_phase": .47}),
        ("afterimage-flash", "Afterimage flash", "light", "A subtle flash leaves an offset impression of A that decays over incoming B.", light + ["ghost", "chromatic", "direction"], {"duration": .4, "intensity": .3, "ghost": .22, "spread": .3, "chromatic": .16, "softness": .04, "cut_phase": .47}),
        ("soft-wipe", "Soft swipe", "reveal", "A feathered moving reveal with adjustable edge softness.", ["duration", "direction", "easing", "softness", "cut_phase"], {"duration": .35, "easing": "smooth"}),
        ("luma-reveal", "Luma reveal", "reveal", "Reveal through highlights or shadows using either shot's luminance.", ["duration", "easing", "softness", "cut_phase", "luma_order", "luma_source"], {"duration": .4, "easing": "smooth"}),
        ("light-flash", "Clean exposure flash", "light", "A shaped neutral exposure hit with independent rise, recovery and shot crossover.", [key for key in light if key != "spread"], {"duration": .3, "intensity": .4, "warmth": 0, "softness": .04, "cut_phase": .47}),
        ("dip-to-black", "Dip to black", "baseline", "A shaped exposure dip with adjustable depth.", ["duration", "intensity", "cut_phase", "peak_phase", "attack", "decay", "softness"], {"duration": .35, "intensity": 1}),
        ("cross-dissolve", "Linear-light dissolve", "baseline", "A clean light-preserving blend for comparison.", ["duration", "easing", "cut_phase"], {"duration": .35, "easing": "smooth"}),
        ("hard-cut", "Hard cut", "baseline", "Consecutive source windows without an overlap effect.", [], {}),
        ("focus-pull", "Defocus bridge", "experimental", "A brief soft defocus hides a short picture change, with optional highlight bloom. Experimental.", ["duration", "intensity", "spread", "threshold", "softness", "cut_phase", "peak_phase", "attack", "decay"], {"duration": .3, "intensity": .45, "spread": .12, "threshold": .7, "softness": .04, "cut_phase": .47, "peak_phase": .5, "attack": .42, "decay": .42}),
        ("prism-push", "Prism push", "experimental", "A short lens-like radial push with restrained color separation. A flat-image experiment, not 3D camera travel.", ["duration", "intensity", "zoom_amount", "chromatic", "softness", "cut_phase", "peak_phase", "attack", "decay", "anchor_ax", "anchor_ay", "anchor_bx", "anchor_by"], {"duration": .3, "intensity": .22, "zoom_amount": 1.25, "chromatic": .12, "softness": .04, "cut_phase": .47, "peak_phase": .5, "attack": .42, "decay": .42}),
    ]
    return {"renderer_version": RENDERER_VERSION, "recipes": [
        {"id": identity, "name": name, "group": group, "description": description,
         "defaults": Recipe(id=identity, **defaults).model_dump(mode="json"), "controls": controls,
         "control_specs": [_recipe_control(identity, key) for key in controls]}
        for identity, name, group, description, controls, defaults in specs
    ], "limits": {"min_clip_seconds": .2, "max_clip_seconds": 12,
                  "min_transition_seconds": .1, "max_transition_seconds": 2, "fps": FPS},
       "color_policy": "rec709-linear-light-sdr-v1",
       "retime": {"defaults": Retime().model_dump(mode="json"), "span_unit": "source-seconds",
                  "mapping_policy": "source-speed-integral-2048-v1", "output_fps": FPS,
                  "interpolation": ["nearest", "blend", "flow"], "flow_engine": "ffmpeg-minterpolate-cpu"}}


def _recipe_control(identity, key):
    """Use the effect's actual meaning instead of a generic Amount/Spread label."""
    labels = {
        "whip-pan": {"intensity": "Shutter exposure", "spread": "Shutter softness", "cut_phase": "Picture change", "softness": "Cut blend"},
        "crash-zoom": {"intensity": "Shutter exposure", "spread": "Shutter softness"},
        "highlight-bloom": {"spread": "Halo radius"},
        "film-burn": {"spread": "Burn width", "easing": "Light travel curve"},
        "lens-sweep": {"spread": "Streak size", "anchor_ay": "Light position", "easing": "Light travel curve"},
        "afterimage-flash": {"spread": "Ghost offset"},
        "focus-pull": {"intensity": "Defocus", "spread": "Highlight bloom", "peak_phase": "Focus peak", "attack": "Defocus in", "decay": "Focus recovery"},
        "prism-push": {"intensity": "Lens bend", "zoom_amount": "Peak push", "peak_phase": "Push peak", "attack": "Push in", "decay": "Settle"},
    }
    label = labels.get(identity, {}).get(key)
    if key == "softness" and identity != "whip-pan":
        label = "Edge softness" if identity in {"soft-wipe", "luma-reveal"} else "Picture crossover"
    step = {"step": .05} if identity == "prism-push" and key == "zoom_amount" else {}
    options = {"options": [{"value": "linear", "label": "Gentle"}, {"value": "smooth", "label": "Smooth"},
                           {"value": "snappy", "label": "Fast"}]} if key == "easing" and identity in {"whip-pan", "crash-zoom"} else {}
    return {**CONTROL_SPECS[key], **({"label": label} if label else {}), **step, **options}
