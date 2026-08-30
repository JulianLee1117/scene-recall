"""Focused API startup and native-lock hardening tests."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import MagicMock, patch

import pytest

from pipeline.config import Config


def test_runtime_preflight_checks_database_and_user_state(
    config: Config,
) -> None:
    from pipeline.api.main import _preflight_runtime_paths

    _preflight_runtime_paths(config)

    assert (config.paths.assets_dir / "db").is_dir()
    assert config.paths.state_dir.is_dir()
    assert not list(
        config.paths.assets_dir.rglob(".scene-recall-write-test-*")
    )
    assert not list(
        config.paths.state_dir.rglob(".scene-recall-write-test-*")
    )


@pytest.mark.parametrize(
    ("path_name", "purpose"),
    (("database", "database"), ("state", "user-state")),
)
def test_runtime_preflight_names_an_unwritable_configured_path(
    config: Config,
    path_name: str,
    purpose: str,
) -> None:
    from pipeline.api.main import _preflight_runtime_paths

    blocked_dir = (
        config.paths.assets_dir / "db"
        if path_name == "database"
        else config.paths.state_dir
    )

    def fail_only_for_blocked_path(*args: object, **kwargs: object):
        if Path(str(kwargs.get("dir"))) == blocked_dir:
            raise PermissionError("write access denied")
        return NamedTemporaryFile(*args, **kwargs)

    with (
        patch(
            "pipeline.api.main.NamedTemporaryFile",
            side_effect=fail_only_for_blocked_path,
        ),
        pytest.raises(RuntimeError) as exc_info,
    ):
        _preflight_runtime_paths(config)

    message = str(exc_info.value)
    assert f"{purpose} directory" in message
    assert str(blocked_dir) in message
    assert "filesystem access" in message


def test_project_native_locks_preserve_stable_lock_paths(
    tmp_path: Path,
    config: Config,
) -> None:
    from pipeline.index.writer import _database_write_lock, open_db
    from pipeline.ingest.locks import film_operation_lock, global_ingest_lock

    locks = (
        film_operation_lock(tmp_path / "film"),
        global_ingest_lock(tmp_path / "assets"),
        _database_write_lock(open_db(config)),
    )
    for lock in locks:
        with lock:
            assert Path(lock.lock_file).is_file()
        assert Path(lock.lock_file).is_file()
        with lock:
            pass


def test_database_write_lock_ignores_dynamic_test_double_uris() -> None:
    from pipeline.index.writer import _database_write_lock

    assert isinstance(_database_write_lock(MagicMock()), nullcontext)
