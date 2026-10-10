"""Still frames of source instants for the Match Cuts workspace (content picture, cached on disk).

Frames are decoded from the retained film (nearest decoded frame at or after
the requested time), cropped to the film's content box so image coordinates
equal the index's content coordinates, and cached as JPEG under
``assets_dir/matching/frames``. The cache is disposable.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import threading
from typing import Any

from pipeline.source_frames import decode

WIDTHS = (320, 640, 1280)
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="match-frames")
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def cache_path(config: Any, film_id: str, time_value: float, width: int) -> Path:
    key = f"{film_id}:{time_value:.3f}:{width}"
    name = hashlib.sha256(key.encode()).hexdigest()[:24]
    return Path(config.paths.assets_dir) / "matching" / "frames" / film_id[:16] / f"{name}.jpg"


def frame(config: Any, db: Any, film_id: str, time_value: float, width: int, content_box: list[float] | None) -> Path:
    """Path of the cached content-picture JPEG for one instant (decoding it when missing)."""
    from PIL import Image
    from pipeline.lab.media import resolve_film
    width = min(WIDTHS, key=lambda value: abs(value - width))
    path = cache_path(config, film_id, time_value, width)
    if path.is_file():
        return path
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(str(path), threading.Lock())
    with lock:
        if path.is_file():
            return path
        source = Path(resolve_film(db, film_id)["path"])
        image = decode(source, time_value)
        if content_box:
            x0, y0, x1, y1 = content_box
            image = image.crop((round(x0 * image.width), round(y0 * image.height),
                                round(x1 * image.width), round(y1 * image.height)))
        image = image.resize((width, max(2, round(image.height * width / image.width))), Image.Resampling.LANCZOS)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".tmp-{threading.get_ident()}-{path.name}")
        image.save(temporary, format="JPEG", quality=86)
        temporary.replace(path)
    with _LOCKS_GUARD:
        _LOCKS.pop(str(path), None)
    return path


def prefetch(config: Any, db: Any, items: list[tuple[str, float, list[float] | None]], width: int = 640) -> None:
    """Decode frames in the background so the workspace's thumbnails arrive warm."""
    for film_id, time_value, content_box in items:
        _POOL.submit(_quiet, config, db, film_id, time_value, width, content_box)


def _quiet(*args: Any) -> None:
    try:
        frame(*args)
    except Exception:  # noqa: BLE001 - a prefetch miss is decoded again on request
        pass
