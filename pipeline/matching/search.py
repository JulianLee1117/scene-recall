"""Scene-based Match search with pinned evidence and bounded refinement.

Independent cue retrieval chooses a shared shortlist. Each incoming window is
decoded and tracked once; every final cue then proves the same exact cut pair.
The older Lab service remains available for historical jobs and comparisons.
"""
from __future__ import annotations

from pathlib import Path
from copy import deepcopy
import time

import numpy as np

from pipeline.matching import cohort, media, motion, subjects
from pipeline.matching.contracts import SearchRequest
from pipeline.matching.search_cues import CUE_ORDER, camera_window, mask_at, rank_pair, score_pair, shared_shortlist
from pipeline.matching.search_subject import select_reference_track

CONTRACT = "scene-match-library-people-exact-pair-v4"
MAX_REFINEMENTS = 10
INITIAL_REFINEMENTS = 5


def _request(request):
    value = request.model_dump() if isinstance(request, SearchRequest) else dict(request)
    identity = value.pop("profile_id", None)
    return SearchRequest.model_validate(value).model_dump(), identity


def _profile(config, subset, name):
    path = cohort.cohort_path(config, subset["id"]) / name / "manifest.json"
    if not path.is_file():
        return None
    document = cohort.read(path)
    if (not document.get("complete") or document.get("cohort_id") != subset["id"]
            or document.get("id") != cohort.digest({key: value for key, value in document.items() if key != "id"})):
        raise ValueError("Prepared Match evidence changed or is incomplete. Prepare a new profile.")
    windows = document.get("windows", [])
    expected = set(subset["motion_unit_ids"])
    if len(windows) != len(expected) or len(windows) != document.get("expected_rows") or {row["unit_id"] for row in windows} != expected:
        raise ValueError("Prepared Match evidence does not cover its declared windows")
    if name == "subjects" and any(track.get("profile_id") != document["profile"]["id"]
                                  for row in windows for track in row["tracks"]):
        raise ValueError("Prepared Match tracks contain incompatible profiles")
    return document


def library_profile(config, db):
    """Explicit local preparation enables bounded library-backed discovery."""
    if config is None:
        return None
    from pipeline.matching import people, library_retrieval
    if not (people.model_directory(config) / "profile.json").is_file():
        return None
    return {"people": people.load_profile(config)["id"],
            "library": library_retrieval.library_identity(config, db)}


def _resolve(config, db, request):
    options, pinned = _request(request)
    subset = cohort.load(config, options["cohort_id"])
    library = library_profile(config, db) if options["focus"] == "auto" else None
    source = cohort.unit(db, options["reference"]["unit_id"])
    if not source["t_start"] <= options["reference"]["time"] < source["t_end"]:
        raise ValueError("Choose a reference moment inside its indexed shot")
    available = {row["film_id"] for row in library["library"]["films"]} if library else {row["film_id"] for row in subset["units"]}
    if options["film_ids"] and not set(options["film_ids"]).issubset(available):
        raise ValueError("The selected films are outside the available indexed library" if library else
                         "The selected films are outside the prepared Match subset")
    evidence = {}
    wanted = ("motion",) if options["focus"] == "camera" else ("subjects", "motion") if options["focus"] in {"auto", "subject"} else ("subjects",)
    for name in wanted:
        found = _profile(config, subset, name)
        if found is not None:
            evidence[name] = found
    required = "motion" if options["focus"] == "camera" else "subjects"
    if (options["focus"] != "auto" and required not in evidence) or not evidence:
        raise ValueError("No compatible Match evidence is prepared for this focus")
    if "subjects" in evidence and "motion" in evidence:
        if evidence["subjects"]["profile"]["flow_profile_id"] != evidence["motion"]["profile"]["id"]:
            raise ValueError("Subject and camera evidence use incompatible flow profiles")
    identity = {"search_contract": CONTRACT, **{name: value["id"] for name, value in evidence.items()}, **(library or {})}
    if pinned is not None and pinned != identity:
        raise ValueError("Match profiles changed after this search was queued. Start a new search.")
    return options, subset, source, evidence, identity


def validate_request(config, db, request):
    """Validate public input and freeze the identities required by the queued job."""
    return _resolve(config, db, request)[-1]


