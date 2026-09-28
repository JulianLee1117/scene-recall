"""Reference-to-candidate matching. Lab is an adapter, not the retrieval engine."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
from pathlib import Path
import time

import numpy as np
from lancedb.expr import col, lit

from pipeline.experiments.region_geometry import (
    Box,
    Picture,
    propose_crop,
    NoFeasibleCrop,
)
from pipeline.matching import cohort, media, motion, visual


def _native_outgoing_start(path, frame, source, options, cancelled):
    """Share exact source-frame handle resolution across scorers and assembly."""
    cache = options.setdefault("_context", {}).setdefault("outgoing_starts", {})
    key = (str(path), frame.time, frame.end, source["t_start"], source["t_end"])
    if key not in cache:
        cache[key] = media.outgoing_start(path, frame, source["t_start"], source["t_end"], cancelled)
    return cache[key]


def profile(config, identity, mode):
    path = (
        cohort.cohort_path(config, identity)
        / ("visual" if mode == "image" else "subjects" if mode in {"subject", "shape"} else "motion")
        / "manifest.json"
    )
    if not path.is_file():
        raise ValueError(
            "This subset has no prepared visual profile. Prepare approved DINOv3 weights first."
            if mode == "image"
            else "Prepare tracked subjects for this subset first." if mode == "subject"
            else "Prepare motion evidence for this subset first."
        )
    document = cohort.read(path)
    if (
        not document.get("complete")
        or document.get("cohort_id") != identity
        or document.get("id")
        != cohort.digest({k: v for k, v in document.items() if k != "id"})
    ):
        raise ValueError(
            "The matching evidence is incomplete or corrupt; rebuild the profile"
        )
    return document


def reference(db, document, options):
    clip = next(
        (row for row in document["clips"] if row["id"] == options["reference_clip_id"]),
        None,
    )
    if clip is None or not clip.get("unit_id"):
        raise ValueError("Choose an indexed reference shot first")
    source = cohort.unit(db, clip["unit_id"])
    if (
        source["film_id"] != clip["film_id"]
        or clip["source_start"] < source["t_start"]
        or clip["source_end"] > source["t_end"]
    ):
        raise ValueError("Reference selection must stay within its indexed shot")
    timestamp = clip.get("reference_time")
    timestamp = (
        (clip["source_start"] + clip["source_end"]) / 2
        if timestamp is None
        else timestamp
    )
    if not clip["source_start"] <= timestamp < clip["source_end"]:
        raise ValueError("Mark a reference before the end of the selected shot")
    if clip.get("crop") and not options.get("allow_reframing"):
        raise ValueError(
            "This reference is already cropped. Enable Allow reframing to align its displayed image, or reset its crop."
        )
    lower, upper = clip.get("window_start"), clip.get("window_end")
    if lower is not None and (upper - lower > 4 or not lower <= timestamp < upper):
        raise ValueError(
            "Choose a reference window of at most four seconds containing the reference"
        )
    if (
        not options.get("focus")
        and options["mode"] == "movement"
        and options["movement"] == "subject"
        and not clip.get("region")
    ):
        raise ValueError("Use Match this area to select the moving subject")
    return clip, source, timestamp


def validate_request(config, db, document, options):
    subset = cohort.load(config, options["cohort_id"])
    reference(db, document, options)
    scope = set(document["film_ids"])
    if scope and not any(row["film_id"] in scope for row in subset["units"]):
        raise ValueError("The selected films are outside this prepared subset")
    if options.get("focus"):
        from pipeline.matching.transitions import CONTRACT
        return {"scorer": CONTRACT, **{key: value["id"] for key, value in profiles(config, subset["id"], options["focus"]).items()}}
    return profile(config, subset["id"], options["mode"])["id"]


def profiles(config, identity, focus):
    """Resolve explicit channels once; missing/corrupt channels are never mislabeled."""
    requested = ["subject", "shape", "camera", "image"] if focus == "auto" else ["shape", "image"] if focus == "image" else [focus]
    found = {}
    for name in requested:
        try:
            found[name] = profile(config, identity, name)
        except (OSError, ValueError, KeyError):
            if focus not in {"auto", "image"}:
                raise
    if not found:
        raise ValueError("No compatible matching evidence is prepared for this footage")
    return found


def _visual_rows(config, db, subset, evidence):
    from pipeline.experiments.dense_geometry import validate_profile

    validate_profile(evidence["profile"])
    base = cohort.cohort_path(config, subset["id"]) / "visual"
    expected = {row["frame_id"]: row for row in subset["frames"]}
    found = set()
    for bundle_ref in evidence["bundles"]:
        directory = (base / bundle_ref["path"]).resolve()
        if not directory.is_relative_to(base.resolve()):
            raise ValueError("Invalid descriptor bundle location")
        manifest = directory / "manifest.json"
        if hashlib.sha256(manifest.read_bytes()).hexdigest() != bundle_ref["sha256"]:
            raise ValueError("Descriptor bundle changed")
        bundle = cohort.read(manifest)
        if (
            bundle["profile"] != evidence["profile"]
            or not bundle["complete"]
            or bundle["coverage_digest"] != cohort.digest(bundle["frames"])
        ):
            raise ValueError("Incompatible descriptor bundle")
        for item in bundle["frames"]:
            frame = expected.get(item["id"])
            if (
                frame is None
                or item["id"] in found
                or item["image_sha256"] != frame["image_sha256"]
            ):
                raise ValueError("Descriptor coverage does not match the subset")
            current = (
                db.open_table("frames")
                .search()
                .where(col("frame_id") == lit(frame["frame_id"]))
                .select(["unit_id", "timestamp", "path"])
                .limit(1)
                .to_list()
            )
            if (
                not current
                or current[0]["unit_id"] != frame["unit_id"]
                or current[0]["timestamp"] != frame["timestamp"]
            ):
                raise ValueError("Indexed frame evidence changed; prepare a new subset")
            if (
                hashlib.sha256(Path(current[0]["path"]).read_bytes()).hexdigest()
                != frame["image_sha256"]
            ):
                raise ValueError("Indexed frame pixels changed; prepare a new subset")
            found.add(item["id"])
            path = (directory / item["descriptor_file"]).resolve()
            if (
                not path.is_relative_to(directory)
                or hashlib.sha256(path.read_bytes()).hexdigest()
                != item["descriptor_sha256"]
            ):
                raise ValueError("Descriptor cache is missing or corrupt")
            with np.load(path, allow_pickle=False) as data:
                features = data["features"]
            if not np.isfinite(features).all() or list(features.shape) != item["shape"]:
                raise ValueError("Invalid dense features")
            yield (
                frame,
                features,
                Box(**item["viewport"]),
                Picture(**item["source_picture"]),
            )
    if found != set(expected) or len(found) != evidence["expected_rows"]:
        raise ValueError("Incomplete descriptor coverage")


def _image_candidates(
    config,
    db,
    subset,
    evidence,
    clip,
    source,
    timestamp,
    options,
    output,
    progress,
    cancelled,
):
    from pipeline.experiments.dense_geometry import DinoV3LocalAdapter
    import torch

    model = DinoV3LocalAdapter(
        Path(evidence["checkpoint"]),
        evidence["profile"],
        device="cuda" if torch.cuda.is_available() else "cpu",
    )
    path = Path(cohort.resolve_film(db, source["film_id"])["path"])
    anchor = media.at(path, timestamp, source["t_start"], source["t_end"], cancelled)
    qfeatures, qview = model.extract(anchor.image)
    region = Box(**clip["region"]) if clip.get("region") else None
    ref_crop = Box(**clip["crop"]) if clip.get("crop") else None
    units = {row["unit_id"]: row for row in subset["units"]}
    ranked = {}
    for i, (frame, features, viewport, picture) in enumerate(
        _visual_rows(config, db, subset, evidence)
    ):
        if cancelled():
            from pipeline.lab.media import JobCancelled

            raise JobCancelled("Matching cancelled")
        if i % 20 == 0:
            progress(f"Comparing visual evidence {i + 1}/{len(subset['frames'])}")
        row = units[frame["unit_id"]]
        if row["film_id"] == source["film_id"] or (
            options.get("film_ids") and row["film_id"] not in options["film_ids"]
        ):
            continue
        comparison = visual.compare(
            qfeatures,
            qview,
            region,
            features,
            viewport,
            reference_picture=Picture(*anchor.image.size),
            candidate_picture=picture,
            output=output,
            allow_crop=options["allow_reframing"],
            reference_crop=ref_crop,
        )
        if comparison and (
            row["unit_id"] not in ranked
            or comparison["score"] > ranked[row["unit_id"]]["score"]
        ):
            ranked[row["unit_id"]] = {
                **comparison,
                "unit": row,
                "time": frame["timestamp"],
                "method": "dense",
            }
    # PE is an independent candidate source over the SAME cohort, not a dense gate.
    ref_frames = (
        db.open_table("frames")
        .search()
        .where(col("unit_id") == lit(source["unit_id"]))
        .limit(3)
        .to_list()
    )
    if ref_frames:
        ref = min(ref_frames, key=lambda row: abs(row["timestamp"] - timestamp))
        candidates = []
        for row in subset["frames"]:
            unit = units[row["unit_id"]]
            if unit["film_id"] == source["film_id"] or (
                options.get("film_ids") and unit["film_id"] not in options["film_ids"]
            ):
                continue
            actual = (
                db.open_table("frames")
                .search()
                .where(col("frame_id") == lit(row["frame_id"]))
                .limit(1)
                .to_list()
            )
            if not actual or actual[0]["visual_encoder"] != ref["visual_encoder"]:
                continue
            a, b = np.asarray(ref["visual_vec"]), np.asarray(actual[0]["visual_vec"])
            candidates.append(
                (float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8)), row)
            )
        pe_ids = []
        for _, frame in sorted(candidates, key=lambda item: -item[0]):
            if frame["unit_id"] not in pe_ids:
                pe_ids.append(frame["unit_id"])
        dense_ids = sorted(ranked, key=lambda key: -ranked[key]["score"])
        # Union bounded ranks; never combine raw PE and DINO vector scores.
        union = list(dict.fromkeys([*dense_ids[:10], *pe_ids[:10]]))
        union.sort(
            key=lambda key: (
                -(1 / (60 + dense_ids.index(key) + 1) if key in dense_ids else 0)
                - (1 / (60 + pe_ids.index(key) + 1) if key in pe_ids else 0)
            )
        )
    else:
        union = sorted(ranked, key=lambda key: -ranked[key]["score"])
    refs = [anchor]
    if clip.get("window_start") is not None and not clip["locked"]:
        refs = (
            media.samples(
                path,
                clip["window_start"],
                clip["window_end"],
                fps=1,
                cancelled=cancelled,
            )[:4]
            or refs
        )
    references = [(sample, *model.extract(sample.image)) for sample in refs]
    final = []
    for i, identity in enumerate(union[:options.get("candidate_budget", 10)]):
        progress(f"Finding the cut frame {i + 1}/{min(10, len(union))}")
        if identity not in ranked:
            continue
        seed = ranked[identity]
        row = seed["unit"]
        candidate_path = Path(cohort.resolve_film(db, row["film_id"])["path"])
        start = max(row["t_start"], seed["time"] - 2)
        end = min(row["t_end"], start + 4)
        decoded = media.samples(candidate_path, start, end, fps=4, cancelled=cancelled)
        best = None

        def inspect(sample):
            nonlocal best
            if sample.time + 0.5 > row["t_end"]:
                return
            features, view = model.extract(sample.image)
            for ref, rfeatures, rview in references:
                if ref.end - 0.5 < source["t_start"]:
                    continue
                if options.get("focus") and ref.end - _native_outgoing_start(path, ref, source, options, cancelled) < 0.5 - 1e-6:
                    continue
                value = visual.compare(
                    rfeatures,
                    rview,
                    region,
                    features,
                    view,
                    reference_picture=Picture(*ref.image.size),
                    candidate_picture=Picture(*sample.image.size),
                    output=output,
                    allow_crop=options["allow_reframing"],
                    reference_crop=ref_crop,
                )
                if value and (best is None or value["score"] > best["score"]):
                    best = {
                        **value,
                        "unit": row,
                        "sample": sample,
                        "reference": ref,
                        "evidence_kind": "visual correspondence",
                    }

        for sample in decoded:
            progress(f"Refining visual alignment in candidate {i + 1}")
            inspect(sample)
        if best:
            for sample in media.samples(
                candidate_path,
                max(start, best["sample"].time - 0.13),
                min(end, best["sample"].time + 0.13),
                native=True,
                cancelled=cancelled,
            ):
                inspect(sample)
            final.append(best)
    return final


def _motion_candidates(
    config,
    db,
    subset,
    evidence,
    clip,
    source,
    timestamp,
    options,
    output,
    progress,
    cancelled,
):
    import torch
    from pipeline.matching.transitions import motion_similarity, motion_components

    similarity = motion_similarity if options.get("focus") else motion.similarity

    model = motion.Flow(
        Path(evidence["model_directory"]),
        "cuda" if torch.cuda.is_available() else "cpu",
    )
    if model.profile != evidence["profile"]:
        raise ValueError("Motion model differs from prepared subset")
    units = {row["unit_id"]: row for row in subset["units"]}
    if {row["unit_id"] for row in evidence["windows"]} != set(
        subset["motion_unit_ids"]
    ) or len(evidence["windows"]) != evidence["expected_rows"]:
        raise ValueError("Incomplete motion coverage")
    path = Path(cohort.resolve_film(db, source["film_id"])["path"])
    anchor = media.at(path, timestamp, source["t_start"], source["t_end"], cancelled)
    lower = max(source["t_start"], anchor.end - 1)
    upper = anchor.end
    explore = clip.get("window_start") is not None and not clip["locked"]
    if explore:
        lower, upper = clip["window_start"], clip["window_end"]
        if options.get("focus"):
            lower = max(source["t_start"], lower - 1)
    progress("Measuring the reference movement")
    rows = media.samples(path, lower, upper, fps=6, cancelled=cancelled)
    measured = motion.sequence(model, rows, cancelled, verify_photometric=bool(options.get("focus")))
    region = Box(**clip["region"]) if clip.get("region") else None
    anchors = [anchor]
    if explore and len(rows) >= 6:
        eligible = [row for row in rows if row.time >= clip["window_start"]]
        anchors += [eligible[int(index)] for index in np.linspace(0, len(eligible) - 1, 3)] if eligible else []
    queries = []
    valid_anchors = 0
    for reference_frame in anchors:
        if options.get("focus") and reference_frame.end - _native_outgoing_start(path, reference_frame, source, options, cancelled) < 0.5 - 1e-6:
            continue
        valid_anchors += 1
        chunk = [
            item
            for item in measured
            if reference_frame.end - 1.001 <= item["start"]
            and item["end"] <= reference_frame.end
        ]
        query = motion.descriptor(chunk, options["movement"], region)
        if query is not None:
            queries.append((reference_frame, query))
    if not queries:
        raise ValueError(
            "Choose a moment with at least half a second of footage before the cut."
            if options.get("focus") and not valid_anchors
            else "Movement is too small or camera separation is uncertain here. Select a clearer moving moment or try Match image."
        )
    ranked = []
    for i, window in enumerate(evidence["windows"]):
        if i % 10 == 0:
            progress(f"Comparing movement {i + 1}/{len(evidence['windows'])}")
        row = units[window["unit_id"]]
        if row["film_id"] == source["film_id"] or (
            options.get("film_ids") and row["film_id"] not in options["film_ids"]
        ):
            continue
        values = [
            {
                **item,
                "camera": np.asarray(item["camera"]),
                "residual": np.asarray(item["residual"]),
            }
            for item in window["samples"]
        ]
        best = None
        for offset in range(max(0, len(values) - 3)):
            if options.get("focus") and row["t_end"] - values[offset]["start"] < 0.5 - 1e-6:
                continue
            chunk = [
                item
                for item in values[offset:]
                if item["end"] <= values[offset]["start"] + 1.001
            ]
            for candidate_region in visual.regions(region) if region else [None]:
                descriptor = motion.descriptor(
                    chunk, options["movement"], candidate_region
                )
                if descriptor is None:
                    continue
                reference_frame, query = max(
                    queries, key=lambda item: similarity(item[1], descriptor)
                )
                score = similarity(query, descriptor)
                if best is None or score > best["score"]:
                    best = {
                        "unit": row,
                        "time": values[offset]["start"],
                        "score": score,
                        "reference": reference_frame,
                        "query": query,
                        "region": asdict(candidate_region)
                        if candidate_region
                        else None,
                    }
        if best and best["score"] > 0:
            ranked.append(best)
    final = []
    limit = options.get("candidate_budget", 10)
    for i, chosen in enumerate(sorted(ranked, key=lambda row: -row["score"])[:limit]):
        progress(f"Checking movement at the cut {i + 1}/{min(10, len(ranked))}")
        row = chosen["unit"]
        anchor, query = chosen["reference"], chosen["query"]
        path = Path(cohort.resolve_film(db, row["film_id"])["path"])
        # Refine local timing using actual motion evidence, not caption or appearance.
        decoded = media.samples(
            path,
            max(row["t_start"], chosen["time"] - 0.2),
            min(row["t_end"], chosen["time"] + 1.2),
            fps=12,
            cancelled=cancelled,
        )
        measurements = motion.sequence(model, decoded, cancelled, verify_photometric=bool(options.get("focus")))
        candidate_region = Box(**chosen["region"]) if chosen["region"] else None
        choices = []
        for offset in range(min(5, len(measurements))):
            if options.get("focus") and row["t_end"] - decoded[offset].time < 0.5 - 1e-6:
                continue
            chunk = [
                item
                for item in measurements[offset:]
                if item["end"] <= measurements[offset]["start"] + 1.001
            ]
            value = motion.descriptor(chunk, options["movement"], candidate_region)
            if value is not None:
                available = queries if options.get("focus") else [(anchor, query)]
                for query_index, (_, possible) in enumerate(available):
                    choices.append((similarity(possible, value), offset, query_index))
        if not choices:
            continue
        score, offset, query_index = max(choices)
        if options.get("focus"):
            anchor, query = queries[query_index]
        if score <= 0:
            continue
        sample = decoded[offset]
        crop = None
        if options["allow_reframing"] and region and candidate_region:
            try:
                crop = propose_crop(
                    Picture(*anchor.image.size),
                    region,
                    Picture(*sample.image.size),
                    candidate_region,
                    output=output,
                    reference_crop=Box(**clip["crop"]) if clip.get("crop") else None,
                    max_upscale=2,
                ).as_document()
            except NoFeasibleCrop:
                continue
            if options["movement"] == "subject":
                # Velocities are normalized to the displayed picture after crop.
                qscale = np.array(
                    [crop["reference_crop"]["width"], crop["reference_crop"]["height"]]
                )
                cscale = np.array(
                    [crop["candidate_crop"]["width"], crop["candidate_crop"]["height"]]
                )
                selected_chunk = [
                    item
                    for item in measurements[offset:]
                    if item["end"] <= measurements[offset]["start"] + 1.001
                ]
                value = motion.descriptor(selected_chunk, "subject", candidate_region)
                score = similarity(query / qscale, value / cscale)
            score -= 0.15 * crop["crop_loss"] + 0.20 * crop["context_loss"]
            if score <= 0:
                continue
        final.append(
            {
                **chosen,
                "score": score,
                "sample": sample,
                "reference": anchor,
                "crop": crop,
                "evidence_kind": f"{options['movement']} movement",
                "components": motion_components(query, motion.descriptor(
                    [item for item in measurements[offset:] if item["end"] <= measurements[offset]["start"] + 1.001],
                    options["movement"], candidate_region)) if options.get("focus") else {},
            }
        )
    return final


def find(config, db, document, options, progress, cancelled=lambda: False):
    started = time.monotonic()
    subset = cohort.load(config, options["cohort_id"])
    progress("Checking source footage and prepared evidence")
    cohort.verify(config, db, subset)
    modern = bool(options.get("focus"))
    evidence = profiles(config, subset["id"], options["focus"]) if modern else profile(config, subset["id"], options["mode"])
    from pipeline.matching.transitions import CONTRACT
    identity = {"scorer": CONTRACT, **{key: value["id"] for key, value in evidence.items()}} if modern else evidence["id"]
    if options.get("profile_id") != identity:
        raise ValueError(
            "Matching profile changed after this job was queued; start a new search"
        )
    clip, source, timestamp = reference(db, document, options)
    if modern:
        clip = {**clip, "window_start": None, "window_end": None}
        if options.get("timing") == "nearby" and not clip["locked"]:
            clip.update(window_start=max(source["t_start"], timestamp - 1),
                        window_end=min(source["t_end"], timestamp + 1))
    reference_path = Path(cohort.resolve_film(db, source["film_id"])["path"])
    from pipeline.ingest.probe import _content_hash

    if _content_hash(reference_path) != source["film_id"]:
        raise ValueError("Reference source identity changed")
    output = (
        Picture(1920, 1080)
        if document["aspect_ratio"] == "16:9"
        else Picture(1080, 1920)
    )
    options = {**options, "film_ids": document["film_ids"], "_context": {}}
    function = _image_candidates if options["mode"] == "image" else _motion_candidates
    notices = []
    if modern:
        from pipeline.matching.transitions import fuse_channels
        channels = {}
        context = options["_context"]
        budgets = {key: 10 // len(evidence) + (i < 10 % len(evidence)) for i, key in enumerate(evidence)}
        for key, prepared in evidence.items():
            if cancelled():
                from pipeline.lab.media import JobCancelled
                raise JobCancelled("Matching cancelled")
            progress(f"Finding {key} matches")
            channel_options = {**options, "movement": "camera", "candidate_budget": budgets[key], "channel": key, "_context": context}
            if key in {"subject", "shape"}:
                from pipeline.matching.subject_service import candidates as function
            else:
                function = _image_candidates if key == "image" else _motion_candidates
            try:
                channels[key] = function(config, db, subset, prepared, clip, source, timestamp,
                                         channel_options, output, progress, cancelled)
            except ValueError as exc:
                # Auto may report a channel with unknown reference evidence;
                # explicit modes must surface its actual failure.
                if options["focus"] not in {"auto", "image"}:
                    raise
                notices.append(f"{key}: {exc}")
        found = fuse_channels(channels)
    else:
        found = function(
        config,
        db,
        subset,
        evidence,
        clip,
        source,
        timestamp,
        options,
        output,
        progress,
        cancelled,
        )
    result = []
    native_starts = {}
    insufficient_context = False
    for item in sorted(
        found, key=lambda item: (-item["score"], item["unit"]["unit_id"])
    )[:10]:
        row = item["unit"]
        ref = item["reference"]
        sample = item["sample"]
        if modern and ref.time not in native_starts:
            native_starts[ref.time] = _native_outgoing_start(reference_path, ref, source, options, cancelled)
        if modern and (ref.end - native_starts[ref.time] < 0.5 - 1e-6
                       or min(row["t_end"], sample.time + 1) - sample.time < 0.5 - 1e-6):
            insufficient_context = True
            continue
        outgoing = {
            **clip,
            "source_start": native_starts[ref.time] if modern else max(source["t_start"], ref.end - 1),
            "source_end": ref.end,
            "reference_time": ref.time,
            "window_start": None,
            "window_end": None,
        }
        incoming = {
            "id": "match-"
            + hashlib.sha256(f"{row['unit_id']}:{sample.time}".encode()).hexdigest()[
                :16
            ],
            "unit_id": row["unit_id"],
            "film_id": row["film_id"],
            "title": row["caption"][:200],
            "source_start": sample.time,
            "source_end": min(row["t_end"], sample.time + 1),
            "reference_time": sample.time,
            "window_start": None,
            "window_end": None,
            "locked": False,
            "region": item.get("region"),
            "crop": None,
        }
        if item.get("crop"):
            outgoing["crop"], incoming["crop"] = (
                item["crop"]["reference_crop"],
                item["crop"]["candidate_crop"],
            )
        film = cohort.resolve_film(db, row["film_id"])
        result.append(
            {
                "id": incoming["id"],
                "film_title": film["title"],
                "score": float(item["score"]),
                "evidence": item["evidence_kind"],
                "outgoing": outgoing,
                "incoming": incoming,
                "crop": item.get("crop"),
                "reference_frame_pts": ref.time,
                "candidate_frame_pts": sample.time,
                "components": item.get("components", {}),
                "matched_channels": item.get("matched_channels", []),
                "retrieved_channels": item.get("retrieved_channels", []),
            }
        )
    return {
        "candidates": result,
        "cohort_id": subset["id"],
        "profile_id": identity,
        "mode": options["mode"],
        "focus": options.get("focus"),
        "timing": options.get("timing"),
        "available_channels": list(evidence) if modern else [options["mode"]],
        "notices": notices,
        "reference_clip_id": clip["id"],
        "elapsed_seconds": time.monotonic() - started,
        "shot_count": len(subset["units"]),
        "message": "Compare the played transitions; these are experimental suggestions."
        if result
        else "Choose a moment with at least half a second of footage on each side of the cut."
        if insufficient_context
        else "No reliable matches in this subset. Try another reference or allow reframing.",
    }
