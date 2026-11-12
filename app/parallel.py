"""Reading windows in parallel.

The Cactus runtime serialises every call inside one process, and one call
only uses about half the cores, so the windows of a page are read by a small
pool of worker processes instead of one after another.

The pool is created before any inference happens, so the workers start from a
clean process, and each one loads its own Mirage instance.
"""
from __future__ import annotations

import multiprocessing
import os
import sys
from concurrent.futures import ProcessPoolExecutor

from PIL import Image

_pool: ProcessPoolExecutor | None = None
_model = None


def worker_count() -> int:
    """How many reader processes to use. Zero means read in this process.

    Off by default: the pool is only worth it on a machine with cores to
    spare, and it costs a Mirage instance per worker.
    """
    if not sys.platform.startswith("linux"):
        # macOS and Windows spawn their children, and a spawned child re-imports
        # the entry point, which for a server means starting a second server.
        return 0
    try:
        return max(0, int(os.environ.get("OCR_WORKERS", "1")))
    except ValueError:
        return 1


def _init_worker() -> None:
    global _model
    import cactus

    _model = cactus.Mirage()


def _read(payload):
    box, rgb, width, height, text_conf = payload
    result = _model.describe(
        Image.frombytes("RGB", (width, height), rgb),
        sections=["text"],
        fields=["bbox", "score"],
        text_conf=text_conf,
    )
    return box, result.get("text", [])


def _noop() -> None:
    return None


def pool() -> ProcessPoolExecutor | None:
    """The pool of readers, created once and kept.

    The workers are forked, not spawned: a spawned worker re-imports the
    entry point, which for a server means starting a second server. Forking
    is only safe while no inference is running, because the engine holds a
    lock around every call, so the pool is created at startup, before the
    first request.

    The executor starts its processes the first time something is submitted,
    so a throwaway job is submitted here to make the fork happen now.
    """
    global _pool
    if worker_count() <= 0:
        return None
    if _pool is None:
        executor = ProcessPoolExecutor(
            max_workers=worker_count(),
            initializer=_init_worker,
            mp_context=multiprocessing.get_context("fork"),
        )
        executor.submit(_noop).result()  # fork the workers now, not on the first request
        _pool = executor
    return _pool


def existing() -> ProcessPoolExecutor | None:
    """The pool, if it was created already. Never creates one."""
    return _pool


def shutdown() -> None:
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False)
        _pool = None
