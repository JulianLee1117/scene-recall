"""Small executable search contract for planners; no model loading or search."""
from pipeline.index.text_features import resolve_ready_text_profile
from pipeline.index.writer import require_visual_encoder_profile, table_names


CAPABILITY_VERSION = "existing-search-adapters-v1"
LABELS = {"all": "All signals", "scene": "Scene", "words": "Dialogue & text", "look": "Look", "mood": "Mood", "composition": "Framing"}
DESCRIPTIONS = {
    "all": "Hybrid visual, semantic text and lexical ranking; mixed clues are preferences, not hard predicates",
    "scene": "Caption meaning only: visible subjects/actions described by sparse still-image annotations",
    "words": "Dialogue/OCR meaning at shot level; not guaranteed exact quotation or line timing",
    "look": "Global paired-encoder frame appearance; color, subject and scene semantics are not disentangled",
    "mood": "Stored mood labels and energy only; does not search subjects, lighting or plot",
    "composition": "Framing from an offered indexed image: spatial layout, not pose or movement",
}


def search_capabilities(config, db):
    """Check active index/profile readiness once per job, never per slot.

    Unknown means the read could not verify readiness. It is not permission to
    claim that the semantic or visual profile is active.
    """
    visual = semantic = broad = None
    try:
        names = table_names(db)
        units = "units" in names and int(db.open_table("units").count_rows()) > 0
        semantic = bool(units and resolve_ready_text_profile(config, db) is not None)
        visual = False
        if units:
            try:
                require_visual_encoder_profile(db, config)
                visual = config.models.visual_encoder in {"pe_core_l14", "siglip2_so400m"}
            except (RuntimeError, ValueError, KeyError, OSError):
                visual = False
        weights = config.retrieval.weights
        needs_visual = weights.img > 0 or (weights.txt > 0 and not semantic)
        broad = bool(units and (weights.img > 0 or weights.txt > 0 or weights.lex > 0) and (not needs_visual or visual))
        # Focused Look/Framing need frame records, unlike broad unit fallback.
        visual = bool(visual and "frames" in names and int(db.open_table("frames").count_rows()) > 0)
    except (AttributeError, TypeError, ValueError, KeyError, RuntimeError, OSError):
        pass
    return {
        "version": CAPABILITY_VERSION,
        "facets": [{"facet": facet, "label": LABELS[facet], "text_available": (broad if facet == "all" else visual if facet == "look" else False if facet == "composition" else semantic),
                    "source_available": (False if facet == "all" else (visual if config.models.visual_encoder == "pe_core_l14" else False) if facet == "composition" else visual if facet == "look" else semantic),
                    "evidence": description} for facet, description in DESCRIPTIONS.items()],
        "recipe": {"max_clauses": 3, "unique_facets": True, "max_recipes_per_job": 32,
                   "max_primitive_clauses_per_job": 96, "fusion": "Equal reciprocal-rank fusion; Framing constrains candidates; other clues are preferences"},
        "source_scope": "Use only offered reference IDs. References are excluded from results; Framing defaults to other films unless a film scope is explicit",
        "unit_scope": "Results are shots or long-take subdivisions with sparse keyframes, not complete dramatic scenes or verified actions",
        "unsupported": ["motion matching in this planner", "plot/character continuity", "hard negation or metadata predicates",
                        "custom ranking weights", "verified action completion", "exact lyric/utterance alignment"],
        "availability_note": "Read-only index/profile check, not a guarantee of model runtime resources. null means unverified. Separate Lab motion experiments are not a recipe adapter.",
    }
