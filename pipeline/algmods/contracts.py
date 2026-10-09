"""Bounded request contract for Alg Mods renders: one film window, one treatment."""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pipeline.lab.regions import Region

MODS_VERSION = "algmods-v10"
FPS = 30
MIN_WINDOW_SECONDS = .2
MAX_WINDOW_SECONDS = 12.


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SourceWindow(StrictModel):
    film_id: str = Field(min_length=1, max_length=240)
    unit_id: str | None = Field(default=None, max_length=240)
    source_start: float = Field(ge=0)
    source_end: float = Field(gt=0)

    @model_validator(mode="after")
    def window(self):
        if not MIN_WINDOW_SECONDS - 1e-8 <= self.source_end - self.source_start <= MAX_WINDOW_SECONDS + 1e-8:
            raise ValueError("The source window must be between 0.2 and 12 seconds")
        return self


class Dots(StrictModel):
    """Painted dots that move with the surfaces. ``vivid`` after Yoon Hyup, ``pastel`` after alg.comp.mod."""
    kind: Literal["dots"] = "dots"
    style: Literal["vivid", "pastel"] = "vivid"
    density: float = Field(default=3000, ge=500, le=8000)        # dots per megapixel, an upper bound
    radius_frac: float = Field(default=.0075, ge=.003, le=.02)   # dot radius as a share of frame width
    size_jitter: float = Field(default=.12, ge=0, le=.8)         # log-normal spread of dot radii
    bright_boost: float = Field(default=.4, ge=0, le=1.5)        # extra radius on lit spots
    paint_floor: float = Field(default=.3, ge=.05, le=.8)        # light-or-colour energy below which there is no mark
    spacing: float = Field(default=0, ge=-.2, le=.5)             # gap between dots as a share of combined radii
    grow_in: int = Field(default=5, ge=1, le=15)                 # frames a new dot takes to grow to full size
    shrink_out: int = Field(default=4, ge=1, le=15)              # frames a lost dot takes to shrink away
    grace: int = Field(default=4, ge=0, le=15)                   # frames a lost surface is given before its mark goes
    fill: bool = True                                            # seed every paintable spot, not corners only
    protect_subject: bool = False                                # keep the segmented subject as real picture
    palette_spread: float = Field(default=.6, ge=0, le=1)        # draw among the nearest palette colours (0 = nearest only)
    flat_boost: float = Field(default=.25, ge=0, le=2)           # bigger marks where the picture is flat
    flat_thin: float = Field(default=.5, ge=0, le=1)             # fewer lattice sites where the picture is flat
    flat_gap: float = Field(default=1.5, ge=0, le=4)             # marks keep their distance where the picture is flat (skies open)
    dash: float = Field(default=1.2, ge=0, le=3)                 # dashes along coherent structure (0 = round marks)
    ground: Literal["scene", "navy", "black"] = "scene"          # the ground colour behind the marks
    live: Region = Field(default_factory=lambda: Region(kind="none"))   # the film shows through here, hard-edged


class Stripes(StrictModel):
    """Time stripes: bands of the frame shown from different moments, so motion smears into steps."""
    kind: Literal["stripes"] = "stripes"
    count: int = Field(default=48, ge=6, le=160)                 # bands across the frame
    max_lag: int = Field(default=24, ge=1, le=60)                # frames the slowest band lags
    pattern: Literal["interleave", "ramp", "random"] = "interleave"
    direction: Literal["horizontal", "vertical", "fan"] = "horizontal"   # fan: bands radiate from the centre
    feather: int = Field(default=3, ge=0, le=12)                 # px blur on band edges
    ramp: int = Field(default=24, ge=0, le=90)                   # frames over which the lag grows from zero
    whole_frame: bool = False                                    # False: only the segmented subject is restriped
    live: Region = Field(default_factory=lambda: Region(kind="none"))   # the film shows through here, hard-edged


