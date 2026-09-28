"""API compatibility facade for durable FIFO ingestion."""

from pipeline.lab.store import LabStore


class DurableIngestQueue:
    def __init__(self, store: LabStore):
        self.store = store

    def enqueue(self, path):
        job = self.store.enqueue("ingest", path=path)
        return next(item for item in self.snapshots() if item["job_id"] == job["id"])

    def snapshots(self):
        return self.store.ingest_snapshots()

    def close(self):
        """API shutdown leaves pending and active jobs owned by the worker."""
