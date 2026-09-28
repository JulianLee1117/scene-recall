"""Library recall followed by bounded, source-exact Match Cut verification.

Indexed appearance proposes shots. Detected people establish a foreground group
that every returned people match must preserve. Neither appearance nor camera
motion can substitute for a missing member of that group.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

import numpy as np
from PIL import Image

from pipeline.matching import cohort, media, people, library_retrieval
from pipeline.matching.person_layout import compare_people
from pipeline.matching.search_shape import shape_similarity

SCREEN_LIMIT = 48
MAX_REFINEMENTS = 10
INITIAL_REFINEMENTS = 5
NATIVE_SAMPLES = 4


def _cancel(cancelled):
    if cancelled():
        from pipeline.lab.media import JobCancelled
        raise JobCancelled("Match search cancelled")


def _verify_film(proposed):
    from pipeline.ingest.probe import _content_hash
    if _content_hash(Path(proposed["film_path"])) != proposed["film_id"]:
        raise ValueError("Candidate source identity changed")


class PeopleEvidence:
    """Reusable derivations keyed by detector profile and exact decoded pixels."""
    def __init__(self, config, detector):
        self.detector = detector
        self.profile = detector.profile["id"]
        self.directory = config.paths.assets_dir / "matching" / "people-evidence" / self.profile

    def describe(self, image):
        rgb = image.convert("RGB")
        key = hashlib.sha256(str(rgb.size).encode() + rgb.tobytes()).hexdigest()
        path = self.directory / f"{key}.json"
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if (cached["pixel_sha256"] == key and cached["result"]["profile_id"] == self.profile
                    and cohort.digest(cached["result"]) == cached["result_sha256"]):
                return cached["result"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
        result = self.detector.describe(rgb)
        if result["profile_id"] != self.profile:
            raise ValueError("People detector changed during this search")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        temporary.write_text(json.dumps({"pixel_sha256": key, "result": result,
                                         "result_sha256": cohort.digest(result)}, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
        return result


def group_box(selected):
    if not selected:
        return None
    boxes = [item["box"] for item in selected]
    x, y = min(box["x"] for box in boxes), min(box["y"] for box in boxes)
    return {"x": x, "y": y, "width": max(box["x"] + box["width"] for box in boxes) - x,
            "height": max(box["y"] + box["height"] for box in boxes) - y}


def _layout_cues(reference, candidate, first, second):
    comparison = compare_people(first, second)
    if not comparison["reliable"]:
        return None
    count = len(first)
    description = ("One prominent person has similar placement and height in the picture." if count == 1 else
                   f"{count} prominent people have similar placement and height in the picture.")
    if count == 2:
        spacing = comparison["measurements"]["spacing"][0]
        ratio = spacing["candidate_distance"] / max(spacing["reference_distance"], 1e-8)
        description += (" The pair is closer together." if ratio < .8 else
                        " The pair is farther apart." if ratio > 1.25 else " Their spacing is similar.")
    timestamps = {"reference_frame_pts": reference.time, "candidate_frame_pts": candidate.time}
    cues = [{"code": "layout", "label": "Similar subject arrangement", "description": description,
             "strength": comparison["strength"], "measurements": {**comparison["measurements"], **timestamps}}]
    outlines = [shape_similarity(first[pair["reference_index"]], second[pair["candidate_index"]])
                for pair in comparison["measurements"]["assignment"]]
    if outlines and all(outline["reliable"] for outline in outlines):
        cues.append({"code": "shape", "label": "Similar silhouette",
                     "description": "The outlines of the matched people also have a similar shape.",
                     "strength": min((outline["score"] - .45) / .55 for outline in outlines),
                     "measurements": {"people": [outline["components"] for outline in outlines], **timestamps}})
    # Arrangement is the search requirement. Correlated shape is explanatory,
    # not an extra vote that can hide a badly placed or missing partner.
    return {"score": comparison["strength"], "primary_cue": "layout", "cues": cues,
            "reference": reference, "sample": candidate,
            "outgoing_region": group_box(first), "incoming_region": group_box(second)}


def _screen(seeds, reference_people, detector, progress, cancelled):
    screened, checked = [], 0
    for seed in seeds[:SCREEN_LIMIT]:
        if cancelled():
            from pipeline.lab.media import JobCancelled
            raise JobCancelled("Match search cancelled")
        checked += 1
        progress(f"Checking the foreground arrangement in library scene {checked}/{min(len(seeds), SCREEN_LIMIT)}")
        with Image.open(seed["path"]) as image:
            selected = detector.describe(image)["selected"]
        comparison = compare_people(reference_people, selected)
        # A retained still only proposes a window. Loose geometric screening
        # keeps a promising shot whose people move into alignment nearby; strict
        # layout gates apply only at the final native cut frames.
        assignment = comparison["measurements"]["assignment"]
        if len(selected) == len(reference_people) and assignment:
            support = min(min(max(0., 1 - pair["center_distance"] / .3), pair["height_similarity"])
                          for pair in assignment)
            if support > 0:
                screened.append({**seed, "layout_strength": support})
    return sorted(screened, key=lambda row: (-row["layout_strength"], row["global_rank"], row["frame_id"]))[:MAX_REFINEMENTS], checked


def _people_pairs(proposals, reference_rows, reference_people, source, options, identity, starts,
                  detector, coverage, progress, cancelled, publish):
    from pipeline.matching.search import _candidate
    results = []
    for index, proposed in enumerate(proposals):
        if index >= INITIAL_REFINEMENTS and len(results) >= 3:
            break
        _cancel(cancelled)
        _verify_film(proposed)
        progress(f"Checking people at the actual cut: {index + 1}/{len(proposals)}")
        rows = media.samples(Path(proposed["film_path"]), max(proposed["t_start"], proposed["time"] - .25),
                             min(proposed["t_end"], proposed["time"] + 1.3), fps=12, cancelled=cancelled)
        coverage["refined_window_count"] += 1
        rows = [row for row in rows if proposed["t_end"] - row.time >= options["min_incoming_seconds"] - 1e-6]
        seed_frame = media.at(Path(proposed["film_path"]), proposed["time"], proposed["t_start"], proposed["t_end"], cancelled)
        others = [row for row in rows if abs(row.time - seed_frame.time) > 1e-7]
        selected_rows = ([seed_frame] if proposed["t_end"] - seed_frame.time >= options["min_incoming_seconds"] - 1e-6 else [])
        if others:
            selected_rows += [others[int(i)] for i in np.linspace(0, len(others) - 1, min(NATIVE_SAMPLES - len(selected_rows), len(others)))]
        best = None
        for row in selected_rows:
            if cancelled():
                from pipeline.lab.media import JobCancelled
                raise JobCancelled("Match search cancelled")
            incoming = detector.describe(row.image)["selected"]
            for ref in reference_rows:
                outgoing = reference_people[ref.time]
                value = _layout_cues(ref, row, outgoing, incoming)
                if value is None:
                    continue
                key = (value["score"], -abs(ref.time - options["reference"]["time"]), -row.time)
                if best is None or key > best[0]:
                    best = key, value
        if best is not None:
            item = _candidate(best[1], proposed, source, starts, identity, proposed["film_title"])
            item["retrieved_channels"] = ["library_appearance"]
            item["retrieval_evidence"] = {"frame_id": proposed["frame_id"], "frame_time": proposed["time"],
                                          "global_rank": proposed["global_rank"]}
            results.append(item)
            publish(item)
    return results


def _generic_pairs(config, db, seeds, source, options, evidence, identity, anchor, decoded, references,
                   starts, reference, coverage, progress, cancelled, publish):
    """Preserve explicit object/region and camera applications after broad recall."""
    from pipeline.matching.search import _models, _measure, _enabled, _best_pair, _candidate
    from pipeline.matching.search_subject import select_reference_track
    from pipeline.matching.search_cues import mask_at
    enabled = _enabled(evidence, options["focus"])
    tracker, flow = _models(evidence)
    point = options["reference"].get("subject_point")
    query, camera = _measure(decoded, tracker, flow, enabled, cancelled, region=options["reference"].get("region"),
                             point=(point["x"], point["y"]) if point else None, seed_time=anchor.time)
    query = select_reference_track(query, anchor.time, explicit=bool(point or options["reference"].get("region")))
    reference["region"] = (mask_at(query, anchor.time) or {}).get("box")
    result, cache = [], {}
    for index, proposed in enumerate(seeds[:MAX_REFINEMENTS]):
        if index >= INITIAL_REFINEMENTS and len(result) >= 3:
            break
        _cancel(cancelled)
        _verify_film(proposed)
        progress(f"Checking the cut in library scene {index + 1}/{min(len(seeds), MAX_REFINEMENTS)}")
        rows = media.samples(Path(proposed["film_path"]), max(proposed["t_start"], proposed["time"] - .25),
                             min(proposed["t_end"], proposed["time"] + 1.3), fps=12, cancelled=cancelled)
        coverage["refined_window_count"] += 1
        tracks, incoming_camera = _measure(rows, tracker, flow, enabled, cancelled, seed_time=proposed["time"])
        value = _best_pair(query, references, tracks, camera, incoming_camera, rows, proposed, options, enabled, cache)
        if value is not None:
            item = _candidate(value, proposed, source, starts, identity, proposed["film_title"])
            item["retrieved_channels"] = ["library_appearance"]
            result.append(item)
            publish(item)
    return result, enabled


def find(config, db, options, source, evidence, identity, progress, cancelled, on_candidate):
    from pipeline.matching.search import _references, _clip
    from pipeline.ingest.probe import _content_hash
    started = time.monotonic()
    film = cohort.resolve_film(db, source["film_id"])
    path = Path(film["path"])
    if _content_hash(path) != source["film_id"]:
        raise ValueError("Reference source identity changed")
    progress("Reading the reference and indexed library")
    anchor, decoded, references, starts = _references(path, source, options, cancelled)
    reference = _clip("reference", source, starts[anchor.time], anchor.end, anchor.time, options["reference"].get("region"))
    snapshot = identity["library"]
    scope = set(options["film_ids"] or [row["film_id"] for row in snapshot["films"]])
    if not options["include_source_film"]:
        scope.discard(source["film_id"])
    coverage = {"source": "library_keyframes", "film_count": len(scope), "frame_count": snapshot["frame_count"],
                "shot_count": snapshot["unit_count"], "retrieved_shot_count": 0, "screened_frame_count": 0,
                "shortlist_count": 0, "refined_window_count": 0}
    result, notices, summary, enabled = [], [], None, set()
    def publish(candidate):
        _cancel(cancelled)
        if library_retrieval.library_identity(config, db) != snapshot:
            raise ValueError("Library changed during this search. Start a new search.")
        if on_candidate:
            on_candidate(deepcopy(candidate))
        _cancel(cancelled)
    if references:
        seeds = library_retrieval.retrieve(config, db, source, anchor.time, options, snapshot, cancelled)
        coverage["retrieved_shot_count"] = len(seeds)
        explicit = bool(options["reference"].get("subject_point") or options["reference"].get("region"))
        progress("Identifying the foreground people")
        detector = PeopleEvidence(config, people.Detector(config))
        if detector.profile != identity["people"]:
            raise ValueError("People model changed after this search was queued")
        selected = [] if explicit else detector.describe(anchor.image)["selected"]
        if len(selected) > people.MAX_PEOPLE:
            # Never truncate a crowd into a supported group or compensate with
            # camera motion when the complete foreground arrangement is unknown.
            notices.append(f"This moment has more than {people.MAX_PEOPLE} prominent people. Choose a tighter moment or mark a subject to compare.")
        elif selected:
            summary = {"kind": "people", "count": len(selected)}
            reference["region"] = group_box(selected)
            reference_people = {anchor.time: selected}
            for ref in references:
                if ref.time not in reference_people:
                    reference_people[ref.time] = detector.describe(ref.image)["selected"]
            # Nearby timing must preserve the same foreground group count.
            references = [ref for ref in references if len(reference_people[ref.time]) == len(selected)]
            proposals, checked = _screen(seeds, selected, detector, progress, cancelled)
            coverage.update(screened_frame_count=checked, shortlist_count=len(proposals))
            enabled = {"layout", "shape"}
            result = _people_pairs(proposals, references, reference_people, source, options, identity, starts,
                                   detector, coverage, progress, cancelled, publish)
            if not result:
                notices.append(f"No verified arrangement of {len(selected)} prominent people in the checked scenes. Try another moment.")
        else:
            coverage["shortlist_count"] = min(len(seeds), MAX_REFINEMENTS)
            result, enabled = _generic_pairs(config, db, seeds, source, options, evidence, identity, anchor, decoded,
                                             references, starts, reference, coverage, progress, cancelled, publish)
    else:
        notices.append("Choose a moment with at least half a second of footage before the cut.")
    _cancel(cancelled)
    if library_retrieval.library_identity(config, db) != snapshot:
        raise ValueError("Library changed during this search. Start a new search.")
    result.sort(key=lambda item: (-item["score"], item["id"]))
    return {"candidates": result, "profile_id": identity, "cohort_id": options["cohort_id"],
            "reference": reference, "reference_summary": summary, "coverage": coverage,
            "focus": options["focus"], "timing": options["timing"], "notices": notices,
            "evaluated_channels": sorted(enabled), "available_channels": sorted(enabled),
            "elapsed_seconds": time.monotonic() - started,
            "message": "Compare the played transitions; matching the arrangement does not verify an action or dance step."}
