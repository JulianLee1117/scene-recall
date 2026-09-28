"""One bounded, read-only library generation per editorial job.

The editor retains only its latest complete snapshot between jobs. Publication
locks cover capture, never model inference, hosted requests, or playback.
"""
from __future__ import annotations

import time


_SNAPSHOTS = {}
READINESS_WAIT_SECONDS = 60


from pipeline.index.snapshot import IndexSnapshot, publication_read, capture_snapshot


def _capture(config, db):
    return capture_snapshot(config, db, require_semantic_ready=True)


def acquire_editor_snapshot(config, db, progress, cancelled, *, timeout=READINESS_WAIT_SECONDS):
    from filelock import Timeout
    from pipeline.lab.media import JobCancelled
    key = (str(db.uri), config.models.visual_encoder, config.models.text_encoder)
    deadline = time.monotonic() + timeout
    announced = False
    while True:
        if cancelled():
            raise JobCancelled("Job cancelled while waiting for the searchable library")
        try:
            snapshot = _capture(config, db)
        except Timeout:
            snapshot = None
        if snapshot is not None:
            _SNAPSHOTS[key] = snapshot
            return snapshot
        if key in _SNAPSHOTS:
            progress("Using the last complete searchable library while ingestion publishes new evidence")
            return _SNAPSHOTS[key]
        if not announced:
            progress("Waiting for ingestion to finish publishing the searchable library; no AI requests started")
            announced = True
        if time.monotonic() >= deadline:
            raise RuntimeError("The searchable library is still being published. No AI requests were made; retry when indexing completes.")
        time.sleep(min(.5, max(0, deadline - time.monotonic())))
