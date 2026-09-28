"""Exact versus unquantized IVF_FLAT, isolated from production table policies."""
from contextlib import nullcontext
import math
from pathlib import Path
import shutil
from time import perf_counter
from uuid import uuid4

import lancedb
from lancedb.expr import col, lit
import numpy as np

from pipeline.index.search_storage import reserve_search_storage, storage_status
from pipeline.index.snapshot import capture_snapshot
from pipeline.eval.search_foundation import validate_ann


def benchmark_frames(config, db, *, ann=False, count=12, repeats=5):
    if count < 1 or repeats < 2:
        raise ValueError("Benchmark needs references and repeated warm measurements")
    snapshot = capture_snapshot(config, db)
    source = snapshot.open_table("frames")
    films = snapshot.open_table("films").search().select(["film_id"]).limit(None).to_list()
    references = []
    for film in sorted(films, key=lambda row: row["film_id"]):
        references.extend(source.search().where(col("film_id") == lit(film["film_id"]))
                          .select(["frame_id", "film_id", "visual_vec"]).limit(1).to_list())
        if len(references) >= count:
            break
    if not references:
        raise ValueError("No published reference frames")
    dimension = source.schema.field("visual_vec").type.list_size
    row_count = source.count_rows()
    scratch_root = (Path(config.paths.assets_dir) / "search-builds").resolve()
    scratch = scratch_root / uuid4().hex
    estimate = row_count * (dimension * 4 + 1024) * 3 + 32 * 1024**2
    result = {"contract": "frame-retrieval-benchmark-v1", "versions": snapshot.versions,
              "frame_count": row_count, "ann": ann, "cases": [], "promoted": False}
    with reserve_search_storage(config, estimate) if ann else nullcontext():
        temporary = None
        try:
            table = source
            if ann:
                scratch.mkdir(parents=True, exist_ok=False)
                temporary = lancedb.connect(str(scratch))
                batches = source.search().select(["frame_id", "film_id", "unit_id", "visual_vec"]).limit(None).to_batches(batch_size=4096)
                table = temporary.create_table("candidates", data=batches)
                table.create_scalar_index("film_id", index_type="BTREE")
                partitions = max(1, min(int(math.sqrt(row_count)), row_count // 256))
                table.create_index(metric="cosine", vector_column_name="visual_vec", index_type="IVF_FLAT",
                                   num_partitions=partitions, name="evaluation_ivf_flat", replace=False)
                result["partitions"] = partitions
            for reference in references:
                for scope in (None, reference["film_id"]):
                    for probes in ([16, 32, 64] if ann else [None]):
                        def run(exact):
                            query = table.search(reference["visual_vec"], vector_column_name="visual_vec").metric("cosine")
                            if scope is not None:
                                query = query.where(col("film_id") == lit(scope))
                            query = query.bypass_vector_index() if exact else query.nprobes(probes)
                            return query.select(["frame_id", "unit_id", "_distance"]).limit(600).to_list()
                        run(True)
                        if ann:
                            run(False)
                        timings = {True: [], False: []}
                        hits = {}
                        for _ in range(repeats):
                            for exact in ([True, False] if ann else [True]):
                                started = perf_counter()
                                hits[exact] = run(exact)
                                timings[exact].append((perf_counter() - started) * 1000)
                        case = {"reference_id": reference["frame_id"], "film_id": scope, "nprobes": probes,
                                "exact_units": sorted({row["unit_id"] for row in hits[True]}),
                                "known_positive_units": [],
                                "exact_p95_ms": float(np.percentile(timings[True], 95))}
                        if ann:
                            case.update(ann_units=sorted({row["unit_id"] for row in hits[False]}),
                                        ann_p95_ms=float(np.percentile(timings[False], 95)))
                        result["cases"].append(case)
            if ann:
                # Report per probe budget; this is retrieval evidence, not a
                # production activation receipt or human-positive judgment.
                result["passing_probe_budgets"] = []
                for probes in (16, 32, 64):
                    try:
                        validate_ann([case for case in result["cases"] if case["nprobes"] == probes])
                        result["passing_probe_budgets"].append(probes)
                    except ValueError:
                        pass
        finally:
            if temporary is not None:
                temporary.drop_table("candidates", ignore_missing=True)
                # This UUID directory was exclusively created above. Verify
                # its absolute ownership before removing evaluation scratch.
                if scratch.resolve().parent != scratch_root or scratch.is_symlink():
                    raise ValueError("Benchmark scratch path changed; inspect before removal")
                shutil.rmtree(scratch)
    result["optional_storage"] = storage_status(config)
    return result
