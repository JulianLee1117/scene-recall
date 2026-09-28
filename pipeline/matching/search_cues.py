"""Independent match cues, evaluated on one explicitly identified A/B pair."""
from __future__ import annotations

import numpy as np

from pipeline.matching import subjects, motion
from pipeline.matching.search_shape import shape_similarity
from pipeline.matching.transitions import motion_components

CUE_ORDER = ("subject", "shape", "camera", "position")


def mask_at(track, timestamp):
    if track is None:
        return None
    # Nearest-frame evidence is not proof of the selected cut frame.
    return next((frame for frame in track["frames"] if abs(frame["time"] - timestamp) < 1e-7), None)


def _visible_mask(frame):
    if frame is None or not frame["visible"]:
        return False
    center = np.asarray(frame["centroid"], dtype=float)
    area = float(frame["area"])
    return center.shape == (2,) and np.isfinite(center).all() and ((0 <= center) & (center <= 1)).all() and np.isfinite(area) and 0 < area <= 1


def _subject_edge(track, frame, outgoing, cache):
    key = ("subject-edge", id(track), frame.time, frame.end, outgoing)
    if key not in cache:
        rows = [item for item in track["motion"] if item["end"] <= frame.end + 1e-6] if outgoing else [item for item in track["motion"] if item["start"] >= frame.time - 1e-6]
        edge = rows[-1 if outgoing else 0] if rows else None
        known = edge is not None and edge["reliable"] and abs(edge["end" if outgoing else "start"] - frame.time) < 1e-6
        cache[key] = (track, known)
    return cache[key][1]


def camera_window(sequence, timestamp, outgoing, cache, frame_time=None):
    key = ("camera", id(sequence), timestamp, outgoing, frame_time)
    if key not in cache:
        if outgoing:
            rows = [item for item in sequence if timestamp - 1.05 <= item["start"] and item["end"] <= timestamp + 1e-6]
        else:
            rows = [item for item in sequence if item["start"] >= timestamp - 1e-6 and item["end"] <= timestamp + 1.05]
        # A descriptor covering the middle of a clip cannot prove its entry/exit.
        boundary_known = rows and rows[-1 if outgoing else 0]["reliable"]
        if boundary_known:
            expected = frame_time if outgoing and frame_time is not None else timestamp
            boundary_known = abs(rows[-1]["end"] - expected) < 1e-6 if outgoing else abs(rows[0]["start"] - expected) < 1e-6
        value = motion.descriptor(rows, "camera") if boundary_known else None
        cache[key] = (sequence, value)
    return cache[key][1]


def _cue(code, label, description, score, measurements, reference, candidate):
    return {"code": code, "label": label, "description": description, "strength": float(score),
            "measurements": {**measurements, "reference_frame_pts": reference.time,
                             "candidate_frame_pts": candidate.time}}