def eligible_source(row, reference, options):
    if row["unit_id"] == reference["unit_id"]:
        return False
    if options["film_ids"] and row["film_id"] not in options["film_ids"]:
        return False
    if row["film_id"] == reference["film_id"]:
        if not options["include_source_film"]:
            return False
        if max(row["t_start"], reference["t_start"]) < min(row["t_end"], reference["t_end"]):
            return False
    return row["t_end"] - row["t_start"] >= options["min_incoming_seconds"] - 1e-6


class _SharedFlow:
    """Keep one window's flow arrays for both mask and global camera descriptors."""
    def __init__(self, wrapped):
        self.wrapped, self.profile, self.pairs = wrapped, wrapped.profile, {}

    def clear(self):
        self.pairs.clear()

    def pair(self, first, second):
        key = (id(first), id(second))
        if key not in self.pairs:
            self.pairs[key] = (first, second, self.wrapped.pair(first, second))
        return self.pairs[key][2]


def _models(evidence):
    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    prepared = evidence.get("subjects")
    directory = prepared["flow_model_directory"] if prepared else evidence["motion"]["model_directory"]
    flow = _SharedFlow(motion.Flow(Path(directory), device))
    if "motion" in evidence and flow.profile != evidence["motion"]["profile"]:
        raise ValueError("Camera model changed after evidence preparation")
    tracker = subjects.Tracker(Path(prepared["checkpoint_directory"]), flow, device) if prepared else None
    if tracker is not None and tracker.profile != prepared["profile"]:
        raise ValueError("Subject model changed after evidence preparation")
    return tracker, flow


def _measure(rows, tracker, flow, enabled, cancelled, *, region=None, point=None, seed_time=None):
    flow.clear()
    tracks = tracker.describe(rows, region=region, point=point, seed_time=seed_time,
                              cancelled=cancelled, measure_motion="subject" in enabled)["tracks"] if tracker is not None and enabled.intersection({"position", "shape", "subject"}) and len(rows) >= 2 else []
    camera = motion.sequence(flow, rows, cancelled, verify_photometric=True) if "camera" in enabled else []
    flow.clear()
    return tracks, camera


def _enabled(evidence, focus):
    result = set()
    if "subjects" in evidence:
        result.update(("position", "shape"))
        if focus in {"auto", "subject"}:
            result.add("subject")
    if "motion" in evidence:
        result.add("camera")
    return result


def _references(path, source, options, cancelled):
    timestamp = options["reference"]["time"]
    anchor = media.at(path, timestamp, source["t_start"], source["t_end"], cancelled)
    nearby = options["timing"] == "nearby"
    lower = max(source["t_start"], anchor.time - (1 if nearby else 0))
    upper = min(source["t_end"], anchor.time + 1) if nearby else anchor.end
    rows = media.samples(path, max(source["t_start"], lower - 1), min(source["t_end"], upper + .05), fps=6, cancelled=cancelled)
    rows = sorted([row for row in rows if abs(row.time - anchor.time) > 1e-7] + [anchor], key=lambda row: row.time)
    choices = [anchor]
    nearby_rows = [row for row in rows if lower <= row.time < upper]
    if nearby and nearby_rows:
        choices.extend(nearby_rows[int(index)] for index in np.linspace(0, len(nearby_rows) - 1, 3))
    choices = list({row.time: row for row in choices}.values())
    starts = {row.time: media.outgoing_start(path, row, source["t_start"], source["t_end"], cancelled) for row in choices}
    return anchor, rows, [row for row in choices if row.end - starts[row.time] >= .5 - 1e-6], starts


def _coarse(units, subject_windows, camera_windows, query, reference_camera, references, enabled, focus, options, cache, cancelled):
    channels = {code: [] for code in CUE_ORDER if code in enabled and (focus == "auto" or code == focus)}
    if not channels:
        return []
    for row in units:
        subjects._cancel(cancelled)
        tracks = subject_windows.get(row["unit_id"], {}).get("tracks", [])
        cameras = camera_windows.get(row["unit_id"], {}).get("samples", [])
        best = {}
        # Camera entries can have times outside the sampled masks; those pairs
        # receive only camera evidence, never a nearest mask's position or shape.
        times = {frame["time"]: frame["end"] for track in tracks for frame in track["frames"]}
        times.update({item["start"]: item["end"] for item in cameras if item["start"] not in times})
        for timestamp, end in times.items():
            if row["t_end"] - timestamp < options["min_incoming_seconds"] - 1e-6:
                continue
            sample = media.Sample(timestamp, end, None)
            for track in tracks or [None]:
                for ref in references:
                    cues = score_pair(ref, sample, query, track, reference_camera, cameras, enabled=channels, cache=cache)
                    value = rank_pair(cues, focus)
                    if value is None:
                        continue
                    for cue in cues:
                        proposal = {"score": value["score"], "unit": row, "time": timestamp,
                                    "region": (mask_at(track, timestamp) or {}).get("box") if value["primary_cue"] != "camera" else None,
                                    "cue": value["primary_cue"]}
                        if cue["code"] not in best or proposal["score"] > best[cue["code"]]["score"]:
                            best[cue["code"]] = proposal
        for code, proposal in best.items():
            channels[code].append(proposal)
    return shared_shortlist(channels, MAX_REFINEMENTS)


