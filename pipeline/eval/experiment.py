"""Run reproducible retrieval experiments without inventing relevance labels.

An experiment snapshot serves three purposes:

1. record the exact code/config/query/index context of a retrieval run;
2. compare channel-weight variants with operational diagnostics immediately;
3. pool every returned candidate for later human 0--3 judgments.

Before the pooled candidates are fully judged, the snapshot deliberately has
``relevance: null``.  Latency, result overlap, and candidate contribution are
diagnostics only; none is treated as a proxy relevance label.

Examples::

    python -m pipeline.eval.experiment run \
      --queries pipeline/eval/fallen_angels_queries.yaml \
      --variants pipeline/eval/variants.yaml \
      --output pipeline/eval/runs/fa-001.yaml

    python -m pipeline.eval.experiment score pipeline/eval/runs/fa-001.yaml
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import platform
import re
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import fmean
from time import perf_counter
from typing import Any
from uuid import uuid4

import yaml
from dotenv import load_dotenv

from pipeline.config import Config, RetrievalWeights, load_config
from pipeline.eval.review import (
    RELEVANT_GRADE,
    VALID_GRADES,
    format_timecode,
    load_query_set,
)
from pipeline.index.writer import (
    ensure_search_indexes,
    open_db,
    published_film_ids,
    table_names,
)
from pipeline.search.retrieve import search


SCHEMA_VERSION = 1
DEFAULT_QUERIES = Path(__file__).parent / "fallen_angels_queries.yaml"
DEFAULT_VARIANTS = Path(__file__).parent / "variants.yaml"
_REPO_ROOT = Path(__file__).resolve().parents[2]
_VARIANT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SNAPSHOT_HASH_FIELD = "machine_payload_sha256"
_HUMAN_JUDGMENT_FIELDS = frozenset({"grade", "flags", "note"})

SearchFunction = Callable[..., list[dict[str, Any]]]
Clock = Callable[[], float]
IndexStateFunction = Callable[[Any], dict[str, Any]]


@dataclass(frozen=True)
class Variant:
    """One named RRF weight configuration."""

    id: str
    weights: RetrievalWeights
    description: str = ""

    def as_document(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "weights": asdict(self.weights),
        }


def _read_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256_bytes(payload)


def _machine_owned_payload(value: Any) -> Any:
    """Remove only human-editable judgment fields from a snapshot payload."""
    if isinstance(value, Mapping):
        is_candidate = (
            "unit_id" in value
            and "evidence_sha256" in value
            and _HUMAN_JUDGMENT_FIELDS.issubset(value)
        )
        return {
            str(key): _machine_owned_payload(item)
            for key, item in value.items()
            if key != _SNAPSHOT_HASH_FIELD
            and not (is_candidate and key in _HUMAN_JUDGMENT_FIELDS)
        }
    if isinstance(value, (list, tuple)):
        return [_machine_owned_payload(item) for item in value]
    return _plain(value)


def snapshot_payload_sha256(document: Mapping[str, Any]) -> str:
    """Fingerprint all machine-owned snapshot data, excluding judgments."""
    return _canonical_sha256(_machine_owned_payload(document))


def load_variants(path: Path) -> list[Variant]:
    """Load and validate a versioned channel-variant document."""
    document = _read_yaml(path) or {}
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("variant file must be a version-1 mapping")
    raw_variants = document.get("variants")
    if not isinstance(raw_variants, list) or not raw_variants:
        raise ValueError("variant file contains no variants")

    variants: list[Variant] = []
    seen: set[str] = set()
    for position, raw in enumerate(raw_variants, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"variant #{position} must be a mapping")
        variant_id = str(raw.get("id") or "").strip()
        if not _VARIANT_ID_RE.fullmatch(variant_id):
            raise ValueError(
                f"variant #{position} id must match {_VARIANT_ID_RE.pattern!r}"
            )
        if variant_id in seen:
            raise ValueError(f"duplicate variant id: {variant_id}")
        seen.add(variant_id)

        weights_raw = raw.get("weights")
        if not isinstance(weights_raw, dict):
            raise ValueError(f"variant {variant_id!r} must define weights")
        if set(weights_raw) != {"img", "txt", "lex"}:
            raise ValueError(
                f"variant {variant_id!r} weights must contain img, txt, and lex"
            )
        try:
            weight_values = {
                name: float(weights_raw[name]) for name in ("img", "txt", "lex")
            }
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"variant {variant_id!r} weights must be numeric"
            ) from exc
        if any(not math.isfinite(value) or value < 0.0 for value in weight_values.values()):
            raise ValueError(
                f"variant {variant_id!r} weights must be finite and non-negative"
            )
        if not any(value > 0.0 for value in weight_values.values()):
            raise ValueError(f"variant {variant_id!r} cannot disable every channel")

        variants.append(
            Variant(
                id=variant_id,
                description=str(raw.get("description") or "").strip(),
                weights=RetrievalWeights(**weight_values),
            )
        )
    return variants


def capture_index_state(db: Any) -> dict[str, Any]:
    """Capture enough local index identity to detect a moving benchmark corpus."""
    tables: dict[str, dict[str, Any]] = {}
    for name in sorted(table_names(db)):
        table = db.open_table(name)
        schema_text = str(table.schema)
        indices: list[dict[str, Any]] = []
        for index in sorted(table.list_indices(), key=lambda item: item.name):
            stats = table.index_stats(index.name)
            indices.append(
                {
                    "name": str(index.name),
                    "type": str(index.index_type),
                    "columns": [str(column) for column in index.columns],
                    "indexed_rows": (
                        int(stats.num_indexed_rows) if stats is not None else None
                    ),
                    "unindexed_rows": (
                        int(stats.num_unindexed_rows) if stats is not None else None
                    ),
                }
            )
        tables[name] = {
            "version": int(table.version),
            "rows": int(table.count_rows()),
            "schema_sha256": _sha256_bytes(schema_text.encode("utf-8")),
            "indices": indices,
        }
    return {
        "tables": tables,
        "published_film_ids": sorted(published_film_ids(db)),
    }


def _git_output(arguments: Sequence[str], repo_root: Path) -> bytes | None:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repo_root,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout


def _display_path(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def build_provenance(
    queries_path: Path,
    variants_path: Path,
    config: Config,
    *,
    repo_root: Path = _REPO_ROOT,
) -> dict[str, Any]:
    """Describe source/config inputs without serializing private local media."""
    commit_output = _git_output(["rev-parse", "HEAD"], repo_root)
    status = _git_output(
        ["status", "--porcelain=v1", "--untracked-files=all"], repo_root
    )
    diff = _git_output(["diff", "--binary", "HEAD"], repo_root)
    git_available = commit_output is not None and status is not None and diff is not None
    commit = (
        commit_output.decode().strip() or None
        if commit_output is not None
        else None
    )
    dirty = bool(status.strip()) if git_available and status is not None else None
    lock_path = repo_root / "uv.lock"
    return {
        "git": {
            "commit": commit,
            "available": git_available,
            "dirty": dirty,
            "reproducible": bool(git_available and not dirty),
            # This identifies the tracked diff and dirty path set. A dirty run
            # is diagnostic; a clean commit remains the reproducible target.
            "working_tree_sha256": (
                _sha256_bytes(status + b"\0" + diff)
                if git_available and status is not None and diff is not None
                else None
            ),
        },
        "queries": {
            "path": _display_path(queries_path, repo_root),
            "sha256": _sha256_file(queries_path),
        },
        "variants": {
            "path": _display_path(variants_path, repo_root),
            "sha256": _sha256_file(variants_path),
        },
        "models": {
            "visual_encoder": config.models.visual_encoder,
            # Current dense text retrieval shares PE's aligned text tower.
            # Keep the configured future encoder visible without implying it
            # produced the vectors measured by this snapshot.
            "semantic_text_encoder": config.models.visual_encoder,
            "configured_text_encoder": config.models.text_encoder,
            "stored_unit_vector_manifest": None,
            "lineage_note": (
                "Legacy units do not yet persist encoder revision/preprocessing "
                "metadata; the corpus is assumed to match visual_encoder. Add an "
                "embedding manifest before comparing encoder migrations."
            ),
        },
        "retrieval_config": asdict(config.retrieval),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "uv_lock_sha256": _sha256_file(lock_path) if lock_path.is_file() else None,
        },
    }


def _film_scope(
    query: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> tuple[str, ...] | None:
    raw_scope: Any
    if "film_ids" in query:
        raw_scope = query.get("film_ids")
    else:
        scope = metadata.get("scope") or {}
        if not isinstance(scope, dict):
            raise ValueError("query metadata scope must be a mapping")
        raw_scope = scope.get("film_ids")
        if raw_scope is None:
            if scope.get("all_films") is True:
                return None
            raise ValueError(
                "experiment scope must explicitly set film_ids or all_films: true"
            )
    if not isinstance(raw_scope, list) or not all(
        isinstance(film_id, str) and film_id.strip() for film_id in raw_scope
    ):
        raise ValueError("film_ids scope must be a list of non-empty strings")
    if not raw_scope:
        raise ValueError("film_ids scope cannot be empty; use all_films: true")
    return tuple(dict.fromkeys(film_id.strip() for film_id in raw_scope))


def _variant_config(config: Config, variant: Variant) -> Config:
    resolved = copy.deepcopy(config)
    resolved.retrieval.weights = copy.deepcopy(variant.weights)
    return resolved


def _plain(value: Any) -> Any:
    """Convert common scalar containers into stable YAML-safe values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    item_method = getattr(value, "item", None)
    if callable(item_method):
        try:
            return _plain(item_method())
        except (TypeError, ValueError):
            pass
    return str(value)


