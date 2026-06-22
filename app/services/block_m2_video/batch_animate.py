# -*- coding: utf-8 -*-
"""Engine-agnostic concurrent runner for N video generations.

Money-safe like the swap engine: a semaphore caps concurrent generations (429
defense), each generation is isolated, and only TRANSIENT failures
(TransientVideoError) are retried in a final sequential sweep. TERMINAL/billable
failures are recorded as None and NEVER retried. Returns a list aligned to
``requests`` (Path on success, None on failure).
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .engines.errors import TransientVideoError

logger = logging.getLogger(__name__)


async def animate_batch(
    engine,
    requests: list,
    *,
    concurrency: int = 2,
    progress_cb=None,
    cancel_check=None,
    sweep_pause: float = 2.0,
) -> list:
    total = len(requests)
    results: list = [None] * total
    transient_idxs: list = []
    sem = asyncio.Semaphore(max(1, concurrency))
    done = {"n": 0}
    done_lock = asyncio.Lock()

    async def _attempt(idx: int, req):
        try:
            res = await engine.generate(req)
            return res.output_path, False
        except TransientVideoError as exc:
            logger.warning("animate idx=%d transient: %s", idx, exc)
            return None, True
        except Exception as exc:  # noqa: BLE001 — terminal, do not retry
            logger.warning("animate idx=%d terminal: %s", idx, exc)
            return None, False

    async def _one(idx: int, req) -> None:
        if cancel_check and cancel_check():
            return
        async with sem:
            if cancel_check and cancel_check():
                return
            path, transient = await _attempt(idx, req)
        results[idx] = path
        if transient:
            transient_idxs.append(idx)
        async with done_lock:
            done["n"] += 1
            completed = done["n"]
        if progress_cb:
            progress_cb("animate_progress", {"completed": completed, "total": total, "index": idx, "ok": path is not None})

    await asyncio.gather(*[_one(i, r) for i, r in enumerate(requests)])

    # Sweep: retry ONLY transient failures, sequentially, with a pause.
    for idx in list(transient_idxs):
        if cancel_check and cancel_check():
            break
        if sweep_pause:
            await asyncio.sleep(sweep_pause)
        path, _ = await _attempt(idx, requests[idx])
        if path is not None:
            results[idx] = path
        if progress_cb:
            progress_cb("animate_retry", {"index": idx, "ok": path is not None})
    return results