class Quadtree(StrictModel):
    """Quadtree: the picture as flat squares, fine where it varies; or a cyclic reveal from coarse to fine."""
    kind: Literal["quadtree"] = "quadtree"
    mode: Literal["reveal", "hold"] = "reveal"
    period: float = Field(default=2, ge=.5, le=8)                # seconds per reveal cycle
    threshold: float = Field(default=18, ge=2, le=60)            # hold: split a square whose colour varies more than this
    min_size: int = Field(default=6, ge=2, le=32)                # hold: smallest square in px
    coarse: float = Field(default=64, ge=16, le=128)             # reveal: starting square size
    fine: float = Field(default=2, ge=1, le=8)                   # reveal: final square size
    live: Region = Field(default_factory=lambda: Region(kind="none"))   # the film shows through here, hard-edged


class Mosaic(StrictModel):
    """The shot rebuilt from other shots. ``source`` decides what the tiles are: the whole library, the
    host film's own moments, or the shots a search returns (``queries``). ``region`` confines the tiles."""
    kind: Literal["mosaic"] = "mosaic"
    source: Literal["library", "self", "search"] = "library"
    queries: list[str] = Field(default_factory=list, max_length=6)   # search: what the tiles should be of
    layout: Literal["grid", "quad"] = "quad"                         # quad: cells follow the picture
    aspect: Literal["portrait", "native"] = "native"                 # native: the full frame at its own size
    columns: int = Field(default=12, ge=6, le=32)                    # grid only
    moving: bool = True                                              # tiles play their preview clips
    reveal: Literal["none", "in", "in-out"] = "none"                 # the real shot shatters into tiles
    hold: int = Field(default=8, ge=1, le=60)                        # frames a tile stays at least
    drift: float = Field(default=.1, ge=0, le=1)                     # re-match when the picture under a tile moves away
    region: Region = Field(default_factory=Region)

    @model_validator(mode="after")
    def searched(self):
        if self.source == "search" and not [q for q in self.queries if q.strip()]:
            raise ValueError("A search-sourced mosaic needs at least one query")
        return self


Treatment = Annotated[Union[Dots, Stripes, Quadtree, Mosaic], Field(discriminator="kind")]

STYLE_DEFAULTS = {
    "vivid": {"density": 3000, "radius_frac": .0075, "size_jitter": .35, "bright_boost": .4,
              "palette_spread": .6, "flat_boost": .25, "flat_thin": .5, "flat_gap": 1.5, "dash": 1.2, "ground": "scene"},
    "pastel": {"density": 3000, "radius_frac": .0065, "size_jitter": 0, "bright_boost": 0,
               "palette_spread": 0, "flat_boost": 0, "flat_thin": 0, "flat_gap": 0, "dash": 0, "ground": "black"},
}


class Output(StrictModel):
    side_by_side: bool = False                                   # original on the left, treated on the right


class RenderRequest(StrictModel):
    source: SourceWindow
    treatment: Treatment = Field(default_factory=Dots)
    output: Output = Field(default_factory=Output)


def _range(key, label, lo, hi, step, *, unit=None, advanced=False):
    return {"key": key, "label": label, "type": "range", "min": lo, "max": hi, "step": step,
            "advanced": advanced, **({"unit": unit} if unit else {})}


def _toggle(key, label, *, advanced=False):
    return {"key": key, "label": label, "type": "toggle", "advanced": advanced}


def _select(key, label, values, *, advanced=False):
    return {"key": key, "label": label, "type": "select", "advanced": advanced,
            "options": [{"value": value, "label": title} for value, title in values]}


def _text(key, label, *, placeholder="", advanced=False):
    return {"key": key, "label": label, "type": "text", "placeholder": placeholder, "advanced": advanced}


LIVE_CONTROLS = [
    _select("live.kind", "Live layer", [("none", "None"), ("subject", "Subject stays film"), ("near", "Nearest stays film"),
                                        ("far", "Farthest stays film"), ("background", "Background stays film")]),
    _range("live.share", "Live share of depth", .05, .6, .05, advanced=True),
    _range("live.dilate", "Grow the live layer", 0, 64, 2, unit="px", advanced=True),
]

