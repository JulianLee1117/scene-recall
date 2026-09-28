"""Measured job bytes and checked render headroom, without artifact eviction."""
from __future__ import annotations

import logging
from pathlib import Path
import shutil
import time

from pipeline.lab.cleanup import is_render_intermediate
from pipeline.lab.media import _check_render_directory, _check_render_file

_LOG = logging.getLogger(__name__)
MIN_FREE_BYTES = 256 * 1024 * 1024
MAX_SCRATCH_BYTES = 1536 * 1024 * 1024
CHECK_SECONDS = 1.


def measure(root, kind):
    """Read one flat owned job directory. Never follow links or recurse.

    Missing/unavailable/changed storage is unknown, not a false zero total.
    Provider receipt partials remain retained diagnostic evidence.
    """
    root = Path(root)
    try:
        _check_render_directory(root)
        if not root.is_dir():
            return None
        sizes = {}
        for index, path in enumerate(root.iterdir()):
            if index >= 128:
                return None
            _check_render_file(path)
            sizes[path.name] = path.stat().st_size
        scratch = sum(size for name, size in sizes.items() if is_render_intermediate(name, kind))
        return {"output_bytes": sizes.get("output.mp4", 0), "original_bytes": sizes.get("original.media", 0),
                "retained_bytes": sum(sizes.values()) - scratch, "scratch_bytes": scratch}
    except (OSError, ValueError):
        return None


class Guard:
    """Cancellation-compatible, throttled free-space and scratch checks.

    These are checked ceilings, not a reservation or an atomic filesystem quota.
    Retiming also keeps its stricter per-source native/flow scratch ceiling.
    """
    def __init__(self, root, cancelled=lambda: False, *, kind="transition-render"):
        self.root, self.cancelled, self.kind = Path(root), cancelled, kind
        self.next_check = 0.
        self.warned = False

    def __call__(self):
        if self.cancelled():
            return True
        now = time.monotonic()
        if now < self.next_check:
            return False
        self.next_check = now + CHECK_SECONDS
        try:
            free = shutil.disk_usage(self.root).free
        except (OSError, NotImplementedError):
            if not self.warned:
                _LOG.warning("Could not measure free space for transition job %s", self.root.name)
                self.warned = True
        else:
            if free < MIN_FREE_BYTES:
                raise ValueError("Transition rendering needs at least 256 MiB of free space on the assets drive; free space and render again")
        usage = measure(self.root, self.kind)
        if usage and usage["scratch_bytes"] > MAX_SCRATCH_BYTES:
            raise ValueError("Transition rendering exceeded its 1.5 GiB temporary-file budget; shorten the windows or use Draft quality")
        return False
