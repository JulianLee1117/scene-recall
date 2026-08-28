"""Cross-process locks for operations that mutate one film's asset cache."""

from __future__ import annotations

from pathlib import Path

from filelock import FileLock


_FILM_OPERATION_LOCK = ".scene-recall-film.lock"
_FILM_OPERATION_LOCK_TIMEOUT_SECONDS = 600
_FILM_RELINK_JOURNAL = ".scene-recall-relink.json"
_GLOBAL_INGEST_LOCK = ".scene-recall-ingest.lock"


def film_operation_lock(asset_dir: Path) -> FileLock:
    """Serialize ingest and source relocation for one content-addressed film."""
    asset_dir.mkdir(parents=True, exist_ok=True)
    return FileLock(
        asset_dir / _FILM_OPERATION_LOCK,
        timeout=_FILM_OPERATION_LOCK_TIMEOUT_SECONDS,
    )


def global_ingest_lock(assets_dir: Path) -> FileLock:
    """Serialize heavyweight ingestion across API and CLI processes."""
    assets_dir.mkdir(parents=True, exist_ok=True)
    # API jobs are already queued. A separate CLI process should fail clearly
    # instead of appearing to hang behind an hours-long ingest.
    return FileLock(assets_dir / _GLOBAL_INGEST_LOCK, timeout=0)


def film_relink_journal_path(asset_dir: Path) -> Path:
    """Return the durable pending-relink marker for one film cache."""
    return asset_dir / _FILM_RELINK_JOURNAL


def require_no_pending_film_relink(asset_dir: Path) -> None:
    """Refuse cache mutation until an interrupted relink is recovered."""
    journal = film_relink_journal_path(asset_dir)
    if journal.exists():
        raise RuntimeError(
            "an interrupted source relink must be recovered before ingest: "
            f"{journal}; run `python -m pipeline.cli recover-relink "
            f"{asset_dir.name}`"
        )
