"""Bounded native endpoint decoding for manual external AI bridge experiments."""
from pathlib import Path
import time

import av
from PIL import Image

from pipeline.lab.media import JobCancelled


def endpoint(path: Path, clip, *, outgoing, destination, cancelled=lambda: False):
    """Export the last-A / first-B native frame strictly inside its source window."""
    deadline = time.monotonic() + 45
    chosen, timestamp = None, None
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        origin = (container.start_time or 0) / av.time_base
        container.seek(max(0, int((clip["source_start"] + origin) / stream.time_base)), stream=stream, backward=True)
        for frame in container.decode(stream):
            if cancelled():
                raise JobCancelled("Transition render cancelled")
            if time.monotonic() > deadline:
                raise ValueError("Source endpoint decoding exceeded its time budget")
            if frame.pts is None:
                continue
            current = float(frame.pts * stream.time_base) - origin
            if current >= clip["source_end"] - 1e-8:
                break
            if current < clip["source_start"] - 1e-8:
                continue
            chosen, timestamp = frame, current
            if not outgoing:
                break
        if chosen is None:
            raise ValueError("The selected window contains no native source frame")
        image = chosen.to_image()
        sar = float(stream.sample_aspect_ratio or 1)
        if abs(sar - 1) > 1e-6:
            image = image.resize((max(1, round(image.width * sar)), image.height), Image.Resampling.LANCZOS)
        angle = chosen.rotation or float(stream.metadata.get("rotate", "0"))
        if angle % 360:
            image = image.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
        image.save(destination, format="JPEG", quality=95)
    return timestamp
