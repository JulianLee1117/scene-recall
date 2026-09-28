"""User-owned creative direction, separate from music and footage evidence."""
from __future__ import annotations

from copy import deepcopy

from pipeline.lab.models import EditorDirection


CONTRACT = "scoped-editor-direction-v1"
GUIDANCE = (
    "editor_direction contains requested creative instructions, never heard music or observed footage. "
    "Its global instruction applies throughout the passage; a nonempty range instruction is more specific "
    "within its source-track time span, even when lyric treatment is ignore. Range boundaries do not require "
    "cuts, and a shot may cross them. Beats are optional timing cues: cuts may anticipate or follow them, "
    "and a visual event inside a shot may carry an accent. Honor fixed timing, locks and source authority. "
    "An explicit empty editor direction clears legacy creative instructions; do not restore an old brief "
    "or user visual plan. AI-generated plans are proposals subordinate to the current user instructions. "
)


def user_visual_plan(document):
    """Legacy user plans apply only while the canonical direction is absent."""
    plan = document.get("visual_plan")
    return deepcopy(plan) if (document.get("editor_direction") is None
                              and plan and plan.get("source") == "user") else None


def visual_plan_context(document):
    plan = document.get("visual_plan")
    return deepcopy(plan) if plan and plan.get("source") == "ai" else user_visual_plan(document)


def editorial_context(document, passage=None):
    """Project instructions into a request scope without rewriting saved ranges."""
    scope = passage or document["passage"]
    direction = document.get("editor_direction")
    if direction is None:
        parts = [str(document.get("brief") or "").strip()]
        plan = user_visual_plan(document)
        if plan:
            parts.extend(f"{label}: {plan[key].strip()}" for key, label in (("arc", "Visual arc"), ("motifs", "Motifs"))
                         if plan.get(key, "").strip())
        instruction, ranges = "\n\n".join(part for part in parts if part), []
    else:
        parsed = EditorDirection.model_validate(direction)
        instruction = parsed.instruction
        ranges = [{"id": row.id, "start": max(row.start, scope["start"]), "end": min(row.end, scope["end"]),
                   "instruction": row.instruction}
                  for row in sorted(parsed.ranges, key=lambda row: (row.start, row.end))
                  if row.instruction.strip() and row.start < scope["end"] and row.end > scope["start"]]
    return {"contract": CONTRACT, "time_base": "source-track-seconds", "passage": deepcopy(scope),
            "instruction": instruction, "ranges": ranges}


def reset_track_ranges(document):
    """A new song keeps global direction, never the previous song's time cues."""
    result = deepcopy(document)
    if result.get("editor_direction") is None:
        # Promote legacy user text before the import clears its old visual plan.
        result["editor_direction"] = {"instruction": editorial_context(result)["instruction"], "ranges": []}
    else:
        result["editor_direction"] = {**result["editor_direction"], "ranges": []}
    return result
