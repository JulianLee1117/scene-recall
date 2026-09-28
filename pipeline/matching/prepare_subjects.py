"""Explicit pinned SAM 2.1 preparation for the existing bounded motion cohort."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from pipeline.matching import cohort, media, motion, subjects
from pipeline.matching.prepare import commit


def prepare(config, db, document, checkpoint, device="cpu", *, output=None):
    """Resume complete windows and publish only a complete, versioned manifest."""
    if len(document["units"]) > 200 or len(document["motion_unit_ids"]) > 80:
        raise ValueError("Subject preparation requires the bounded matching cohort")
    cohort.verify(config, db, document)
    base = cohort.cohort_path(config, document["id"])
    movement = cohort.read(base / "motion" / "manifest.json")
    if (movement.get("cohort_id") != document["id"] or not movement.get("complete")
            or movement.get("id") != cohort.digest({k: v for k, v in movement.items() if k != "id"})):
        raise ValueError("Prepare a complete motion profile for this subset first")
    flow = motion.Flow(Path(movement["model_directory"]), device)
    subjects.prepare_model(checkpoint, flow.profile, device)
    tracker = subjects.Tracker(checkpoint, flow, device)
    output = Path(output) if output is not None else base / "subjects"
    windows, start_clock = [], time.perf_counter()
    units = {row["unit_id"]: row for row in document["units"]}
    for index, identity in enumerate(document["motion_unit_ids"]):
        unit = units[identity]
        center = (unit["t_start"] + unit["t_end"]) / 2
        start, end = max(unit["t_start"], center - 2), min(unit["t_end"], center + 2)
        key = cohort.digest({"cohort_id": document["id"], "profile_id": tracker.profile["id"],
                             "unit_id": identity, "start": start, "end": end})
        cache = output / "windows" / tracker.profile["id"] / f"{key}.json"
        if cache.exists():
            saved = cohort.read(cache)
            if saved.get("key") != key or saved.get("id") != cohort.digest({k: v for k, v in saved.items() if k != "id"}):
                raise ValueError("Cached subject window changed; remove it and prepare again")
            window = saved["window"]
        else:
            film = cohort.resolve_film(db, unit["film_id"])
            started = time.perf_counter()
            rows = media.samples(Path(film["path"]), start, end, fps=6)
            described = tracker.describe(rows, seed_time=center) if len(rows) >= 2 else {"tracks": []}
            window = {"unit_id": identity, "start": start, "end": end,
                      "tracks": described["tracks"], "seconds": time.perf_counter() - started}
            payload = {"key": key, "window": window}
            commit(cache, {**payload, "id": cohort.digest(payload)})
        windows.append(window)
        print(f"Prepared tracked subjects {index + 1}/{len(document['motion_unit_ids'])}: {len(window['tracks'])} tracks", flush=True)
    payload = {"cohort_id": document["id"], "checkpoint": str(checkpoint.resolve()),
               "checkpoint_directory": str(checkpoint.resolve()),
               "model_directory": str(checkpoint.resolve()),
               "flow_model_directory": movement["model_directory"], "flow_profile_id": flow.profile["id"],
               "profile": tracker.profile, "expected_rows": len(document["motion_unit_ids"]),
               "complete": True, "windows": windows}
    manifest = {**payload, "id": cohort.digest(payload)}
    commit(output / "manifest.json", manifest)
    print(json.dumps({"id": manifest["id"], "windows": len(windows),
                      "tracks": sum(len(w["tracks"]) for w in windows),
                      "seconds": time.perf_counter() - start_clock}), flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["checkpoint", "cohort"])
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--cohort")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--output", type=Path, help="Optional isolated experiment output directory")
    args = parser.parse_args()
    if args.action == "checkpoint":
        print(subjects.download_checkpoint(args.checkpoint))
        return
    if not args.cohort:
        parser.error("--cohort is required for cohort preparation")
    from pipeline.config import load_config
    from pipeline.index.writer import open_db
    from pipeline.ingest.locks import global_ingest_lock

    config = load_config()
    with global_ingest_lock(config.paths.assets_dir):
        prepare(config, open_db(config), cohort.load(config, args.cohort), args.checkpoint,
                args.device, output=args.output)


if __name__ == "__main__":
    main()