def _best_pair(query, references, tracks, reference_camera, incoming_camera, rows, unit, options, enabled, cache):
    best = None
    for sample in rows:
        if unit["t_end"] - sample.time < options["min_incoming_seconds"] - 1e-6:
            continue
        for track in tracks or [None]:
            for ref in references:
                cues = score_pair(ref, sample, query, track, reference_camera, incoming_camera, enabled=enabled, cache=cache)
                value = rank_pair(cues, options["focus"])
                if value is None:
                    continue
                choice = {**value, "cues": cues, "reference": ref, "sample": sample,
                          "outgoing_region": (mask_at(query, ref.time) or {}).get("box"),
                          "incoming_region": (mask_at(track, sample.time) or {}).get("box")}
                key = (value["score"], -abs(ref.time - options["reference"]["time"]), -sample.time)
                if best is None or key > best[0]:
                    best = key, choice
    return best[1] if best else None


def _clip(identity, unit, start, end, timestamp, region=None):
    return {"id": identity, "unit_id": unit["unit_id"], "film_id": unit["film_id"], "title": unit.get("caption", "")[:200],
            "source_start": start, "source_end": end, "reference_time": timestamp, "locked": False,
            "window_start": None, "window_end": None, "region": region, "crop": None}


def _candidate(item, row, source, starts, profile_id, film_title):
    ref, sample = item["reference"], item["sample"]
    outgoing = _clip("reference", source, starts[ref.time], ref.end, ref.time, item["outgoing_region"])
    incoming = _clip("candidate", row, sample.time, min(row["t_end"], sample.time + 1), sample.time, item["incoming_region"])
    identity = "match-" + cohort.digest({"profile": profile_id, "outgoing": outgoing, "incoming": incoming})[:24]
    incoming["id"] = identity
    return {"id": identity, "outgoing": outgoing, "incoming": incoming, "film_title": film_title,
            "score": float(item["score"]), "cues": item["cues"], "primary_cue": item["primary_cue"],
            "evidence": next(cue["description"] for cue in item["cues"] if cue["code"] == item["primary_cue"]),
            "reference_frame_pts": ref.time, "candidate_frame_pts": sample.time, "crop": None,
            "matched_channels": [cue["code"] for cue in item["cues"]],
            "incoming_authority": {"unit_id": row["unit_id"], "film_id": row["film_id"],
                                   "source_start": row["t_start"], "source_end": row["t_end"],
                                   "available_seconds": row["t_end"] - sample.time}}


