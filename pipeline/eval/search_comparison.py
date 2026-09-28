"""Mechanical twelve-reference replay, without inventing human quality labels."""
from dataclasses import replace
from time import perf_counter

import numpy as np
from lancedb.expr import col, lit

from pipeline.index.composition import ready_profile
from pipeline.index.framing_cache import read_input
from pipeline.index.search_storage import storage_status
from pipeline.index.snapshot import capture_snapshot
from pipeline.search.composition import shadow_profile
from pipeline.search.retrieve import search_by_image


def compare_references(config, db, identity, references, *, repeats=3):
    if (not isinstance(references, list) or not all(isinstance(row, dict) for row in references)
            or len(references) != 12 or len({row["frame_id"] for row in references}) != 12
            or repeats < 2):
        raise ValueError("Choose twelve distinct indexed reference frames and at least two warm repeats")
    snapshot = capture_snapshot(config, db)
    if ready_profile(config, snapshot, identity) is None:
        raise ValueError("Complete composition coverage is required before comparison")
    baseline_config = replace(config, retrieval=replace(config.retrieval, composition_profile=None))
    output, durations = [], []
    fields = ("unit_id", "film_id", "t_start", "t_end", "caption", "matched_frame_url", "matched_frame_timestamp", "debug")
    for reference in references:
        rows = snapshot.open_table("frames").search().where(col("frame_id") == lit(reference["frame_id"])).limit(2).to_list()
        if len(rows) != 1:
            raise ValueError("A chosen reference is missing or ambiguous")
        source = read_input(rows[0])
        scope = tuple(reference.get("film_ids", ()))
        def run():
            return search_by_image(source.image(), snapshot, baseline_config, film_ids=scope,
                                   exclude_unit_id=rows[0]["unit_id"], exclude_film_id=rows[0]["film_id"], result_limit=10)
        baseline = run()
        with shadow_profile(identity):
            run()  # warmup
            for _ in range(repeats):
                started = perf_counter()
                challenger = run()
                durations.append(perf_counter() - started)
        output.append({"reference_id": reference["frame_id"], "source_sha256": source.sha256,
                       "reference_path": str(source.source.path), "film_ids": list(scope),
                       "baseline": [{key: row.get(key) for key in fields} for row in baseline[:10]],
                       "challenger": [{key: row.get(key) for key in fields} for row in challenger[:10]],
                       "preference": None, "baseline_ndcg10": None, "challenger_ndcg10": None,
                       "known_positives_retained": None})
    return {"profile_id": identity, "table_versions": snapshot.versions, "cases": output,
            "timing_scope": "warm public Framing pipeline, pinned library; excludes HTTP transport and snapshot capture",
            "warm_p95_seconds": float(np.percentile(durations, 95)), "warm_samples": durations,
            "optional_physical_bytes": storage_status(config)["used_bytes"],
            "complete_coverage": True, "reviewed_by": None}