def _evidence_sha256(candidate: Mapping[str, Any]) -> str:
    return _canonical_sha256(
        {
            "unit_id": str(candidate.get("unit_id") or ""),
            "film_id": str(candidate.get("film_id") or ""),
            "t_start": float(candidate.get("t_start", 0.0)),
            "t_end": float(candidate.get("t_end", 0.0)),
        }
    )


def _pooled_candidate(
    result: Mapping[str, Any],
    *,
    variant_id: str,
    rank: int,
) -> dict[str, Any]:
    required = ("unit_id", "film_id", "t_start", "t_end")
    missing = [field for field in required if result.get(field) is None]
    if missing:
        raise ValueError(
            "search result is missing pooled evidence: " + ", ".join(missing)
        )
    t_start = float(result["t_start"])
    t_end = float(result["t_end"])
    if not math.isfinite(t_start) or not math.isfinite(t_end) or t_end <= t_start:
        raise ValueError(f"search result {result['unit_id']!r} has invalid timestamps")
    presentation = _presentation(result)
    candidate = {
        "unit_id": str(result["unit_id"]),
        "film_id": str(result["film_id"]),
        "t_start": t_start,
        "t_end": t_end,
        "timecode": f"{format_timecode(t_start)}–{format_timecode(t_end)}",
        "caption": str(result.get("caption") or ""),
        "keyframe_url": result.get("keyframe_url"),
        "preview_url": result.get("preview_url"),
        "matched_frame_index": result.get("matched_frame_index"),
        "matched_frame_timestamp": result.get("matched_frame_timestamp"),
        "presentations": {variant_id: presentation},
        "first_seen": {"variant": variant_id, "rank": rank},
        "grade": None,
        "flags": [],
        "note": "",
    }
    candidate["evidence_sha256"] = _evidence_sha256(candidate)
    return candidate


