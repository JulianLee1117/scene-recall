"""Read-only source stills shared by saved moments and Match Cuts.

Saved thumbnails use a bounded in-memory cache, independent of derived shot
and match indexes. A source fingerprint invalidates bytes if the file changes.
"""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from pathlib import Path
import threading

_DECODERS = threading.BoundedSemaphore(2)


def decode(path: Path, time_value: float, *, thread_count: int = 0):
    """First decoded frame at or after film seconds, preserving display aspect."""
    import av
    from PIL import Image

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        stream.thread_count = thread_count
        origin = container.start_time / av.time_base if container.start_time is not None else 0.0
        sar = float(stream.sample_aspect_ratio or 1)
        container.seek(max(0, int((time_value - 1.0 + origin) / stream.time_base)), stream=stream, backward=True)
        chosen = None
        for frame in container.decode(stream):
            if frame.pts is None:
                continue
            chosen = frame
            if float(frame.pts * stream.time_base) - origin + 1e-3 >= time_value:
                break
        if chosen is None:
            raise ValueError("No frame at this time")
        image = chosen.to_image().convert("RGB")
    if abs(sar - 1) > 1e-3:
        image = image.resize((max(2, round(image.width * sar)), image.height), Image.Resampling.BICUBIC)
    return image


@lru_cache(maxsize=128)
def _thumbnail(source: str, size: int, mtime_ns: int, timestamp_ms: int) -> bytes:
    from PIL import Image

    # size and mtime_ns are cache identity, not decode parameters.
    with _DECODERS:
        image = decode(Path(source), timestamp_ms / 1000, thread_count=2)
        image.thumbnail((640, 640), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, format="JPEG", quality=86)
        return output.getvalue()


def thumbnail(source: Path, timestamp: float) -> bytes:
    """A bounded display JPEG at a saved source moment; never writes to disk."""
    stat = source.stat()
    return _thumbnail(str(source.resolve()), stat.st_size, stat.st_mtime_ns, round(timestamp * 1000))