REGION_CONTROLS = [
    _select("region.kind", "Where", [("all", "Whole frame"), ("subject", "Subject only"), ("background", "Background only")]),
    _toggle("region.largest", "Largest subject only", advanced=True),
    _range("region.dilate", "Grow the region", 0, 64, 2, unit="px", advanced=True),
]


def catalog():
    return {
        "version": MODS_VERSION,
        "treatments": [
            {"id": "dots", "name": "Painted dots",
             "description": "The shot as painted marks that move with its surfaces. Marks go where the light and colour are; voids stay navy.",
             "styles": [
                 {"id": "vivid", "name": "Vivid", "description": "After Yoon Hyup: opaque palette colour on a navy ground, dots of varied size, bigger on the lights.",
                  "defaults": Dots(style="vivid", **STYLE_DEFAULTS["vivid"]).model_dump(mode="json")},
                 {"id": "pastel", "name": "Pastel", "description": "After alg.comp.mod: uniform soft pastel dots on true black.",
                  "defaults": Dots(style="pastel", **STYLE_DEFAULTS["pastel"]).model_dump(mode="json")},
             ],
             "defaults": Dots().model_dump(mode="json"),
             "controls": [
                 _range("density", "Density", 500, 8000, 100, unit="dots/MP"),
                 _range("radius_frac", "Dot size", .003, .02, .0005, unit="of width"),
                 _range("size_jitter", "Size variety", 0, .8, .05),
                 _range("bright_boost", "Bigger on lights", 0, 1.5, .1),
                 _range("paint_floor", "Darkness left empty", .05, .8, .05),
                 _select("ground", "Ground", [("scene", "The scene's shadow colour"), ("navy", "Navy"), ("black", "Black")]),
                 _range("palette_spread", "Colour variety", 0, 1, .1),
                 _range("flat_boost", "Bigger marks on flat areas", 0, 2, .1),
                 _range("flat_gap", "Flat areas left open", 0, 4, .25),
                 _range("flat_thin", "Flat sites dropped", 0, 1, .1, advanced=True),
                 _range("dash", "Dashes along structure", 0, 3, .1),
                 _toggle("protect_subject", "Keep the subject real"),
                 _range("spacing", "Spacing", -.2, .5, .05, advanced=True),
                 _range("grow_in", "Grow in", 1, 15, 1, unit="frames", advanced=True),
                 _range("shrink_out", "Shrink out", 1, 15, 1, unit="frames", advanced=True),
                 _range("grace", "Surface grace", 0, 15, 1, unit="frames", advanced=True),
                 _toggle("fill", "Fill paintable areas", advanced=True),
                 *LIVE_CONTROLS,
             ]},
            {"id": "stripes", "name": "Time stripes",
             "description": "Bands of the frame shown from different moments: a moving subject smears into steps while the rest stays whole.",
             "defaults": Stripes().model_dump(mode="json"),
             "controls": [
                 _range("count", "Bands", 6, 160, 2),
                 _range("max_lag", "Deepest lag", 1, 60, 1, unit="frames"),
                 _select("pattern", "Lag pattern", [("interleave", "Interleaved"), ("ramp", "Ramp"), ("random", "Random")]),
                 _select("direction", "Bands", [("horizontal", "Across"), ("vertical", "Down"), ("fan", "Fan from centre")]),
                 _toggle("whole_frame", "Restripe the whole frame"),
                 _range("feather", "Edge feather", 0, 12, 1, unit="px", advanced=True),
                 _range("ramp", "Lag ramp-in", 0, 90, 1, unit="frames", advanced=True),
                 *LIVE_CONTROLS,
             ]},
            {"id": "mosaic", "name": "Mosaic",
             "description": "The shot rebuilt from other shots: the library, the film itself, or whatever a search finds (a hand of hands). Tiles play; the real shot can shatter into them.",
             "defaults": Mosaic().model_dump(mode="json"),
             "controls": [
                 _select("source", "Tiles from", [("library", "The whole library"), ("self", "This film itself"), ("search", "A search")]),
                 _text("queries", "Search for", placeholder="close-up of a hand, hands touching"),
                 _select("layout", "Cells", [("quad", "Follow the picture"), ("grid", "Grid")]),
                 _select("aspect", "Frame", [("native", "Full frame"), ("portrait", "9:16 crop")]),
                 _range("columns", "Columns (grid)", 6, 32, 1, advanced=True),
                 _toggle("moving", "Tiles play"),
                 _select("reveal", "Arrival", [("none", "Mosaic throughout"), ("in", "Shatter in"), ("in-out", "Shatter in, re-form")]),
                 *REGION_CONTROLS,
                 _range("hold", "Hold", 1, 60, 1, unit="frames", advanced=True),
                 _range("drift", "Drift", 0, 1, .05, advanced=True),
             ]},
            {"id": "quadtree", "name": "Quadtree",
             "description": "The picture as flat squares, fine where it varies and coarse where it does not; or a cyclic reveal from blocks to detail.",
             "defaults": Quadtree().model_dump(mode="json"),
             "controls": [
                 _select("mode", "Mode", [("reveal", "Cyclic reveal"), ("hold", "Hold")]),
                 _range("period", "Reveal period", .5, 8, .25, unit="s"),
                 _range("coarse", "Reveal starts at", 16, 128, 4, unit="px"),
                 _range("fine", "Reveal ends at", 1, 8, .5, unit="px"),
                 _range("threshold", "Hold: split threshold", 2, 60, 1, advanced=True),
                 _range("min_size", "Hold: smallest square", 2, 32, 1, unit="px", advanced=True),
                 *LIVE_CONTROLS,
             ]},
        ],
        "limits": {"min_window_seconds": MIN_WINDOW_SECONDS, "max_window_seconds": MAX_WINDOW_SECONDS, "fps": FPS,
                   "width": 720, "height": 1280},
    }