def score_pair(reference, candidate, outgoing_track, incoming_track, outgoing_camera, incoming_camera,
               *, enabled, cache):
    """Every returned cue refers to these exact source frames and tracked region."""
    cues = []
    a, b = mask_at(outgoing_track, reference.time), mask_at(incoming_track, candidate.time)
    if outgoing_track is not None and incoming_track is not None:
        if not outgoing_track.get("profile_id") or outgoing_track.get("profile_id") != incoming_track.get("profile_id"):
            raise ValueError("Cannot compare incompatible tracked-subject profiles")
    if _visible_mask(a) and _visible_mask(b):
        offset = np.asarray(b["centroid"], dtype=float) - np.asarray(a["centroid"], dtype=float)
        distance = float(np.linalg.norm(offset))
        if "position" in enabled and distance <= .15:
            def location(center):
                horizontal = "left" if center[0] < .4 else "right" if center[0] > .6 else "center"
                vertical = "upper" if center[1] < .4 else "lower" if center[1] > .6 else "middle"
                return horizontal, vertical
            locations = location(a["centroid"]), location(b["centroid"])
            description = "The selected regions' centers are close at the cut."
            if locations[0] == locations[1]:
                horizontal, vertical = locations[0]
                place = "center" if (horizontal, vertical) == ("center", "middle") else f"{vertical} {horizontal}"
                description = f"Both selected regions sit near the {place} of the picture."
            description += f" Their centers differ by {abs(offset[0]) * 100:.1f}% of the picture width and {abs(offset[1]) * 100:.1f}% of its height."
            cues.append(_cue("position", "Similar screen position",
                             description,
                             1 - distance / .15,
                             {"reference_center": a["centroid"], "candidate_center": b["centroid"],
                              "horizontal_difference": float(offset[0]), "vertical_difference": float(offset[1]),
                              "center_distance": distance}, reference, candidate))
        if "shape" in enabled:
            shape = shape_similarity(a, b)
            if shape["reliable"]:
                ratio = b["area"] / a["area"]
                note = " The candidate is smaller on screen." if ratio < 2 / 3 else " The candidate is larger on screen." if ratio > 1.5 else ""
                cues.append(_cue("shape", "Similar silhouette", "The foreground outlines have a similar shape." + note,
                                 (shape["score"] - .45) / .55,
                                 {**shape["components"], "candidate_area_ratio": ratio}, reference, candidate))
        if "subject" in enabled and _subject_edge(outgoing_track, reference, True, cache) and _subject_edge(incoming_track, candidate, False, cache):
            movement = subjects.similarity(outgoing_track, incoming_track, reference.end, candidate.time, cache=cache)
            if movement["reliable"]:
                cues.append(_cue("subject", "Similar subject movement",
                                 "Movement within the selected subjects follows a similar pattern at the cut.",
                                 (movement["components"]["movement"] - .2) / .8,
                                 movement["components"], reference, candidate))
    if "camera" in enabled:
        first = camera_window(outgoing_camera, reference.end, True, cache, reference.time)
        second = camera_window(incoming_camera, candidate.time, False, cache)
        if first is not None and second is not None:
            camera = motion_components(first, second)
            if camera["reliable"]:
                velocity, incoming_velocity = np.asarray(first[-1]), np.asarray(second[0])
                def direction(value):
                    return ("down" if value[1] >= 0 else "up") if abs(value[1]) > abs(value[0]) else ("right" if value[0] >= 0 else "left")
                description = "The picture moves in a similar direction and at a similar speed across the cut."
                if min(np.linalg.norm(velocity[:2]), np.linalg.norm(incoming_velocity[:2])) >= .003 and direction(velocity) == direction(incoming_velocity):
                    description = f"The picture moves {direction(velocity)} on both sides of the cut, at a similar speed."
                if max(np.linalg.norm(velocity[:2]), np.linalg.norm(incoming_velocity[:2])) < .003:
                    description = "The picture's zoom or rotation changes similarly across the cut."
                cues.append(_cue("camera", "Similar camera movement",
                                 description,
                                 camera["score"],
                                 {**camera, "outgoing_velocity": np.asarray(first[-1]).tolist(),
                                  "incoming_velocity": np.asarray(second[0]).tolist()}, reference, candidate))
    return cues


def rank_pair(cues, focus):
    """An editorial ordering policy, not a calibrated match probability.

    Position is a useful fallback, but near-identical centers are common. Shape
    and position form one static family; screen subject flow and camera flow
    form one temporal family. Correlated cues do not buy extra votes.
    """
    by_code = {cue["code"]: cue for cue in cues}
    if not cues or (focus != "auto" and focus not in by_code):
        return None
    if focus != "auto":
        return {"primary_cue": focus, "score": by_code[focus]["strength"]}
    strengths = {code: max(0., min(1., cue["strength"])) * (.25 if code == "position" else 1.)
                 for code, cue in by_code.items()}
    primary = max(strengths, key=lambda code: (strengths[code], -CUE_ORDER.index(code)))
    static = max((strengths.get(code, 0.) for code in ("position", "shape")))
    temporal = max((strengths.get(code, 0.) for code in ("subject", "camera")))
    # Supporting evidence has to be strong, independent and from this exact pair.
    return {"primary_cue": primary, "score": max(static, temporal) + .1 * min(static, temporal)}


def shared_shortlist(channels, maximum=10):
    """Deduplicate common-pair proposals without giving correlated cues votes.

    Proposal scores use the same rank_pair policy as decoded refinement. A shot
    is seeded at its strongest actual pair, never a fusion of incompatible cut
    points or regions. Each shot consumes at most one refinement window.
    """
    if not 1 <= maximum <= 10:
        raise ValueError("Refinement is bounded to at most ten unique shots")
    pooled = {}
    for code, proposals in channels.items():
        for proposal in proposals:
            identity = proposal["unit"]["unit_id"]
            entry = pooled.setdefault(identity, {"seed_key": (-float("inf"), -99, -float("inf")),
                                                  "seed": proposal, "retrieved_cues": []})
            entry["retrieved_cues"].append(code)
            key = (proposal["score"], -CUE_ORDER.index(code), -proposal["time"])
            if key > entry["seed_key"]:
                entry["seed"], entry["seed_key"] = proposal, key
    return [{**entry["seed"], "retrieved_cues": entry["retrieved_cues"]}
            for _, entry in sorted(pooled.items(), key=lambda item: (-item[1]["seed"]["score"], item[0]))[:maximum]]
