"""Small, explicit boundary scorer; descriptors are evidence, not learned probabilities."""

from __future__ import annotations

import numpy as np

CONTRACT = "cut-boundary-photometric-foreground-v3"


def motion_components(outgoing, incoming):
    """Compare the exit velocity to the entry velocity, not repeated whole actions.

    Inputs are three ordered temporal bins in identical normalized units. Zoom
    and rotation remain dimensions of camera evidence; no still similarity can
    manufacture a motion score.
    """
    a, b = np.asarray(outgoing, dtype=float), np.asarray(incoming, dtype=float)
    if a.shape != b.shape or a.ndim != 2 or len(a) != 3:
        raise ValueError("Motion comparison requires three compatible temporal bins")
    if not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("Motion evidence must be finite")
    exit_velocity, entry_velocity = a[-1], b[0]
    na, nb = np.linalg.norm(exit_velocity), np.linalg.norm(entry_velocity)
    if min(na, nb) < 0.003:
        return {"reliable": False, "score": -1.0, "reason": "No clear motion at the cut"}
    direction = float(np.clip(exit_velocity @ entry_velocity / (na * nb), -1, 1))
    speed = float(np.exp(-abs(np.log(na / nb))))
    # The acceleration on each side should not change abruptly. Preserve its
    # sign; similarity of mean velocity cannot hide a reversal near the cut.
    acceleration = float(np.exp(-np.linalg.norm((a[-1] - a[-2]) - (b[1] - b[0])) / max(na, nb)))
    reverses = any(float(u @ v) < -0.1 * np.linalg.norm(u) * np.linalg.norm(v)
                   for u, v in ((a[-2], a[-1]), (b[0], b[1])))
    reliable = direction > 0.25 and speed >= 0.25 and not reverses
    score = direction * speed * (0.75 + 0.25 * acceleration) if reliable else -1.0
    return {"reliable": reliable, "score": float(score), "direction": direction,
            "speed": speed, "acceleration": acceleration, "reversal": reverses,
            "scorer": CONTRACT}


def motion_similarity(outgoing, incoming):
    return motion_components(outgoing, incoming)["score"]


def fuse_channels(channels, limit=10):
    """Reciprocal ranks combine independent evidence without mixing raw scores."""
    pooled = {}
    for name, candidates in channels.items():
        for rank, candidate in enumerate(sorted(candidates, key=lambda row: -row["score"])):
            key = candidate["unit"]["unit_id"]
            vote = 1 / (60 + rank + 1)
            entry = pooled.setdefault(key, {"votes": 0.0, "best_rank": rank, "candidate": candidate, "winner": name, "channels": []})
            entry["votes"] += vote
            entry["channels"].append(name)
            if rank < entry["best_rank"]:
                entry["candidate"], entry["best_rank"] = candidate, rank
                entry["winner"] = name
    ordered = sorted(pooled.items(), key=lambda item: (-item[1]["votes"], item[0]))
    # Votes refer to the shot. The winning trim is only supported by its own
    # channel's evidence; another channel may have selected a different instant.
    return [{**row["candidate"], "score": row["votes"], "matched_channels": [row["winner"]],
             "retrieved_channels": row["channels"]}
            for _, row in ordered[:limit]]