def find(config, db, request, progress, cancelled=lambda: False, *, on_candidate=None):
    """Find exact pairs; optionally emit owned copies before final ranking.

    A callback runs once per accepted pair, in refinement order. It may add
    preview fields to its copy; it cannot change the engine's returned ranking.
    """
    started = time.monotonic()
    options, subset, source, evidence, identity = _resolve(config, db, request)
    if "library" in identity:
        from pipeline.matching.library_search import find as library_find
        return library_find(config, db, options, source, evidence, identity, progress, cancelled, on_candidate)
    progress("Checking source footage and prepared Match evidence")
    cohort.verify(config, db, subset)
    films = {}

    def film(film_id):
        if film_id not in films:
            films[film_id] = cohort.resolve_film(db, film_id)
        return films[film_id]

    path = Path(film(source["film_id"])["path"])
    from pipeline.ingest.probe import _content_hash
    if _content_hash(path) != source["film_id"]:
        raise ValueError("Reference source identity changed")
    subjects._cancel(cancelled)
    anchor, decoded, references, starts = _references(path, source, options, cancelled)
    reference = _clip("reference", source, starts[anchor.time], anchor.end, anchor.time, options["reference"].get("region"))
    enabled = _enabled(evidence, options["focus"])
    subject_windows = {row["unit_id"]: row for row in evidence.get("subjects", {}).get("windows", [])}
    camera_windows = {row["unit_id"]: row for row in evidence.get("motion", {}).get("windows", [])}
    prepared_ids = set(subject_windows) | set(camera_windows)
    units = [row for row in subset["units"] if row["unit_id"] in prepared_ids and eligible_source(row, source, options)]
    coverage = {"shot_count": len(subset["units"]), "window_count": len(prepared_ids), "film_count": len(subset["films"]),
                "eligible_window_count": len(units), "shortlist_count": 0, "refined_window_count": 0}
    notices, result = [], []
    if references and units:
        progress("Following the reference subject and measuring the cut")
        tracker, flow = _models(evidence)
        point = options["reference"].get("subject_point")
        query, reference_camera = _measure(decoded, tracker, flow, enabled, cancelled,
                                           region=options["reference"].get("region"),
                                           point=(point["x"], point["y"]) if point else None, seed_time=anchor.time)
        query = select_reference_track(query, anchor.time, explicit=bool(point or options["reference"].get("region")))
        reference["region"] = (mask_at(query, anchor.time) or {}).get("box", reference["region"])
        cache = {}
        if "subjects" in evidence and (query is None or not any((mask_at(query, ref.time) or {}).get("visible") for ref in references)):
            notices.append("No visible subject at this moment. Choose a subject or another moment.")
            enabled.difference_update(("position", "shape", "subject"))
        if "subject" in enabled and not any(
                boundary is not None and any(value["speed"] >= .003 and value["phases"] is not None for value in boundary["channels"].values())
                for ref in references for boundary in [subjects._boundary_window(query, ref.end, True, cache)]):
            notices.append("No clear subject movement at this moment. Position and shape can still be compared.")
            enabled.remove("subject")
        if "camera" in enabled and not any(camera_window(reference_camera, ref.end, True, cache, ref.time) is not None for ref in references):
            notices.append("No clear camera movement at this moment.")
            enabled.remove("camera")
        progress("Comparing independent cues across the prepared shots")
        shortlist = _coarse(units, subject_windows, camera_windows, query, reference_camera, references,
                            enabled, options["focus"], options, cache, cancelled)
        coverage["shortlist_count"] = len(shortlist)
        for index, proposed in enumerate(shortlist):
            if index >= INITIAL_REFINEMENTS and len(result) >= 3:
                break
            subjects._cancel(cancelled)
            row = proposed["unit"]
            progress(f"Checking the same cut pair for every cue: {index + 1}/{len(shortlist)}")
            rows = media.samples(Path(film(row["film_id"])["path"]), max(row["t_start"], proposed["time"] - .25),
                                 min(row["t_end"], proposed["time"] + 1.3), fps=12, cancelled=cancelled)
            coverage["refined_window_count"] += 1
            if len(rows) < 2:
                continue
            tracks, camera = _measure(rows, tracker, flow, enabled, cancelled, region=proposed["region"], seed_time=proposed["time"])
            best = _best_pair(query, references, tracks, reference_camera, camera, rows, row, options, enabled, cache)
            if best is not None:
                item = _candidate(best, row, source, starts, identity, film(row["film_id"])["title"])
                item["retrieved_channels"] = proposed["retrieved_cues"]
                result.append(item)
                if on_candidate is not None:
                    subjects._cancel(cancelled)
                    on_candidate(deepcopy(item))
                    subjects._cancel(cancelled)
    if not references:
        notices.append("Choose a moment with at least half a second of footage before the cut.")
    result.sort(key=lambda item: (-item["score"], item["id"]))
    return {"candidates": result, "cohort_id": subset["id"], "profile_id": identity, "reference": reference,
            "focus": options["focus"], "timing": options["timing"], "coverage": coverage, "shot_count": len(subset["units"]),
            "available_channels": [code for code in CUE_ORDER if code in _enabled(evidence, options["focus"])],
            "evaluated_channels": [code for code in CUE_ORDER if code in enabled], "notices": notices,
            "elapsed_seconds": time.monotonic() - started,
            "message": "Compare the played transitions; these are experimental suggestions." if result else "No supported matches in this subset for the requested focus and duration."}
