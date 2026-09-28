"""Prepare process-local search models during otherwise idle editor time."""
from __future__ import annotations

import time


class EditorSearchWarmup:
    """Attempt each encoder once, between jobs, without holding index locks.

    Readiness may appear after the worker starts, so empty or partially published
    libraries are retried at a bounded interval. Failed model loads are not
    repeatedly retried in the idle loop; ordinary search keeps its own errors.
    """

    def __init__(self):
        self.attempted = set()
        self.next_check = 0.0

    def step(self, config, db, should_yield):
        if self.attempted == {"visual", "text"} or time.monotonic() < self.next_check:
            return
        self.next_check = time.monotonic() + 30
        if should_yield():
            return
        from pipeline.lab.resources import editor_config
        config = editor_config(config)  # Refuse accidental GPU-worker use.
        from pipeline.index.writer import table_names
        from pipeline.index.text_features import resolve_ready_text_profile
        from pipeline.lab.index_snapshot import publication_read
        try:
            with publication_read(db, timeout=.1):
                names = set(table_names(db))
                visual_ready = "frames" in names and db.open_table("frames").count_rows() > 0
                text_ready = resolve_ready_text_profile(config, db) is not None
        except Exception as exc:
            print(f"[editor] Search warmup deferred: {exc}", flush=True)
            return
        for kind, ready in (("visual", visual_ready), ("text", text_ready)):
            if not ready or kind in self.attempted or should_yield():
                continue
            self.attempted.add(kind)
            started = time.perf_counter()
            print(f"[editor] Warming {kind} search model while idle...", flush=True)
            try:
                if kind == "visual":
                    from pipeline.ingest.embed import embed_text
                    embed_text(["warmup"], config)
                else:
                    from pipeline.ingest.text_embed import embed_semantic_query
                    embed_semantic_query("warmup", config)
                print(f"[editor] {kind.capitalize()} search model ready in {time.perf_counter() - started:.1f}s", flush=True)
            except Exception as exc:
                print(f"[editor] {kind.capitalize()} search warmup unavailable: {exc}", flush=True)
