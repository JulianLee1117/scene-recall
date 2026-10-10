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
_LOADING: dict[tuple[str, ...], threading.Lock] = {}
_MATRICES: dict[tuple[str, ...], "VectorMatrix"] = {}
_ROWS: dict[tuple[str, ...], "RowTable"] = {}
_BATCH_ROWS = 65_536
_GPU_HEADROOM_BYTES = 3584 * 1024 ** 2   # also room for models that load after the matrices (reranker ~1.5 GB with activations)
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
    _key_index: dict[str, int] | None = None

    def key_index(self) -> dict[str, int]:
        """Row of each key (built on first use)."""
        if self._key_index is None:
            self._key_index = {str(key): row for row, key in enumerate(self.row_keys)}
        return self._key_index

    def vectors_for(self, keys: Iterable[str]) -> dict[str, np.ndarray]:
        """Stored (normalized) vectors of the given keys as float32 arrays."""
        import torch
        index = self.key_index()
        wanted = [(key, index[key]) for key in keys if key in index]
        if not wanted:
            return {}
        rows = torch.tensor([row for _key, row in wanted], dtype=torch.int64, device=self.device)
        block = self.vectors[rows].float().cpu().numpy()
        return {key: block[position] for position, (key, _row) in enumerate(wanted)}

    def allowed_rows(self, film_ids: tuple[str, ...]) -> Any | None:
        """Boolean row mask for a film scope and the running search's shot filters (None when unscoped)."""
        import torch
        from pipeline.search.request import unit_scope

        allowed = None
        if film_ids:
            wanted = [index for index, film in enumerate(self.films) if film in set(film_ids)]
            film_ok = torch.zeros(len(self.films), dtype=torch.bool, device=self.device)
            film_ok[wanted] = True
            allowed = film_ok[self.unit_film[self.row_unit]]
        scope = unit_scope()
        if scope is not None:
            # Shot filters mask rows before top-k, so each channel's depth is all in scope.
            unit_ok = torch.from_numpy(scope.mask_for(self.unit_ids, (self.name, self.version))).to(self.device)
            shot_rows = unit_ok[self.row_unit]
            allowed = shot_rows if allowed is None else allowed & shot_rows
        return allowed


def disable() -> None:
    """Turn resident search off for this process (tests, constrained hosts)."""
    global _DISABLED
    _DISABLED = True


def _device_for(bytes_needed: int) -> Any:
    import torch
    if torch.cuda.is_available():
        free, _total = torch.cuda.mem_get_info()
        free += torch.cuda.memory_reserved() - torch.cuda.memory_allocated()   # this process's own reusable cache
        if free - bytes_needed > _GPU_HEADROOM_BYTES:
            return torch.device("cuda")
    return torch.device("cpu")


def _load(table: Any, name: str, *, vector_column: str, key_column: str, group_column: str | None,
          extra_columns: tuple[str, ...], where: str | None) -> VectorMatrix:
    import torch

    columns = list(dict.fromkeys([key_column, "unit_id", "film_id", vector_column, *extra_columns,
                                  *([group_column] if group_column else [])]))
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
    matrix = VectorMatrix(
        name=name, version=int(table.version), device=device, vectors=vectors,
        row_unit=torch.from_numpy(row_unit).to(device), row_group=torch.from_numpy(row_group).to(device),
        groups=list(groups), unit_ids=list(units), unit_film=torch.tensor(unit_film, dtype=torch.int64, device=device),
        films=list(films), row_keys=row_keys,
        row_extra={column: np.asarray(values, dtype=object) for column, values in row_extra.items()})
    if device.type == "cuda":
        # Loading streams batches through temporary device tensors; hand those cached blocks back
        # so free-memory checks (the reranker's, other processes') see what is really available.
        torch.cuda.empty_cache()
    return matrix


def matrix(db: Any, name: str, *, vector_column: str, key_column: str, group_column: str | None = None,
           extra_columns: tuple[str, ...] = (), where: str | None = None) -> VectorMatrix | None:
    """The resident copy of *name* at the snapshot's pinned version, loading it once if needed."""
    if _DISABLED or getattr(db, "is_index_snapshot", False) is not True:
        return None                         # only pinned search snapshots have stable versions
    try:
        table = db.open_table(name)
        version = int(table.version)
        key = (str(getattr(db, "uri", "")), name, vector_column)
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
                stale_on_gpu = cached is not None and cached.device.type == "cuda"
                _MATRICES.pop(key, None)    # release the previous generation before loading the next,
                cached = None               # this reference included, or it stays allocated during the load
            if stale_on_gpu:
                import torch
                torch.cuda.empty_cache()    # hand its blocks back so this load and other processes can use them
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


@dataclass
class RowTable:
    """Scalar columns of one pinned table version in memory, addressable by key."""

    name: str
    version: int
    table: Any                   # pyarrow.Table
    index: dict[str, int]

    def take(self, keys: Iterable[str]) -> list[dict[str, Any]]:
        import pyarrow as pa
        positions = [self.index[key] for key in dict.fromkeys(keys) if key in self.index]
        if not positions:
            return []
        return self.table.take(pa.array(positions, type=pa.int64())).to_pylist()


def rows(db: Any, name: str, *, key_column: str, columns: Iterable[str]) -> RowTable | None:
    """The resident scalar columns of *name* at the snapshot's pinned version (loaded once per version)."""
    if _DISABLED or getattr(db, "is_index_snapshot", False) is not True:
        return None
    try:
        import pyarrow as pa
        table = db.open_table(name)
        version = int(table.version)
        wanted = list(dict.fromkeys([key_column, *columns]))
        key = (str(getattr(db, "uri", "")), name, "rows", ",".join(wanted))
        with _LOCK:
            cached = _ROWS.get(key)
            if cached is not None and cached.version == version:
                return cached
            loading = _LOADING.setdefault(key, threading.Lock())
        with loading:
            with _LOCK:
                cached = _ROWS.get(key)
                if cached is not None and cached.version == version:
                    return cached
            started = time.perf_counter()
            data = pa.Table.from_batches(list(table.to_batches(columns=wanted)))
            loaded = RowTable(name=name, version=version, table=data,
                              index={str(value): row for row, value in enumerate(data.column(key_column).to_pylist())})
            with _LOCK:
                _ROWS[key] = loaded
            _LOGGER.info("resident_rows table=%s version=%d rows=%d mb=%.0f seconds=%.1f", name, version,
                         data.num_rows, data.nbytes / 1e6, time.perf_counter() - started)
            return loaded
    except Exception as exc:  # noqa: BLE001 - acceleration only: fall back to Lance
        _LOGGER.warning("resident rows for %s unavailable; using Lance: %s", name, exc)
        return None
