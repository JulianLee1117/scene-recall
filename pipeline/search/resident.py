"""Resident exact vector search: whole vector tables in memory, searched in milliseconds.

Lance flat scans cost about a second per channel at library scale, and one
scan cannot rank each semantic view separately. The same exact cosine search
over a resident float16 matrix is one matrix-vector product (GPU when there is
room, CPU otherwise), after which each view or shot takes its own top-k.

Matrices are loaded from the request's pinned snapshot and keyed by table and
version, so a query never mixes table generations or vector spaces: a table
holds exactly one profile (text tables are per model profile; ``frames`` is
single-encoder by ``require_visual_encoder_profile``). Publishing a film
creates a new version, which loads on first use. Any failure returns ``None``
and the caller keeps its Lance path, so this module is purely an accelerator.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import threading
import time
from typing import Any, Iterable

import numpy as np

_LOGGER = logging.getLogger("uvicorn.error")
_LOCK = threading.Lock()
_LOADING: dict[tuple[str, str], threading.Lock] = {}
_MATRICES: dict[tuple[str, str], "VectorMatrix"] = {}
_BATCH_ROWS = 65_536
_GPU_HEADROOM_BYTES = 2 * 1024 ** 3
_DISABLED = False


@dataclass
class VectorMatrix:
    """One pinned table version as a normalized float16 matrix plus row metadata."""

    name: str
    version: int
    device: Any
    vectors: Any                 # torch (N, D) float16, rows L2-normalized
    row_unit: Any                # torch (N,) int64 -> index into unit_ids
    row_group: Any               # torch (N,) int16 -> index into groups
    groups: list[str]
    unit_ids: list[str]
    unit_film: Any               # torch (U,) int64 -> index into films
    films: list[str]
    row_keys: np.ndarray         # (N,) object: feature_id / frame_id
    row_extra: dict[str, np.ndarray]   # extra scalar columns per row (frame_index, timestamp, ...)

    def allowed_rows(self, film_ids: tuple[str, ...]) -> Any | None:
        """Boolean row mask for a film scope (None when unscoped)."""
        if not film_ids:
            return None
        import torch
        wanted = [index for index, film in enumerate(self.films) if film in set(film_ids)]
        if not wanted:
            return torch.zeros(len(self.row_unit), dtype=torch.bool, device=self.device)
        film_ok = torch.zeros(len(self.films), dtype=torch.bool, device=self.device)
        film_ok[wanted] = True
        return film_ok[self.unit_film[self.row_unit]]


def disable() -> None:
    """Turn resident search off for this process (tests, constrained hosts)."""
    global _DISABLED
    _DISABLED = True


def _device_for(bytes_needed: int) -> Any:
    import torch
    if torch.cuda.is_available():
        free, _total = torch.cuda.mem_get_info()
        if free - bytes_needed > _GPU_HEADROOM_BYTES:
            return torch.device("cuda")
    return torch.device("cpu")


def _load(table: Any, name: str, *, vector_column: str, key_column: str, group_column: str | None,
          extra_columns: tuple[str, ...], where: str | None) -> VectorMatrix:
    import torch

    columns = [key_column, "unit_id", "film_id", vector_column, *extra_columns]
    if group_column:
        columns.append(group_column)
    total = int(table.count_rows(where) if where else table.count_rows())
    dimension = int(table.schema.field(vector_column).type.list_size)
    device = _device_for(total * dimension * 2)
    vectors = torch.empty((total, dimension), dtype=torch.float16, device=device)
    units: dict[str, int] = {}
    films: dict[str, int] = {}
    groups: dict[str, int] = {}
    unit_film: list[int] = []
    row_unit = np.empty(total, dtype=np.int64)
    row_group = np.zeros(total, dtype=np.int16)
    row_keys = np.empty(total, dtype=object)
    row_extra: dict[str, list[Any]] = {column: [] for column in extra_columns}
    cursor = 0
    for batch in table.to_batches(columns=columns, filter=where, batch_size=_BATCH_ROWS):
        count = batch.num_rows
        if cursor + count > total:          # the pinned version cannot grow; guard anyway
            raise RuntimeError(f"{name} changed while loading")
        flat = np.array(batch.column(vector_column).values.to_numpy(zero_copy_only=False), dtype=np.float32)  # own a writable copy
        block = torch.from_numpy(flat.reshape(count, dimension))
        block = block / block.norm(dim=1, keepdim=True).clamp(min=1e-6)
        vectors[cursor:cursor + count] = block.to(device=device, dtype=torch.float16)
        for offset, (unit_id, film_id) in enumerate(zip(batch.column("unit_id").to_pylist(),
                                                        batch.column("film_id").to_pylist())):
            unit = units.get(unit_id)
            if unit is None:
                unit = units[unit_id] = len(units)
                film = films.setdefault(film_id, len(films))
                unit_film.append(film)
            row_unit[cursor + offset] = unit
        if group_column:
            for offset, value in enumerate(batch.column(group_column).to_pylist()):
                row_group[cursor + offset] = groups.setdefault(str(value), len(groups))
        for column in extra_columns:
            row_extra[column].extend(batch.column(column).to_pylist())
        row_keys[cursor:cursor + count] = batch.column(key_column).to_pylist()
        cursor += count
    if cursor != total:
        vectors = vectors[:cursor]
        row_unit, row_group, row_keys = row_unit[:cursor], row_group[:cursor], row_keys[:cursor]
    return VectorMatrix(
        name=name, version=int(table.version), device=device, vectors=vectors,
        row_unit=torch.from_numpy(row_unit).to(device), row_group=torch.from_numpy(row_group).to(device),
        groups=list(groups), unit_ids=list(units), unit_film=torch.tensor(unit_film, dtype=torch.int64, device=device),
        films=list(films), row_keys=row_keys,
        row_extra={column: np.asarray(values, dtype=object) for column, values in row_extra.items()})


def matrix(db: Any, name: str, *, vector_column: str, key_column: str, group_column: str | None = None,
           extra_columns: tuple[str, ...] = (), where: str | None = None) -> VectorMatrix | None:
    """The resident copy of *name* at the snapshot's pinned version, loading it once if needed."""
    if _DISABLED or getattr(db, "is_index_snapshot", False) is not True:
        return None                         # only pinned search snapshots have stable versions
    try:
        table = db.open_table(name)
        version = int(table.version)
        key = (str(getattr(db, "uri", "")), name)
        with _LOCK:
            cached = _MATRICES.get(key)
            if cached is not None and cached.version == version:
                return cached
            loading = _LOADING.setdefault(key, threading.Lock())
        with loading:
            with _LOCK:
                cached = _MATRICES.get(key)
                if cached is not None and cached.version == version:
                    return cached
                if cached is not None:
                    del _MATRICES[key]      # release the previous generation before loading the next
            started = time.perf_counter()
            loaded = _load(table, name, vector_column=vector_column, key_column=key_column,
                           group_column=group_column, extra_columns=extra_columns, where=where)
            with _LOCK:
                _MATRICES[key] = loaded
            _LOGGER.info("resident_index table=%s version=%d rows=%d device=%s seconds=%.1f", name, version,
                         len(loaded.row_keys), loaded.device, time.perf_counter() - started)
            return loaded
    except Exception as exc:  # noqa: BLE001 - acceleration only: fall back to Lance
        _LOGGER.warning("resident index for %s unavailable; using Lance: %s", name, exc)
        return None


