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


def _fire(progress_cb, stage: str, payload: dict) -> None:
    """Call progress_cb defensively — a callback error must never abort the batch."""
    if not progress_cb:
        return
    try:
        progress_cb(stage, payload)
    except Exception:  # noqa: BLE001 — progress is best-effort, never fatal
        logger.warning("animate progress_cb raised, ignoring", exc_info=True)


async def animate_batch(
    engine,
    requests: list,
    *,
    concurrency: int = 2,
    progress_cb=None,
    cancel_check=None,
    sweep_pause: float = 2.0,
    errors_out: dict[int, str] | None = None,
) -> list[Path | None]:
    """Run N generations, money-safe. If ``errors_out`` is given, the raw failure
    reason for each index that ends up with NO video (terminal, or a transient
    that survives the sweep) is recorded there so callers can surface WHY a frame
    failed (e.g. Seedance E005 censorship). Recovered frames leave no entry.
    """
    total = len(requests)
    results: list[Path | None] = [None] * total
    transient_idxs: list[int] = []
    sem = asyncio.Semaphore(max(1, concurrency))
    completed = 0

    def _record(idx: int, reason: str) -> None:
        if errors_out is not None:
            errors_out[idx] = reason

    async def _attempt(idx: int, req):
        try:
            res = await engine.generate(req)
            return res.output_path, False, ""
        except TransientVideoError as exc:
            logger.warning("animate idx=%d transient: %s", idx, exc)
            return None, True, str(exc)
        except Exception as exc:  # noqa: BLE001 — terminal, do not retry
            logger.warning("animate idx=%d terminal: %s", idx, exc)
            return None, False, str(exc)

    async def _one(idx: int, req) -> None:
        nonlocal completed
        if cancel_check and cancel_check():
            return
        async with sem:
            if cancel_check and cancel_check():
                return
            path, transient, reason = await _attempt(idx, req)
        results[idx] = path
        if transient:
            transient_idxs.append(idx)        # reason recorded later iff sweep fails
        elif path is None:
            _record(idx, reason)              # terminal — record now
        # Safe under asyncio's cooperative model: no await between increment and use.
        completed += 1
        _fire(progress_cb, "animate_progress",
              {"completed": completed, "total": total, "index": idx, "ok": path is not None})

    await asyncio.gather(*[_one(i, r) for i, r in enumerate(requests)])

    # Sweep: retry ONLY transient failures, sequentially, with a pause.
    for idx in list(transient_idxs):
        if cancel_check and cancel_check():
            break
        if sweep_pause > 0:
            await asyncio.sleep(sweep_pause)
        path, _, reason = await _attempt(idx, requests[idx])
        if path is not None:
            results[idx] = path
        else:
            _record(idx, reason)              # transient survived the sweep
        _fire(progress_cb, "animate_retry", {"index": idx, "ok": path is not None})
    return results
