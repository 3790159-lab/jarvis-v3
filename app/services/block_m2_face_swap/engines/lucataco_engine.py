# -*- coding: utf-8 -*-
"""LucatacoSwapEngine — the active (today) SwapEngine implementation.

Encodes each local target as a down-scaled JPEG data-URI (verified accepted by
lucataco — no hosting), runs all targets concurrently behind a semaphore, and
downloads each result to ``source.parent/results/NNN.jpg``. Per-target failures
(no-face = output None, or PredictionFailed) map to ``None`` in the result list
so the orchestrator's "95/100" tally is accurate.

Rate-limit hardening (2026-06-21):
  * Default concurrency reduced to 2 (env SWAPBATCH_CONCURRENCY overrides).
  * LucatacoTransientError (429/network exhausted, not billed) triggers a
    post-first-pass retry sweep: failed-transient targets are re-submitted
    SEQUENTIALLY with a 2-second gap so burst pressure has subsided.
  * NO_FACE (succeeded+output=None) and PredictionFailed are TERMINAL BILLABLE
    — they are NEVER re-submitted by the sweep.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import os
from pathlib import Path

import httpx
from PIL import Image

from app.services.block_m_common.faceswap_client import PredictionFailed
from app.services.block_m_common.lucataco_client import LucatacoClient, LucatacoTransientError
from .base import CancelCheck, ProgressCb

logger = logging.getLogger(__name__)

# Cost metadata (single source of truth; factory imports these).
COST_PER_SWAP_USD = 0.005
COLD_START_USD = 0.0

_MAX_SIDE = 1600          # down-scale longest edge before base64 (bandwidth)
_JPEG_QUALITY = 90
_DEFAULT_CONCURRENCY = 2  # env SWAPBATCH_CONCURRENCY overrides at runtime

_DOWNLOAD_MAX_RETRIES = 3  # re-GETting the delivery URL is free (no re-bill)


def _to_data_uri(path: Path) -> str:
    """Down-scale to <= _MAX_SIDE longest edge, re-encode JPEG, base64 data-URI."""
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((_MAX_SIDE, _MAX_SIDE))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=_JPEG_QUALITY)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64}"


class LucatacoSwapEngine:
    name = "lucataco"
    cost_per_swap_usd = COST_PER_SWAP_USD

    def __init__(
        self,
        api_token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        download_transport: httpx.BaseTransport | None = None,
        concurrency: int | None = None,
    ) -> None:
        self._client = LucatacoClient(api_token, transport=transport)
        self._download_transport = download_transport
        self._concurrency = (
            concurrency if concurrency is not None
            else int(os.getenv("SWAPBATCH_CONCURRENCY", str(_DEFAULT_CONCURRENCY)))
        )

    async def swap_batch(
        self,
        source: Path,
        targets: list[Path],
        *,
        progress_cb: ProgressCb | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> list[Path | None]:
        try:
            source_uri = _to_data_uri(source)
        except Exception as exc:
            raise ValueError(f"source image unreadable: {exc}") from exc

        sem = asyncio.Semaphore(self._concurrency)
        results_dir = source.parent / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        total = len(targets)
        done = {"n": 0}
        done_lock = asyncio.Lock()

        # --- per-target attempt helper ---
        # Returns (path_or_None, is_transient).
        #   success            -> (Path, False)
        #   no-face            -> (None, False)  # terminal, billable — NO sweep
        #   PredictionFailed   -> (None, False)  # terminal, billable — NO sweep
        #   LucatacoTransient  -> (None, True)   # not billed, safe to retry
        #   other Exception    -> (None, False)  # terminal, don't retry
        #   download failure   -> (None, False)  # already paid; _download retries internally
        async def _attempt(idx: int, target: Path) -> tuple[Path | None, bool]:
            try:
                out_url = await self._client.swap(source_uri, _to_data_uri(target))
            except LucatacoTransientError as exc:
                logger.warning("lucataco transient idx=%d (will sweep): %s", idx, exc)
                return None, True
            except PredictionFailed as exc:
                logger.warning("lucataco PredictionFailed idx=%d: %s", idx, exc)
                return None, False  # terminal, billable
            except Exception as exc:  # noqa: BLE001
                logger.warning("lucataco swap idx=%d error: %s", idx, exc)
                return None, False  # terminal (4xx or unexpected)

            if out_url is None:
                # succeeded + output=None = no face detected; BILLABLE, never retry
                return None, False

            try:
                result = await self._download(out_url, results_dir / f"{idx:03d}.jpg")
                return result, False
            except Exception as exc:  # noqa: BLE001
                # Prediction already succeeded (billing happened); do NOT mark
                # transient — that would re-submit and re-bill. _download's
                # internal retry loop is the safety net here.
                logger.warning("lucataco download idx=%d failed: %s", idx, exc)
                return None, False

        async def _one(idx: int, target: Path) -> tuple[Path | None, bool]:
            """Run _attempt under the semaphore; honour cancel_check."""
            if cancel_check and cancel_check():
                return None, False
            async with sem:
                if cancel_check and cancel_check():
                    return None, False
                result, is_transient = await _attempt(idx, target)
            async with done_lock:
                done["n"] += 1
                completed = done["n"]
            if progress_cb:
                progress_cb(
                    "swap_progress",
                    {"completed": completed, "total": total,
                     "index": idx, "ok": result is not None},
                )
            return result, is_transient

        # --- First pass: all targets concurrently ---
        first_pass = await asyncio.gather(*[_one(i, t) for i, t in enumerate(targets)])
        results: list[Path | None] = [r for r, _ in first_pass]
        transient_idxs = [i for i, (_, is_t) in enumerate(first_pass) if is_t]

        # --- Retry sweep: ONLY transient indices, sequentially ---
        if transient_idxs:
            logger.info(
                "lucataco retry sweep: %d transient target(s): %s",
                len(transient_idxs), transient_idxs,
            )
            for idx in transient_idxs:
                if cancel_check and cancel_check():
                    break
                target = targets[idx]
                if progress_cb:
                    progress_cb(
                        "swap_retry",
                        {"index": idx, "total": total},
                    )
                result, _is_t = await _attempt(idx, target)
                if result is not None:
                    results[idx] = result
                # if still transient after sweep, leave as None (one sweep is enough)
                await asyncio.sleep(2)

        return results

    async def _download(self, url: str, dest: Path) -> Path:
        """Download with internal retry — re-GETting the same URL is free."""
        last_exc: Exception | None = None
        for attempt in range(1, _DOWNLOAD_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=60.0, transport=self._download_transport
                ) as c:
                    resp = await c.get(url)
                    resp.raise_for_status()
                    dest.write_bytes(resp.content)
                return dest
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning(
                    "download attempt %d/%d failed for %s: %s",
                    attempt, _DOWNLOAD_MAX_RETRIES, url, exc,
                )
                if attempt < _DOWNLOAD_MAX_RETRIES:
                    await asyncio.sleep(2 ** attempt)
        raise RuntimeError(
            f"download failed after {_DOWNLOAD_MAX_RETRIES} attempts: {last_exc}"
        ) from last_exc