def similarities(resident: VectorMatrix, query: np.ndarray) -> Any:
    """Cosine similarity of every row to *query* (float32 tensor on the matrix device)."""
    import torch
    vector = torch.as_tensor(np.asarray(query, dtype=np.float32), device=resident.device)
    vector = vector / vector.norm().clamp(min=1e-6)
    if resident.device.type == "cuda":
        return (resident.vectors @ vector.to(torch.float16)).float()
    out = torch.empty(len(resident.vectors), dtype=torch.float32)
    for start in range(0, len(resident.vectors), 131_072):
        out[start:start + 131_072] = resident.vectors[start:start + 131_072].float() @ vector
    return out


def top_rows_by_group(resident: VectorMatrix, query: np.ndarray, groups: Iterable[str], *,
                      film_ids: tuple[str, ...] = (), limit: int) -> dict[str, list[tuple[int, float]]]:
    """Each group's (e.g. text view's) best rows: ``{group: [(row, cosine similarity), ...]}``."""
    import torch
    scores = similarities(resident, query)
    allowed = resident.allowed_rows(film_ids)
    if allowed is not None:
        scores = scores.masked_fill(~allowed, float("-inf"))
    result: dict[str, list[tuple[int, float]]] = {}
    for group in groups:
        if group not in resident.groups:
            result[group] = []
            continue
        masked = scores.masked_fill(resident.row_group != resident.groups.index(group), float("-inf"))
        values, rows = torch.topk(masked, min(limit, len(masked)))
        keep = torch.isfinite(values)
        result[group] = list(zip(rows[keep].tolist(), values[keep].tolist()))
    return result


def top_units(resident: VectorMatrix, query: np.ndarray, *, film_ids: tuple[str, ...] = (),
              limit: int) -> list[tuple[str, float, int]]:
    """Shots ranked by their best row (frame): ``[(unit_id, similarity, best_row), ...]``."""
    import torch
    scores = similarities(resident, query)
    allowed = resident.allowed_rows(film_ids)
    if allowed is not None:
        scores = scores.masked_fill(~allowed, float("-inf"))
    best = torch.full((len(resident.unit_ids),), float("-inf"), device=scores.device)
    best = best.scatter_reduce(0, resident.row_unit, scores, reduce="amax", include_self=True)
    values, units = torch.topk(best, min(limit, len(best)))
    keep = torch.isfinite(values)
    values, units = values[keep], units[keep]
    # The argmax row of each chosen unit: rows of chosen units whose score equals the unit's best.
    chosen = torch.zeros(len(resident.unit_ids), dtype=torch.bool, device=scores.device)
    chosen[units] = True
    candidates = torch.nonzero(chosen[resident.row_unit] & (scores == best[resident.row_unit])).squeeze(1)
    first_row: dict[int, int] = {}
    for row, unit in zip(candidates.tolist(), resident.row_unit[candidates].tolist()):
        first_row.setdefault(unit, row)
    return [(resident.unit_ids[unit], float(value), first_row[unit]) for unit, value in zip(units.tolist(), values.tolist())]
