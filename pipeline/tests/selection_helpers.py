"""Readable selector fixtures mapped to source-keyed, per-shot choices."""

from collections.abc import Mapping, Sequence
from pipeline.lab.scene_selection import source_aliases


def selector_response(choices: Sequence[dict], candidates: Sequence[str] | Mapping[int, Sequence[str]]) -> dict:
    """Map fixture IDs to fixed timestamps or normalized timing preferences.

    The supplied retrieval order defines the offered union, as in production.
    Per-shot candidates can differ while their shared aliases remain stable.
    """
    groups = candidates.values() if isinstance(candidates, Mapping) else [candidates]
    aliases = source_aliases(dict.fromkeys(identity for group in groups for identity in group))
    result = {}
    for choice in choices:
        slot = choice["slot"]
        offered = candidates[slot] if isinstance(candidates, Mapping) else candidates
        identity = choice["candidate_id"]
        if identity is not None and identity not in offered:
            raise ValueError("Fixture choice is not offered to this shot")
        result[f"shot_{slot}"] = {
            "source": None if identity is None else {
                aliases[identity]: choice["source_position"] if "preferred_end_frame" in choice else choice["source_start"]},
            "reason": choice["reason"],
            **({"preferred_end_frame": choice["preferred_end_frame"]} if "preferred_end_frame" in choice else {}),
        }
    return {"choices": result}
