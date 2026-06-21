# -*- coding: utf-8 -*-
"""Async Replicate client for lucataco/faceswap (bare inswapper, uncensored).

Mirrors faceswap_client's submit/poll/backoff. Two lucataco-specific rules:
  * inputs are passed as data-URIs (verified accepted — no hosting needed);
  * a no-face run returns status=succeeded with output=None (BILLABLE) — we
    surface that as ``None`` (a per-target failure), never a retry.
httpx is unaffected by Replicate's Cloudflare urllib-UA ban, so no UA needed.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Any

import httpx

from app.services.block_m_common.faceswap_client import PredictionFailed

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.replicate.com/v1"
_LUCATACO_VERSION = "9a4298548422074c3f57258c5d544497314ae4112df80d116f0d2109e843d20d"


class LucatacoTransientError(RuntimeError):
    """Retries exhausted on a NON-billable failure (429/network); no prediction was created, so callers MAY retry later."""


class LucatacoClient:
    """One swap per ``swap()`` call. Construct once, reuse across a batch."""

    def __init__(
        self,
        api_token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token = (api_token or os.getenv("REPLICATE_API_TOKEN", "")).strip()
        if not self._token:
            raise RuntimeError("REPLICATE_API_TOKEN not set")
        self._headers = {
            "Authorization": f"Token {self._token}",
            "Content-Type": "application/json",
        }
        self._transport = transport  # tests inject httpx.MockTransport

    async def swap(self, swap_image: str, target_image: str) -> str | None:
        """Run one swap. ``swap_image``/``target_image`` are data-URIs or URLs.

        Returns the output URL, or ``None`` when lucataco found no face
        (succeeded+output=None). Raises PredictionFailed on failed/canceled.
        Raises LucatacoTransientError when retries exhausted on 429/network
        without a prediction ever succeeding (no billing occurred).
        """
        payload = {
            "version": _LUCATACO_VERSION,
            "input": {"swap_image": swap_image, "target_image": target_image},
        }
        return await self._run_prediction(payload)

    async def _run_prediction(self, payload: dict, max_retries: int = 8) -> Any:
        submit_url = f"{_BASE_URL}/predictions"
        last_err: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                pred_id = await self._submit(submit_url, payload)
                return await self._poll(pred_id)
            except PredictionFailed:
                raise  # billable, deterministic — never retry
            except httpx.HTTPStatusError as exc:
                last_err = exc
                code = exc.response.status_code
                if 400 <= code < 500 and code != 429:
                    raise RuntimeError(
                        f"Replicate rejected lucataco ({code}): "
                        f"{exc.response.text[:300]}"
                    ) from exc
                logger.warning("lucataco attempt %d/%d failed: %s", attempt, max_retries, exc)
                if attempt < max_retries:
                    if code == 429:
                        retry_after = exc.response.headers.get("Retry-After")
                        wait = (
                            float(retry_after) + random.uniform(0, 2)
                            if retry_after
                            else 10.0 + (2 ** attempt) + random.uniform(0, 5)
                        )
                    else:
                        wait = 2 ** attempt
                    await asyncio.sleep(wait)
            except Exception as exc:  # network/transport — retryable, not billed
                last_err = exc
                logger.warning("lucataco attempt %d/%d failed: %s", attempt, max_retries, exc)
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
        raise LucatacoTransientError(
            f"lucataco failed after {max_retries} retries: {last_err}"
        )

    async def _submit(self, url: str, payload: dict) -> str:
        async with httpx.AsyncClient(timeout=60.0, transport=self._transport) as c:
            resp = await c.post(url, json=payload, headers=self._headers)
            resp.raise_for_status()
            pred_id = resp.json().get("id")
            if not pred_id:
                raise RuntimeError(f"No prediction id in response: {resp.json()}")
            return pred_id

    async def _poll(self, pred_id: str, max_wait: int = 600) -> Any:
        poll_url = f"{_BASE_URL}/predictions/{pred_id}"
        waited, interval = 0, 3
        async with httpx.AsyncClient(timeout=30.0, transport=self._transport) as c:
            while waited < max_wait:
                resp = await c.get(poll_url, headers=self._headers)
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status")
                if status == "succeeded":
                    return data.get("output")  # may be None (no-face)
                if status in ("failed", "canceled"):
                    raise PredictionFailed(
                        f"lucataco {pred_id} {status}: {data.get('error')}"
                    )
                await asyncio.sleep(interval)
                waited += interval
        raise TimeoutError(f"lucataco {pred_id} timed out after {max_wait}s")
