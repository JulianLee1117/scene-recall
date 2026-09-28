"""Bounded retrieval and decoded refinement over independently prepared tracks."""
from __future__ import annotations

from pathlib import Path
import numpy as np

from pipeline.matching import cohort, media, motion, subjects
from pipeline.experiments.region_geometry import Box, Picture, propose_crop, NoFeasibleCrop


def shape_similarity(left, right):
    if not left["visible"] or not right["visible"]:
        return {"reliable": False, "score": -1.0}
    position = float(np.exp(-4 * np.linalg.norm(np.asarray(left["centroid"]) - right["centroid"])))
    scale = float(np.exp(-abs(np.log(left["area"] / right["area"]))))
    silhouette = subjects.silhouette_overlap(left["silhouette"], right["silhouette"])
    # Resizing a mask to 8x8 erases its aspect ratio. Restore that independent
    # evidence so a tall rectangle cannot become a perfect wide-rectangle match.
    ratios = [frame["box"]["width"] / frame["box"]["height"] * frame.get("picture_aspect", 1.) for frame in (left, right)]
    aspect = float(np.exp(-abs(np.log(ratios[0] / ratios[1]))))
    # Position and scale cannot establish a shape match without foreground overlap.
    score = silhouette * (.4 * aspect + .35 * position + .25 * scale)
    return {"reliable": score >= .45 and aspect >= .4, "score": score,
            "components": {"position": position, "scale": scale, "silhouette": silhouette, "aspect": aspect},
            "outgoing_region": left["box"], "incoming_region": right["box"]}


def _best(query, references, tracks, row, shape, score_cache=None):
    best = None
    for outgoing in query:
        for ref in references:
            first = min(outgoing["frames"], key=lambda frame: abs(frame["time"] - ref.time))
            for incoming in tracks:
                if outgoing["profile_id"] != incoming["profile_id"]:
                    raise ValueError("Subject profiles differ; prepare a complete compatible subset")
                for frame in incoming["frames"]:
                    if frame["time"] + .5 > row["t_end"] or not frame["visible"]:
                        continue
                    value = (shape_similarity(first, frame) if shape else
                             subjects.similarity(outgoing, incoming, ref.end, frame["time"], cache=score_cache))
                    if value["reliable"] and (best is None or value["score"] > best["score"]):
                        best = {**value, "time": frame["time"], "reference": ref,
                                "unit": row, "track_id": incoming["id"]}
    return best


