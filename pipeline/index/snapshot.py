"""Pinned read-only library views shared by search and editorial workflows.

The publication lock protects capture only. Callers retain their own readiness,
retry and last-complete-generation policy; inference never holds the lock.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from collections import OrderedDict
from threading import Lock
import json

_RECENT_SEARCH = OrderedDict()
_SEARCH_LOCK = Lock()


class SearchLibraryUnavailable(RuntimeError):
    pass


class _ReadTable:
    def __init__(self, table):
        self.__table = table

    @property
    def schema(self):
        return self.__table.schema

    @property
    def version(self):
        return self.__table.version

    def search(self, *args, **kwargs):
        return self.__table.search(*args, **kwargs)

    def count_rows(self, *args, **kwargs):
        return self.__table.count_rows(*args, **kwargs)

    def to_arrow(self):
        return self.__table.to_arrow()

    def list_indices(self):
        return self.__table.list_indices()

    def index_stats(self, *args, **kwargs):
        return self.__table.index_stats(*args, **kwargs)

    def scan_vector_rows(self, vector, *, column, columns, where, limit):
        """Read exact neighbors without scattered scalar-index vector gathers.

        Broad semantic-view filters match many interleaved rows. Gathering
        their vectors through bitmap indexes is much slower than a sequential
        scan. The optional native scanner retains this table's pinned version,
        cosine distances and prefilter. Leave any vector-index policy to the
        regular query path, and retain that path when pylance is not installed.
        """
        if any(column in index.columns for index in self.__table.list_indices()):
            return None
        try:
            dataset = self.__table.to_lance()
        except ImportError:
            return None
        return dataset.scanner(
            columns=columns,
            filter=where.to_sql(),
            nearest={"column": column, "q": vector, "k": limit,
                     "metric": "cosine", "use_index": False},
            prefilter=True,
            use_scalar_index=False,
        ).to_table().to_pylist()


class IndexSnapshot:
    is_index_snapshot = True

    def __init__(self, uri, tables, manifests):
        self.uri = uri
        self.__tables = tables
        self.__manifests = deepcopy(manifests)
        self.versions = {name: table.version for name, table in tables.items()}

    def open_table(self, name):
        return self.__tables[name]

    def list_tables(self):
        return SimpleNamespace(tables=list(self.__tables))

    def table_names(self, **_kwargs):
        return list(self.__tables)

    def read_profile_manifest(self, path):
        return deepcopy(self.__manifests.get(str(Path(path).resolve())))


@contextmanager
def publication_read(db, *, timeout=600):
    """Exclude publication for a short current-index read, or use pinned rows."""
    if isinstance(db, IndexSnapshot):
        yield
        return
    from pipeline.index.writer import _PUBLICATION_LOCK, _database_write_lock
    from filelock import Timeout
    # Callers must not nest this with a publication writer: its threading lock
    # is deliberately non-reentrant. Snapshot reads need no lock at all.
    if not _PUBLICATION_LOCK.acquire(timeout=timeout):
        raise Timeout(str(getattr(db, "uri", "index publication")))
    try:
        lock = _database_write_lock(db)
        with lock.acquire(timeout=timeout) if hasattr(lock, "acquire") else lock:
            yield
    finally:
        _PUBLICATION_LOCK.release()


def capture_snapshot(config, db, *, require_semantic_ready=False):
    from pipeline.index import framing_features, text_features
    from pipeline.index.writer import table_names

    with publication_read(db, timeout=.1):
        tables = {}
        for name in table_names(db):
            table = db.open_table(name)
            table.checkout(table.version)
            tables[name] = _ReadTable(table)
        text_path = text_features.manifest_path(config, text_features.configured_text_profile(config))
        manifests = {str(text_path.resolve()): text_features._read_manifest(text_path)}
        try:
            framing = framing_features.configured_framing_spatial_profile(config)
        except (OSError, RuntimeError, ValueError):
            framing = None
        if framing is not None:
            path = framing_features.manifest_path(config, framing)
            manifests[str(path.resolve())] = framing_features._read_manifest(path)
        # Capture profile activation and coverage with the same table versions.
        # Include shadow profiles so an evaluator can select one after capture.
        profiles = Path(config.paths.assets_dir) / "search-profiles"
        for path in profiles.glob("composition_*/*.json"):
            if path.name not in {"coverage.json", "promotion.json"}:
                continue
            try:
                manifests[str(path.resolve())] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                manifests[str(path.resolve())] = None
        snapshot = IndexSnapshot(db.uri, tables, manifests)
        # Never replace a complete semantic library with a half-published one.
        # Libraries that have never built this optional index keep their normal
        # capability behavior; no semantic evidence or fallback is invented.
        if require_semantic_ready and text_path.exists() and text_features.resolve_ready_text_profile(config, snapshot) is None:
            return None
        return snapshot


def acquire_search_snapshot(config, db):
    """Pin one request; retain a prior view only while a writer holds the lock."""
    from filelock import Timeout
    key = (str(db.uri), config.models.visual_encoder, config.models.text_encoder)
    try:
        snapshot = capture_snapshot(config, db)
    except Timeout as exc:
        with _SEARCH_LOCK:
            prior = _RECENT_SEARCH.get(key)
        if prior is not None:
            return prior
        raise SearchLibraryUnavailable("The film library is being published; retry in a moment") from exc
    with _SEARCH_LOCK:
        _RECENT_SEARCH[key] = snapshot
        _RECENT_SEARCH.move_to_end(key)
        while len(_RECENT_SEARCH) > 16:
            _RECENT_SEARCH.popitem(last=False)
    return snapshot