def render_params(treatment):
    """(mod, params) for the offline renderer, in its string-keyed form."""
    kind = treatment["kind"] if isinstance(treatment, dict) else treatment.kind
    model = {"dots": Dots, "stripes": Stripes, "quadtree": Quadtree, "mosaic": Mosaic}[kind]
    t = model.model_validate(treatment)
    if kind == "dots":
        return "dots", {"style": t.style, "density": t.density, "radius_frac": t.radius_frac, "size_jitter": t.size_jitter,
                        "bright_boost": t.bright_boost, "paint_floor": t.paint_floor, "spacing": t.spacing,
                        "fade_in": t.grow_in, "fade_out": t.shrink_out, "grace": t.grace,
                        "fill": "1" if t.fill else "0", "protect": "subject" if t.protect_subject else "none",
                        "palette_spread": t.palette_spread, "flat_boost": t.flat_boost, "flat_thin": t.flat_thin, "flat_gap": t.flat_gap,
                        "dash": t.dash, "ground": t.ground,
                        "live": t.live.model_dump(mode="json")}
    if kind == "stripes":
        params = {"count": t.count, "max_lag": t.max_lag, "pattern": t.pattern, "feather": t.feather, "ramp": t.ramp,
                  "whole_frame": "1" if t.whole_frame else "0", "live": t.live.model_dump(mode="json")}
        if t.direction == "fan":
            params["vanish"] = "0.5,0.5"
        else:
            params["direction"] = "1,0" if t.direction == "horizontal" else "0,1"
        return "stripes", params
    if kind == "quadtree":
        return "quadtree", {"mode": t.mode, "period": t.period, "threshold": t.threshold, "min_size": t.min_size,
                            "coarse": t.coarse, "fine": t.fine, "live": t.live.model_dump(mode="json")}
    return "mosaic", {"source": t.source, "queries": [q.strip() for q in t.queries if q.strip()], "columns": t.columns,
                      "layout": t.layout, "aspect": t.aspect,
                      "moving": t.moving, "reveal": t.reveal, "hold": t.hold, "drift": t.drift,
                      "region": t.region.model_dump(mode="json")}
