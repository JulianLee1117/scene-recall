"""Explicit model preparation and bounded cohort derivation; never runs on API import."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import uuid

from lancedb.expr import col, lit

from pipeline.matching import cohort
from pipeline.matching.media import samples
from pipeline.matching.motion import Flow, prepare_model, sequence


def commit(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".partial.json")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def visual(config, db, document, checkpoint, device):
    from pipeline.experiments.dense_geometry import (
        checkpoint_profile,
        DinoV3LocalAdapter,
        extract_bundle,
    )

    profile = checkpoint_profile(checkpoint, "facebook/dinov3-vits16-pretrain-lvd1689m")
    adapter = DinoV3LocalAdapter(checkpoint, profile, device=device)
    base = cohort.cohort_path(config, document["id"]) / "visual"
    run = uuid.uuid4().hex
    bundles = []
    for offset in range(0, len(document["frames"]), 200):
        frames = []
        for row in document["frames"][offset : offset + 200]:
            actual = (
                db.open_table("frames")
                .search()
                .where(col("frame_id") == lit(row["frame_id"]))
                .limit(1)
                .to_list()
            )
            if (
                not actual
                or hashlib.sha256(Path(actual[0]["path"]).read_bytes()).hexdigest()
                != row["image_sha256"]
            ):
                raise ValueError("Indexed source frame changed; prepare a new subset")
            frames.append(
                {"id": row["frame_id"], "path": actual[0]["path"], "source": row}
            )
        relative = f"{run}/{offset // 200:03d}"
        extract_bundle(
            adapter,
            {"schema_version": 1, "kind": "dense_geometry_images", "frames": frames},
            base / relative,
            image_base=Path.cwd(),
        )
        bundles.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(
                    (base / relative / "manifest.json").read_bytes()
                ).hexdigest(),
            }
        )
        print(
            f"Prepared visual evidence {min(offset + 200, len(document['frames']))}/{len(document['frames'])}",
            flush=True,
        )
    payload = {
        "cohort_id": document["id"],
        "checkpoint": str(checkpoint.resolve()),
        "profile": profile,
        "bundles": bundles,
        "expected_rows": len(document["frames"]),
        "complete": True,
    }
    commit(base / "manifest.json", {**payload, "id": cohort.digest(payload)})


def motion(config, db, document, device):
    from importlib.metadata import version
    from pipeline.matching.motion import CONTRACT

    runtime = {name: version(name) for name in ("torch", "torchvision", "numpy", "av")}
    generation = cohort.digest({"contract": CONTRACT, "runtime": runtime})[:12]
    model_dir = cohort.root(config) / "models" / f"raft-small-ctv2-{generation}"
    prepare_model(model_dir)
    model = Flow(model_dir, device)
    units = {row["unit_id"]: row for row in document["units"]}
    films = {
        row["film_id"]: cohort.resolve_film(db, row["film_id"])
        for row in document["films"]
    }
    evidence = []
    for i, identity in enumerate(document["motion_unit_ids"]):
        row = units[identity]
        center = (row["t_start"] + row["t_end"]) / 2
        start, end = max(row["t_start"], center - 2), min(row["t_end"], center + 2)
        decoded = samples(Path(films[row["film_id"]]["path"]), start, end, fps=6)
        measured = sequence(model, decoded)
        for item in measured:
            item["camera"], item["residual"] = (
                item["camera"].tolist(),
                item["residual"].tolist(),
            )
        evidence.append(
            {"unit_id": identity, "start": start, "end": end, "samples": measured}
        )
        print(
            f"Prepared motion evidence {i + 1}/{len(document['motion_unit_ids'])}",
            flush=True,
        )
    payload = {
        "cohort_id": document["id"],
        "model_directory": str(model_dir.resolve()),
        "profile": model.profile,
        "expected_rows": len(document["motion_unit_ids"]),
        "complete": True,
        "windows": evidence,
    }
    commit(
        cohort.cohort_path(config, document["id"]) / "motion" / "manifest.json",
        {**payload, "id": cohort.digest(payload)},
    )


def main():
    from pipeline.config import load_config
    from pipeline.index.writer import open_db
    from pipeline.ingest.locks import global_ingest_lock

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["cohort", "motion", "visual", "status"])
    parser.add_argument("--cohort")
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    args = parser.parse_args()
    config = load_config()
    if args.action == "status":
        print(json.dumps(cohort.listed(config), indent=2))
        return
    db = open_db(config)
    if args.action == "cohort":
        document = cohort.prepare(config, db)
        print(
            json.dumps(
                {
                    "id": document["id"],
                    "shots": len(document["units"]),
                    "films": len(document["films"]),
                }
            )
        )
        return
    if not args.cohort:
        parser.error("--cohort is required")
    document = cohort.load(config, args.cohort)
    cohort.verify(config, db, document)
    with global_ingest_lock(config.paths.assets_dir):
        if args.action == "visual":
            if not args.checkpoint:
                parser.error(
                    "--checkpoint must name an approved local DINOv3 checkpoint; no implicit download"
                )
            visual(config, db, document, args.checkpoint, args.device)
        else:
            motion(config, db, document, args.device)


if __name__ == "__main__":
    main()
