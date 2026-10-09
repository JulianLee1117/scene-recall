"""Fixed local worker lanes; job kind remains the durable routing authority."""

WORKER_ROLES = ("editor", "ingest")
ROLE_KINDS = {
    "editor": ("rhythm", "analyze", "plan", "draft", "generate", "render", "match-preview",
               "match-search-preview", "next-scene", "next-scene-preview", "transition-render", "transition-bridge", "transition-generate",
               "algmods-render"),
    "ingest": ("ingest", "match", "match-search", "backfill-temporal", "prepare-search-features", "fit-search-composition"),
}


def role_for_kind(kind):
    return next((role for role, kinds in ROLE_KINDS.items() if kind in kinds), None)


def kinds_for_role(role="all"):
    if role == "all":
        return tuple(kind for kinds in ROLE_KINDS.values() for kind in kinds)
    if role not in ROLE_KINDS:
        raise ValueError("Worker role must be editor, ingest or all")
    return ROLE_KINDS[role]
