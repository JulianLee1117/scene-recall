"""Process-local editor resource policy; encoder and evidence identities stay intact."""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
import os
import sys


EDITOR_CPU_THREADS = 2


def configure_editor_process():
    """Reserve CUDA for the ingestion lane before importing model runtimes.

    PE/Qwen continue using the same checkpoints, dimensions and normalization.
    Their process-local caches cannot contain an earlier CUDA model because a
    process that already initialized CUDA is refused rather than repurposed.
    """
    torch = sys.modules.get("torch")
    if torch is not None and torch.cuda.is_initialized():
        raise RuntimeError("Start the editor in a fresh process before CUDA initialization")
    # Windows removes an empty value from the native process environment even
    # though os.environ still retains ''. CUDA reads the native environment.
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ[name] = str(EDITOR_CPU_THREADS)
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    if torch is not None:
        torch.set_num_threads(EDITOR_CPU_THREADS)


def editor_config(config):
    """Copy runtime-only rhythm settings without changing the caller's config."""
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "-1":
        raise RuntimeError("The editor CPU resource profile was not initialized")
    return replace(config, lab=replace(config.lab, beat_device="cpu"))


def editorial_lock(config, role):
    """Legacy all-in-one execution keeps the existing exclusive resource gate."""
    if role == "editor":
        return nullcontext()
    from pipeline.ingest.locks import global_ingest_lock
    return global_ingest_lock(config.paths.assets_dir)
