"""Explicit, immutable subsets and independently rebuildable matching profiles."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from lancedb.expr import col, lit

from pipeline.ingest.probe import _content_hash
from pipeline.lab.media import resolve_film


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, allow_nan=False)


def root(config):
    return config.paths.assets_dir / "matching"


def cohort_path(config, identity):
    if not re.fullmatch(r"cohort-[a-f0-9]{16}", identity):
        raise ValueError("Unknown matching subset")
    return root(config) / "cohorts" / identity


def load(config, identity):
    document = read(cohort_path(config, identity) / "manifest.json")
    payload = {k: v for k, v in document.items() if k != "id"}
    if (
        document.get("id") != f"cohort-{digest(payload)[:16]}"
        or document["id"] != identity
    ):
        raise ValueError("Matching subset manifest changed; prepare it again")
    if (
        not 1 <= len(document["units"]) <= 200
        or len(document["motion_unit_ids"]) > 80
        or len(document["frames"]) > 600
    ):
        raise ValueError("Matching subset exceeds its bounded contract")
    return document


def unit(db, identity):
    rows = (
        db.open_table("units")
        .search()
        .where(col("unit_id") == lit(identity))
        .select(["unit_id", "film_id", "t_start", "t_end", "caption"])
        .limit(2)
        .to_list()
    )
    if len(rows) != 1:
        raise ValueError("The reference shot is no longer in the published index")
    return rows[0]


def verify(config, db, document):
    for film in document["films"]:
        current = resolve_film(db, film["film_id"])
        path = Path(current["path"])
        if not path.is_file() or _content_hash(path) != film["film_id"]:
            raise ValueError(
                "A matching source is unavailable or changed; restore it or prepare a new subset"
            )
    for row in document["units"]:
        current = unit(db, row["unit_id"])
        if any(current[key] != row[key] for key in ("film_id", "t_start", "t_end")):
            raise ValueError(
                "Matching subset is stale after reingestion; prepare a new subset"
            )


def prepare(config, db):
    """Deterministic balanced sample, including existing diagnostic positives/negatives."""
    import yaml

    cases = yaml.safe_load(
        (Path(__file__).parents[1] / "eval" / "match_cut_cases.yaml").read_text()
    )
    seeds = {
        item["unit_id"]
        for case in cases["cases"]
        for item in [case["reference"], *case["judgments"]]
    }
    rows = (
        db.open_table("units")
        .search()
        .select(
            ["unit_id", "film_id", "t_start", "t_end", "caption", "energy", "framing"]
        )
        .limit(100000)
        .to_list()
    )
    rows = [row for row in rows if row["t_end"] - row["t_start"] >= 2]
    all_films = db.open_table("films").search().limit(2000).to_list()
    available = {
        row["film_id"]: row for row in all_films if Path(row["path"]).is_file()
    }
    seeded = sorted(
        {
            row["film_id"]
            for row in rows
            if row["unit_id"] in seeds and row["film_id"] in available
        }
    )
    films = (
        seeded
        + [
            key
            for key in sorted(available, key=lambda key: available[key]["title"])
            if key not in seeded
        ][: max(0, 8 - len(seeded))]
    )
    selected = [
        row for row in rows if row["unit_id"] in seeds and row["film_id"] in films
    ]
    chosen = {row["unit_id"] for row in selected}
    buckets = {
        film: sorted(
            [
                row
                for row in rows
                if row["film_id"] == film and row["unit_id"] not in chosen
            ],
            key=lambda row: digest(row["unit_id"]),
        )
        for film in films
    }
    # Round-robin films and framing/energy strata, independent of challenger scores.
    while len(selected) < 200 and any(buckets.values()):
        for film in films:
            if buckets[film] and len(selected) < 200:
                counts = {}
                for row in selected:
                    key = (row["framing"], row["energy"])
                    counts[key] = counts.get(key, 0) + 1
                index = min(
                    range(len(buckets[film])),
                    key=lambda i: counts.get(
                        (buckets[film][i]["framing"], buckets[film][i]["energy"]), 0
                    ),
                )
                selected.append(buckets[film].pop(index))
    if not selected:
        raise ValueError("No accessible indexed shots for the experiment")
    frames = []
    for row in selected:
        matches = (
            db.open_table("frames")
            .search()
            .where(col("unit_id") == lit(row["unit_id"]))
            .select(
                [
                    "frame_id",
                    "unit_id",
                    "frame_index",
                    "timestamp",
                    "path",
                    "visual_encoder",
                ]
            )
            .limit(3)
            .to_list()
        )
        for frame in matches:
            path = Path(frame.pop("path"))
            if not path.is_file():
                raise ValueError("An indexed frame is missing; restore the frame cache")
            frame["image_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            frames.append(frame)
    motion = sorted(
        selected,
        key=lambda row: (
            row["energy"] not in {"kinetic", "moderate"},
            digest(row["unit_id"]),
        ),
    )[:80]
    payload = {
        "schema_version": 1,
        "selection": "balanced-film-framing-energy-v1",
        "units": selected,
        "frames": frames,
        "motion_unit_ids": [row["unit_id"] for row in motion],
        "films": [{"film_id": key, "title": available[key]["title"]} for key in films],
    }
    identity = f"cohort-{digest(payload)[:16]}"
    document = {"id": identity, **payload}
    verify(config, db, document)
    destination = cohort_path(config, identity) / "manifest.json"
    if not destination.exists():
        write_new(destination, document)
    # Proposed references are deliberately ungraded; they are not human acceptance.
    review = {
        "cohort_id": identity,
        "status": "proposed-unreviewed",
        "visual": [],
        "motion": [],
    }
    visual_refs = [case["reference"] for case in cases["cases"]]
    visual_refs += [
        {"unit_id": row["unit_id"], "frame_index": 0}
        for row in selected
        if row["unit_id"] not in seeds
    ]
    review["visual"] = [
        {**row, "split": "review", "preference": None, "useful": None}
        for row in visual_refs[:12]
    ]
    review["motion"] = [
        {
            "unit_id": row["unit_id"],
            "split": "review",
            "preference": None,
            "useful": None,
        }
        for row in motion[:8]
    ]
    review["development_unit_ids"] = [
        row["unit_id"]
        for row in motion[8:28]
        if row["unit_id"] not in {x["unit_id"] for x in review["visual"]}
    ]
    review_path = destination.parent / "review-proposed.json"
    if not review_path.exists():
        write_new(review_path, review)
    return document


def listed(config):
    result = []
    for path in sorted((root(config) / "cohorts").glob("cohort-*/manifest.json")):
        try:
            item = load(config, path.parent.name)
            from pipeline.matching.service import profile
            readiness = {}
            notes = {}
            for channel in ("image", "camera", "subject"):
                try:
                    profile(config, item["id"], channel)
                    readiness[channel] = True
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    readiness[channel] = False
                    notes[channel] = str(exc)
            motion_examples = []
            motion_path = path.parent / "motion" / "manifest.json"
            if motion_path.is_file():
                from pipeline.matching.motion import descriptor
                import numpy as np

                motion_rows = read(motion_path)
                choices = []
                units = {row["unit_id"]: row for row in item["units"]}
                for window in motion_rows["windows"]:
                    measured = window["samples"][-6:]
                    value = descriptor(measured, "camera")
                    if value is not None:
                        choices.append(
                            (
                                float(np.linalg.norm(value)),
                                {
                                    **units[window["unit_id"]],
                                    "reference_time": measured[-1]["end"] - 0.05,
                                },
                            )
                        )
                motion_examples = [
                    row for _, row in sorted(choices, key=lambda pair: -pair[0])[:6]
                ]
            result.append(
                {
                    "id": item["id"],
                    "shot_count": len(item["units"]),
                    "motion_count": len(item["motion_unit_ids"]),
                    "films": item["films"],
                    "examples": item["units"][:6],
                    "motion_examples": motion_examples,
                    "visual_ready": readiness["image"],
                    "motion_ready": readiness["camera"],
                    "subject_ready": readiness["subject"],
                    "shape_ready": readiness["subject"] or readiness["image"],
                    "subject_note": notes.get("subject"),
                }
            )
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return result
