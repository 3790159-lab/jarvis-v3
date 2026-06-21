# -*- coding: utf-8 -*-
"""LucatacoSwapEngine — the active (today) SwapEngine implementation.

Encodes each local target as a down-scaled JPEG data-URI (verified accepted by
lucataco — no hosting), runs all targets concurrently behind a semaphore, and
downloads each result to ``source.parent/results/NNN.jpg``. Per-target failures
(no-face = output None, or PredictionFailed) map to ``None`` in the result list
so the orchestrator's "95/100" tally is accurate.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
from pathlib import Path

import httpx
from PIL import Image

from app.services.block_m_common.faceswap_client import PredictionFailed
from app.services.block_m_common.lucataco_client import LucatacoClient
from .base import CancelCheck, ProgressCb

logger = logging.getLogger(__name__)

# Cost metadata (single source of truth; factory imports these).
COST_PER_SWAP_USD = 0.005
COLD_START_USD = 0.0

_MAX_SIDE = 1600          # down-scale longest edge before base64 (bandwidth)
_JPEG_QUALITY = 90
_DEFAULT_CONCURRENCY = 5  # conservative vs unknown Replicate account limit


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
        concurrency: int = _DEFAULT_CONCURRENCY,
    ) -> None:
        self._client = LucatacoClient(api_token, transport=transport)
        self._download_transport = download_transport
        self._sem = asyncio.Semaphore(concurrency)

    async def swap_batch(
        self,
        source: Path,
        targets: list[Path],
        *,
        progress_cb: ProgressCb | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> list[Path | None]:
        source_uri = _to_data_uri(source)
        results_dir = source.parent / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        total = len(targets)
        done = {"n": 0}
        done_lock = asyncio.Lock()

        async def _one(idx: int, target: Path) -> Path | None:
            if cancel_check and cancel_check():
                return None
            async with self._sem:
                if cancel_check and cancel_check():
                    return None
                try:
                    out_url = await self._client.swap(source_uri, _to_data_uri(target))
                except PredictionFailed as exc:
                    logger.warning("lucataco swap idx=%d failed: %s", idx, exc)
                    out_url = None
                except Exception as exc:  # noqa: BLE001
                    logger.warning("lucataco swap idx=%d error: %s", idx, exc)
                    out_url = None
                result: Path | None = None
                if out_url:
                    try:
                        result = await self._download(out_url, results_dir / f"{idx:03d}.jpg")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("lucataco download idx=%d failed: %s", idx, exc)
                        result = None
            async with done_lock:
                done["n"] += 1
                completed = done["n"]
            if progress_cb:
                progress_cb(
                    "swap_progress",
                    {"completed": completed, "total": total,
                     "index": idx, "ok": result is not None},
                )
            return result

        return await asyncio.gather(*[_one(i, t) for i, t in enumerate(targets)])

    async def _download(self, url: str, dest: Path) -> Path:
        async with httpx.AsyncClient(timeout=60.0, transport=self._download_transport) as c:
            resp = await c.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        return dest