def _presentation(result: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the exact thumbnail/evidence selected by one retrieval variant."""
    return {
        "keyframe_url": result.get("keyframe_url"),
        "matched_frame_url": result.get("matched_frame_url"),
        "matched_frame_index": result.get("matched_frame_index"),
        "matched_frame_timestamp": result.get("matched_frame_timestamp"),
    }


def _ranking_entry(result: Mapping[str, Any], rank: int) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "rank": rank,
        "unit_id": str(result["unit_id"]),
        "presentation": _presentation(result),
    }
    if isinstance(result.get("debug"), Mapping):
        entry["debug"] = _plain(result["debug"])
    return entry


def validate_snapshot_integrity(document: Mapping[str, Any]) -> None:
    """Reject accidental edits to machine-owned benchmark evidence."""
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported experiment schema: {document.get('schema_version')!r}")
    if document.get("kind") != "scene_recall_retrieval_experiment":
        raise ValueError("not a retrieval experiment snapshot")
    expected_hash = document.get(_SNAPSHOT_HASH_FIELD)
    if not isinstance(expected_hash, str) or not expected_hash:
        raise ValueError("experiment snapshot is missing its machine payload hash")
    if snapshot_payload_sha256(document) != expected_hash:
        raise ValueError(
            "experiment machine payload changed after generation; restore the "
            "snapshot and edit only grade, flags, or note fields"
        )

    variant_ids = [
        str(variant.get("id") or "") for variant in (document.get("variants") or [])
    ]
    if not variant_ids or len(set(variant_ids)) != len(variant_ids) or "" in variant_ids:
        raise ValueError("experiment variant IDs must be non-empty and unique")
    query_ids: set[str] = set()
    for query in document.get("queries") or []:
        query_id = str(query.get("id") or "")
        if not query_id or query_id in query_ids:
            raise ValueError("experiment query IDs must be non-empty and unique")
        query_ids.add(query_id)
        candidates = query.get("candidates") or []
        candidate_ids = [str(candidate.get("unit_id") or "") for candidate in candidates]
        if "" in candidate_ids or len(set(candidate_ids)) != len(candidate_ids):
            raise ValueError(f"query {query_id!r} candidate IDs must be unique")
        for candidate in candidates:
            if candidate.get("evidence_sha256") != _evidence_sha256(candidate):
                raise ValueError(
                    f"query {query_id!r} candidate evidence fingerprint changed"
                )
        runs = query.get("runs") or {}
        if set(runs) != set(variant_ids):
            raise ValueError(f"query {query_id!r} has an incomplete variant set")
        for variant_id in variant_ids:
            ranking = runs[variant_id].get("ranking") or []
            ranked_ids = [str(entry.get("unit_id") or "") for entry in ranking]
            expected_ranks = list(range(1, len(ranking) + 1))
            if [entry.get("rank") for entry in ranking] != expected_ranks:
                raise ValueError(
                    f"query {query_id!r} variant {variant_id!r} ranks are invalid"
                )
            if len(set(ranked_ids)) != len(ranked_ids) or any(
                unit_id not in candidate_ids for unit_id in ranked_ids
            ):
                raise ValueError(
                    f"query {query_id!r} variant {variant_id!r} ranking is invalid"
                )


def _validated_grade(candidate: Mapping[str, Any]) -> int | None:
    grade = candidate.get("grade")
    if grade is None or grade == "":
        return None
    if isinstance(grade, bool) or not isinstance(grade, int) or grade not in VALID_GRADES:
        raise ValueError(
            f"grade for {candidate.get('unit_id', '?')} must be an integer 0–3"
        )
    return grade


def preserve_judgments(
    document: dict[str, Any],
    previous: Mapping[str, Any],
) -> int:
    """Copy only matching human-owned fields from an older review/snapshot."""
    previous_queries = {
        str(query.get("id")): query
        for query in (previous.get("queries") or [])
        if isinstance(query, dict) and query.get("id") is not None
    }
    inherited = 0
    for query in document.get("queries", []):
        prior_query = previous_queries.get(str(query.get("id")))
        if not isinstance(prior_query, dict):
            continue
        if str(prior_query.get("query") or "") != str(query.get("query") or ""):
            continue
        prior_candidates = {
            str(candidate.get("unit_id")): candidate
            for candidate in (prior_query.get("candidates") or [])
            if isinstance(candidate, dict) and candidate.get("unit_id") is not None
        }
        for candidate in query.get("candidates", []):
            prior = prior_candidates.get(str(candidate.get("unit_id")))
            if not isinstance(prior, dict):
                continue
            if _evidence_sha256(prior) != candidate.get("evidence_sha256"):
                continue
            grade = _validated_grade(prior)
            flags = prior.get("flags") or []
            note = prior.get("note") or ""
            if not isinstance(flags, list) or not all(
                isinstance(flag, str) for flag in flags
            ):
                raise ValueError(
                    f"flags for {candidate['unit_id']} must be a list of strings"
                )
            if not isinstance(note, str):
                raise ValueError(f"note for {candidate['unit_id']} must be text")
            if grade is not None or flags or note:
                candidate["grade"] = grade
                candidate["flags"] = list(flags)
                candidate["note"] = note
                inherited += 1
    return inherited


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def operational_diagnostics(
    query_rows: Sequence[Mapping[str, Any]],
    variants: Sequence[Variant],
    *,
    limit: int,
    warmup_ms: Mapping[str, float] | None,
) -> dict[str, Any]:
    """Summarize mechanics only; no value here is a relevance judgment."""
    baseline_id = variants[0].id
    variant_rows: dict[str, dict[str, Any]] = {}
    all_pairs: dict[str, set[tuple[str, str]]] = {variant.id: set() for variant in variants}

    for variant in variants:
        latencies: list[float] = []
        counts: list[int] = []
        retentions: list[float] = []
        jaccards: list[float] = []
        new_vs_baseline: set[tuple[str, str]] = set()
        for query in query_rows:
            query_id = str(query["id"])
            run = query["runs"][variant.id]
            baseline = query["runs"][baseline_id]
            ids = [str(row["unit_id"]) for row in run["ranking"][:limit]]
            baseline_ids = [
                str(row["unit_id"]) for row in baseline["ranking"][:limit]
            ]
            id_set = set(ids)
            baseline_set = set(baseline_ids)
            intersection = id_set & baseline_set
            union = id_set | baseline_set
            latencies.append(float(run["latency_ms"]))
            counts.append(len(ids))
            all_pairs[variant.id].update((query_id, unit_id) for unit_id in id_set)
            new_vs_baseline.update(
                (query_id, unit_id) for unit_id in id_set - baseline_set
            )
            retentions.append(
                len(intersection) / len(baseline_set) if baseline_set else 1.0
            )
            jaccards.append(len(intersection) / len(union) if union else 1.0)

        variant_rows[variant.id] = {
            "latency_ms": {
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
                "max": max(latencies) if latencies else None,
            },
            "mean_result_count": fmean(counts) if counts else 0.0,
            "underfilled_queries": sum(count < limit for count in counts),
            "baseline_retention_at_limit": fmean(retentions) if retentions else None,
            "jaccard_at_limit": fmean(jaccards) if jaccards else None,
            "new_candidates_vs_baseline": len(new_vs_baseline),
        }

    for variant in variants:
        other_pairs: set[tuple[str, str]] = set()
        for other in variants:
            if other.id != variant.id:
                other_pairs.update(all_pairs[other.id])
        variant_rows[variant.id]["exclusive_candidates"] = len(
            all_pairs[variant.id] - other_pairs
        )

    return {
        "note": (
            "Operational diagnostics only. Overlap, candidate contribution, and "
            "latency are not relevance measurements."
        ),
        "baseline_variant": baseline_id,
        "query_count": len(query_rows),
        "limit": limit,
        "warmup_ms": warmup_ms,
        "variants": variant_rows,
    }


def run_experiment(
    queries: Sequence[dict[str, Any]],
    metadata: Mapping[str, Any],
    variants: Sequence[Variant],
    db: Any,
    config: Config,
    *,
    limit: int = 12,
    search_fn: SearchFunction = search,
    clock: Clock = perf_counter,
    index_state_fn: IndexStateFunction = capture_index_state,
    provenance: Mapping[str, Any] | None = None,
    previous_judgments: Mapping[str, Any] | None = None,
    warmup: bool = True,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Run every query/variant and return one pooled, unlabelled snapshot."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    if limit > config.retrieval.max_result_limit:
        raise ValueError(
            "limit cannot exceed configured retrieval.max_result_limit "
            f"{config.retrieval.max_result_limit}"
        )
    if not variants:
        raise ValueError("at least one variant is required")

    index_before = index_state_fn(db)
    published = set(index_before.get("published_film_ids") or [])
    if not published:
        raise ValueError("experiment corpus contains no published films")
    for query in queries:
        scope = _film_scope(query, metadata)
        missing = sorted(set(scope or ()) - published)
        if missing:
            raise ValueError(
                "experiment scope references unpublished film IDs: "
                + ", ".join(missing)
            )
    variant_configs = {
        variant.id: _variant_config(config, variant) for variant in variants
    }

    warmup_ms: dict[str, float] | None = None
    if warmup and queries:
        first_query = queries[0]
        scope = _film_scope(first_query, metadata)
        warmup_ms = {}
        for variant in variants:
            started = clock()
            search_fn(
                str(first_query["query"]),
                db,
                variant_configs[variant.id],
                film_ids=scope,
                result_limit=limit,
            )
            warmup_ms[variant.id] = max(
                0.0,
                (clock() - started) * 1000.0,
            )

    query_rows: list[dict[str, Any]] = []
    for query_index, query in enumerate(queries):
        query_id = str(query["id"])
        query_text = str(query["query"])
        category = str(query["category"])
        scope = _film_scope(query, metadata)
        runs: dict[str, Any] = {}
        candidates_by_id: dict[str, dict[str, Any]] = {}
        candidate_order: list[str] = []

        # Rotate execution order by query so one variant never consistently
        # benefits from being first against warm OS/database caches.
        offset = query_index % len(variants)
        execution_order = [*variants[offset:], *variants[:offset]]
        for variant in execution_order:
            started = clock()
            raw_results = search_fn(
                query_text,
                db,
                variant_configs[variant.id],
                film_ids=scope,
                result_limit=limit,
            )
            latency_ms = max(0.0, (clock() - started) * 1000.0)
            ranking: list[dict[str, Any]] = []
            seen_units: set[str] = set()
            for result in raw_results:
                unit_id = str(result.get("unit_id") or "")
                if not unit_id or unit_id in seen_units:
                    continue
                seen_units.add(unit_id)
                rank = len(ranking) + 1
                ranking.append(_ranking_entry(result, rank))
                candidate = _pooled_candidate(
                    result,
                    variant_id=variant.id,
                    rank=rank,
                )
                prior = candidates_by_id.get(unit_id)
                if prior is None:
                    candidates_by_id[unit_id] = candidate
                    candidate_order.append(unit_id)
                elif prior["evidence_sha256"] != candidate["evidence_sha256"]:
                    raise ValueError(
                        f"unit {unit_id!r} changed evidence between variants"
                    )
                else:
                    prior["presentations"][variant.id] = _presentation(result)
            runs[variant.id] = {
                "latency_ms": latency_ms,
                "ranking": ranking,
            }

        query_rows.append(
            {
                "id": query_id,
                "category": category,
                "query": query_text,
                "film_ids": list(scope) if scope is not None else None,
                "execution_order": [variant.id for variant in execution_order],
                "runs": {variant.id: runs[variant.id] for variant in variants},
                "candidates": [
                    candidates_by_id[unit_id] for unit_id in candidate_order
                ],
            }
        )

    index_after = index_state_fn(db)
    if index_after != index_before:
        raise RuntimeError(
            "index changed while the experiment was running; discard the run "
            "and retry after ingestion finishes"
        )

    resolved_provenance = dict(provenance or {})
    resolved_provenance["index"] = index_before
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "scene_recall_retrieval_experiment",
        "created_at": created_at
        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provenance": resolved_provenance,
        "query_metadata": _plain(dict(metadata)),
        "variants": [variant.as_document() for variant in variants],
        "judging": {
            "ownership": "human",
            "grade_scale": {
                0: "irrelevant",
                1: "weak or partial match",
                2: "relevant",
                3: "ideal match",
            },
            "instructions": [
                "Judge the visible source moment, not caption or retrieval score.",
                "Do not infer blank grades from rank, overlap, or model output.",
                "Fill the complete pooled candidate list for a query before it scores.",
            ],
        },
        "diagnostics": operational_diagnostics(
            query_rows,
            variants,
            limit=limit,
            warmup_ms=warmup_ms,
        ),
        "relevance": None,
        "queries": query_rows,
    }
    inherited = (
        preserve_judgments(document, previous_judgments)
        if previous_judgments is not None
        else 0
    )
    document["judgments_inherited"] = inherited
    document[_SNAPSHOT_HASH_FIELD] = snapshot_payload_sha256(document)
    return document


def _dcg(grades: Sequence[int], k: int) -> float:
    return sum(
        (2**grade - 1) / math.log2(rank + 1)
        for rank, grade in enumerate(grades[:k], start=1)
    )


def _ranking_metrics(
    ranking: Sequence[str],
    candidates: Mapping[str, Mapping[str, Any]],
    ideal_grades: Sequence[int],
    *,
    k: int,
) -> dict[str, float]:
    ranked_ids = list(ranking)
    top_ids = ranked_ids[:k]
    grades = [int(_validated_grade(candidates[unit_id])) for unit_id in top_ids]
    relevant = [grade >= RELEVANT_GRADE for grade in grades]
    first_five_relevant = [
        int(_validated_grade(candidates[unit_id])) >= RELEVANT_GRADE
        for unit_id in ranked_ids[:5]
    ]
    first_relevant = next(
        (rank for rank, value in enumerate(relevant, start=1) if value),
        None,
    )
    ideal_dcg = _dcg(sorted(ideal_grades, reverse=True), k)

    def flag_rate(flag: str) -> float:
        if not top_ids:
            return 0.0
        return sum(
            flag in (candidates[unit_id].get("flags") or [])
            for unit_id in top_ids
        ) / len(top_ids)

    return {
        f"precision@{k}": sum(relevant) / k,
        "success@1": float(any(relevant[:1])),
        "success@5": float(any(first_five_relevant)),
        f"ndcg@{k}": _dcg(grades, k) / ideal_dcg if ideal_dcg else 0.0,
        f"mrr@{k}": 1.0 / first_relevant if first_relevant else 0.0,
        f"junk@{k}": flag_rate("junk"),
    }


def _aggregate_metrics(
    rows: Sequence[Mapping[str, float]],
    metric_names: Sequence[str],
) -> dict[str, float]:
    return {name: fmean(float(row[name]) for row in rows) for name in metric_names}


def score_experiment(document: Mapping[str, Any], *, k: int = 10) -> dict[str, Any]:
    """Score only fully human-judged pooled queries from one snapshot."""
    if k <= 0:
        raise ValueError("k must be positive")
    validate_snapshot_integrity(document)
    variant_docs = document.get("variants") or []
    variant_ids = [str(variant["id"]) for variant in variant_docs]
    if not variant_ids:
        raise ValueError("experiment has no variants")

    queries = document.get("queries") or []
    total_candidates = 0
    judged_candidates = 0
    incomplete_ids: list[str] = []
    complete_queries: list[dict[str, Any]] = []
    for query in queries:
        candidates = query.get("candidates") or []
        total_candidates += len(candidates)
        grades = [_validated_grade(candidate) for candidate in candidates]
        judged_candidates += sum(grade is not None for grade in grades)
        if not candidates or any(grade is None for grade in grades):
            incomplete_ids.append(str(query.get("id", "?")))
            continue
        complete_queries.append(dict(query))

    coverage = (
        judged_candidates / total_candidates if total_candidates else None
    )
    judgment_summary = {
        "candidates_total": total_candidates,
        "candidates_judged": judged_candidates,
        "candidate_coverage": coverage,
        "queries_total": len(queries),
        "queries_evaluated": len(complete_queries),
        "queries_incomplete": len(incomplete_ids),
        "incomplete_ids": incomplete_ids,
    }
    if not complete_queries:
        return {
            "diagnostics": document.get("diagnostics"),
            "judgments": judgment_summary,
            "relevance": None,
            "metric_note": (
                "Relevance unavailable: no query has a fully human-judged "
                "pooled candidate set."
            ),
        }

    metric_names = (
        f"precision@{k}",
        "success@1",
        "success@5",
        f"ndcg@{k}",
        f"mrr@{k}",
        f"junk@{k}",
    )
    rows_by_variant: dict[str, list[dict[str, Any]]] = {
        variant_id: [] for variant_id in variant_ids
    }
    for query in complete_queries:
        candidates = {
            str(candidate["unit_id"]): candidate
            for candidate in query["candidates"]
        }
        ideal_grades = [int(_validated_grade(candidate)) for candidate in candidates.values()]
        runs = query.get("runs") or {}
        for variant_id in variant_ids:
            if variant_id not in runs:
                raise ValueError(
                    f"query {query['id']!r} is missing variant {variant_id!r}"
                )
            ranking = [
                str(entry["unit_id"])
                for entry in (runs[variant_id].get("ranking") or [])
            ]
            if len(set(ranking)) != len(ranking):
                raise ValueError(
                    f"query {query['id']!r} variant {variant_id!r} has duplicates"
                )
            missing = [unit_id for unit_id in ranking if unit_id not in candidates]
            if missing:
                raise ValueError(
                    f"query {query['id']!r} ranking references unknown candidate"
                )
            rows_by_variant[variant_id].append(
                {
                    "id": str(query["id"]),
                    "category": str(query["category"]),
                    **_ranking_metrics(
                        ranking,
                        candidates,
                        ideal_grades,
                        k=k,
                    ),
                }
            )

    variant_report: dict[str, Any] = {}
    for variant_id, rows in rows_by_variant.items():
        category_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            category_rows[str(row["category"])].append(row)
        variant_report[variant_id] = {
            "overall": _aggregate_metrics(rows, metric_names),
            "by_category": {
                category: {
                    "evaluated": len(group),
                    **_aggregate_metrics(group, metric_names),
                }
                for category, group in sorted(category_rows.items())
            },
            "queries": rows,
        }

    baseline_id = variant_ids[0]
    baseline_rows = {
        str(row["id"]): row for row in rows_by_variant[baseline_id]
    }
    comparisons: dict[str, Any] = {}
    metric = f"ndcg@{k}"
    for variant_id in variant_ids[1:]:
        deltas = [
            float(row[metric]) - float(baseline_rows[str(row["id"])][metric])
            for row in rows_by_variant[variant_id]
        ]
        comparisons[variant_id] = {
            "baseline": baseline_id,
            "metric": metric,
            "mean_delta": fmean(deltas),
            "wins": sum(delta > 1e-12 for delta in deltas),
            "ties": sum(abs(delta) <= 1e-12 for delta in deltas),
            "losses": sum(delta < -1e-12 for delta in deltas),
        }

    return {
        "diagnostics": document.get("diagnostics"),
        "judgments": judgment_summary,
        "relevance": {
            "note": (
                "Human-judged quality within the pooled candidate union; corpus "
                "recall is not measured."
            ),
            "k": k,
            "baseline_variant": baseline_id,
            "variants": variant_report,
            "paired_vs_baseline": comparisons,
        },
        "metric_note": (
            "Only queries with a fully human-judged pooled candidate set are scored."
        ),
    }


def write_snapshot(
    path: Path,
    document: Mapping[str, Any],
    *,
    overwrite: bool = False,
) -> None:
    """Atomically write a snapshot, protecting existing human judgments."""
    if path.exists() and not overwrite:
        raise FileExistsError(f"snapshot already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            yaml.safe_dump(
                dict(document),
                handle,
                allow_unicode=True,
                sort_keys=False,
                width=100,
            )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _run_command(args: argparse.Namespace) -> None:
    if args.output.exists():
        raise FileExistsError(f"snapshot already exists: {args.output}")
    config = load_config()
    queries, metadata = load_query_set(args.queries)
    variants = load_variants(args.variants)
    previous = _read_yaml(args.judgments_from) if args.judgments_from else None
    if previous is not None:
        validate_snapshot_integrity(previous)
    provenance = build_provenance(args.queries, args.variants, config)
    provenance["judgments_from"] = (
        {
            "path": _display_path(args.judgments_from, _REPO_ROOT),
            "sha256": _sha256_file(args.judgments_from),
        }
        if args.judgments_from
        else None
    )
    git_state = provenance["git"]
    if git_state.get("dirty") is not False and not args.allow_dirty:
        raise ValueError(
            "experiment snapshots require a clean Git worktree; commit changes "
            "first or pass --allow-dirty for a diagnostic-only run"
        )
    db = open_db(config)
    ensure_search_indexes(db)
    document = run_experiment(
        queries,
        metadata,
        variants,
        db,
        config,
        limit=args.limit,
        provenance=provenance,
        previous_judgments=previous,
        warmup=not args.no_warmup,
    )
    write_snapshot(args.output, document)
    candidate_count = sum(len(query["candidates"]) for query in document["queries"])
    print(
        f"Wrote {len(queries)} queries, {len(variants)} variants, and "
        f"{candidate_count} pooled candidates to {args.output}"
    )
    print("Relevance remains unavailable until pooled candidate grades are filled.")


def _score_command(args: argparse.Namespace) -> None:
    document = _read_yaml(args.snapshot) or {}
    report = score_experiment(document, k=args.k)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        if args.output.exists():
            raise FileExistsError(f"score output already exists: {args.output}")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Wrote score report to {args.output}")


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Run reproducible retrieval variants and score human judgments."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run variants and pool blank judgments")
    run.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    run.add_argument("--variants", type=Path, default=DEFAULT_VARIANTS)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--judgments-from", type=Path, default=None)
    run.add_argument("--limit", type=int, default=12)
    run.add_argument("--no-warmup", action="store_true")
    run.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow a diagnostic snapshot from an unreproducible dirty worktree",
    )

    score_parser = commands.add_parser("score", help="score completed human grades")
    score_parser.add_argument("snapshot", type=Path)
    score_parser.add_argument("--output", type=Path, default=None)
    score_parser.add_argument("--k", type=int, default=10)

    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            _run_command(args)
        else:
            _score_command(args)
    except (
        FileExistsError,
        FileNotFoundError,
        RuntimeError,
        ValueError,
        yaml.YAMLError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