def candidates(config, db, subset, evidence, clip, source, timestamp, options,
               output, progress, cancelled):
    import torch

    windows = evidence["windows"]
    shape = options.get("channel") == "shape"
    if ({row["unit_id"] for row in windows} != set(subset["motion_unit_ids"])
            or len(windows) != evidence["expected_rows"]):
        raise ValueError("Tracked subject coverage is incomplete")
    context = options.get("_context", {})
    cache_key = evidence["id"]
    if cache_key not in context:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        flow = motion.Flow(Path(evidence["flow_model_directory"]), device)
        tracker = subjects.Tracker(Path(evidence["checkpoint_directory"]), flow, device)
        if tracker.profile != evidence["profile"]:
            raise ValueError("Tracked subject model changed after preparation")
        path = Path(cohort.resolve_film(db, source["film_id"])["path"])
        anchor = media.at(path, timestamp, source["t_start"], source["t_end"], cancelled)
        lower = clip.get("window_start") if clip.get("window_start") is not None else anchor.time
        upper = clip.get("window_end") if clip.get("window_end") is not None else anchor.end
        decoded = media.samples(path, max(source["t_start"], lower - 1),
                                min(source["t_end"], upper + .05), fps=6, cancelled=cancelled)
        decoded = sorted([row for row in decoded if abs(row.time - anchor.time) > 1e-6] + [anchor], key=lambda row: row.time)
        references = [anchor]
        eligible = [row for row in decoded if lower <= row.time < upper and row.end - .5 >= source["t_start"]]
        if clip.get("window_start") is not None and eligible:
            references += [eligible[int(i)] for i in np.linspace(0, len(eligible) - 1, 3)]
        # An unplayably short reference must not shadow a valid nearby proposal.
        from pipeline.matching.service import _native_outgoing_start
        references = [ref for ref in references
                      if ref.end - _native_outgoing_start(path, ref, source, options, cancelled) >= .5 - 1e-6]
        if not references:
            return []
        point = options.get("subject_point")
        progress("Following the subject around your cut point")
        query = tracker.describe(decoded, region=None if point else clip.get("region"),
                                 point=(point["x"], point["y"]) if point else None,
                                 seed_time=anchor.time, cancelled=cancelled,
                                 measure_motion=not shape or options.get("focus") == "auto")["tracks"]
        # Automatic reference focus follows the primary salient region. Candidate
        # retrieval still considers every prepared track; a click/box overrides it.
        query = query[:1]
        context[cache_key] = (tracker, query, references)
    tracker, query, references = context[cache_key]
    if not query:
        raise ValueError("No visible subject at this moment. Choose a subject or another moment.")
    units = {row["unit_id"]: row for row in subset["units"]}
    ranked = []
    score_cache = {}
    if not shape and not any(
            boundary is not None and any(channel["speed"] >= .003 and channel["phases"] is not None
                                         for channel in boundary["channels"].values())
            for track in query for ref in references
            for boundary in [subjects._boundary_window(track, ref.end, True, score_cache)]):
        raise ValueError("No clear subject movement at this moment. Choose another moment or try Shape & composition.")
    for window in windows:
        subjects._cancel(cancelled)
        row = units[window["unit_id"]]
        if row["film_id"] == source["film_id"] or (options.get("film_ids") and row["film_id"] not in options["film_ids"]):
            continue
        best = _best(query, references, window["tracks"], row, shape, score_cache)
        if best:
            ranked.append(best)
    result = []
    maximum = options.get("candidate_budget", 10)
    initial = min(maximum, options.get("_initial_refinement_budget", 5))
    for index, chosen in enumerate(sorted(ranked, key=lambda value: -value["score"])[:maximum]):
        if index >= initial and len(result) >= 3:
            break
        subjects._cancel(cancelled)
        row = chosen["unit"]
        progress(f"Checking subject alignment in candidate {index + 1}")
        path = Path(cohort.resolve_film(db, row["film_id"])["path"])
        # The preparer's 6fps windows provide recall. A fresh 12fps local window
        # refines only shortlisted shots; final identities are real decoded PTS.
        decoded = media.samples(path, max(row["t_start"], chosen["time"] - .25),
                                min(row["t_end"], chosen["time"] + 1.3), fps=12, cancelled=cancelled)
        if len(decoded) < 3:
            continue
        tracked = tracker.describe(decoded, region=chosen["incoming_region"],
                                   seed_time=chosen["time"], cancelled=cancelled,
                                   measure_motion=not shape)["tracks"]
        best = _best(query, references, tracked, row, shape, score_cache)
        if not best:
            continue
        sample = min(decoded, key=lambda frame: abs(frame.time - best["time"]))
        ref = best["reference"]
        crop = None
        if options["allow_reframing"]:
            try:
                crop = propose_crop(Picture(*ref.image.size), Box(**best["outgoing_region"]),
                                    Picture(*sample.image.size), Box(**best["incoming_region"]), output=output,
                                    reference_crop=Box(**clip["crop"]) if clip.get("crop") else None,
                                    max_upscale=2).as_document()
            except NoFeasibleCrop:
                continue
            # The proposal is optional. Motion under the new displayed scaling
            # needs playback review; do not claim the old measurement verifies it.
            best["score"] -= .15 * crop["crop_loss"] + .2 * crop["context_loss"]
            best["components"] = {**best["components"], "reframing_needs_review": True}
        result.append({**best, "sample": sample, "crop": crop,
                       "coarse_rank": index + 1,
                       "region": best["incoming_region"],
                       "evidence_kind": "Similar subject shape and position" if shape else "Tracked subject movement and position"})
    return result
