# -*- coding: utf-8 -*-
"""Shared WaveSpeed REST mechanics (submit -> poll -> download).

Extracted verbatim from the wan-2.6 spicy image-to-video engine so the RIFE
interpolation client can reuse the SAME money-safe retry/poll/download logic
without duplication. Both engines subclass :class:`WaveSpeedHTTPClient`.

Money-safety contract (do not weaken):
  * ``WaveSpeedTransientError`` (429 / network, NO prediction created) -> safe to
    retry / sweep.
  * ``WaveSpeedEngineError`` (completed-but-failed, or a 4xx rejection) ->
    terminal & billable, NEVER retry.
  * A poll hiccup (429/5xx/network while polling) re-polls the SAME already-
    created prediction; it never re-submits, so a prediction is never billed
    twice.
The API key is read from ``WAVESPEED_API_KEY`` and is NEVER logged.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from pathlib import Path

import httpx

from .errors import TerminalVideoError, TransientVideoError

logger = logging.getLogger(__name__)

BASE_URL = "https://api.wavespeed.ai"


class WaveSpeedEngineError(TerminalVideoError):
    """Terminal failure: prediction completed-but-failed, or a 4xx."""


class WaveSpeedTransientError(TransientVideoError):
    """429/network retries exhausted; NO prediction created -> safe to retry."""


class WaveSpeedHTTPClient:
    """Auth + submit/poll/download shared by all WaveSpeed v3 engines."""

    def __init__(
        self,
        api_key: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        download_transport: httpx.BaseTransport | None = None,
        max_retries: int = 8,
        backoff_base: float = 1.0,   # tests set 0.0 to skip sleeps
    ) -> None:
        self._key = (api_key or os.getenv("WAVESPEED_API_KEY", "")).strip()
        if not self._key:
            raise WaveSpeedEngineError("WAVESPEED_API_KEY not set")
        self._headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }
        self._transport = transport
        self._dl_transport = download_transport
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    async def _submit_with_retry(self, submit_url: str, payload: dict) -> str:
        """POST the job; return its poll URL. 429/network retried; 4xx terminal."""
        last: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=120, transport=self._transport) as c:
                    r = await c.post(submit_url, headers=self._headers, json=payload)
                if r.status_code == 429:
                    raise httpx.HTTPStatusError("429", request=r.request, response=r)
                if 400 <= r.status_code < 500:
                    raise WaveSpeedEngineError(
                        f"WaveSpeed rejected ({r.status_code}): {r.text[:300]}")
                r.raise_for_status()
                d = r.json().get("data", r.json())
                pid = d.get("id")
                return (d.get("urls") or {}).get("get") or \
                    f"{BASE_URL}/api/v3/predictions/{pid}/result"
            except WaveSpeedEngineError:
                raise
            except httpx.HTTPStatusError as exc:
                last = exc
                code = exc.response.status_code if exc.response is not None else 0
                logger.warning("WaveSpeed submit %d/%d failed: %s",
                               attempt, self._max_retries, exc)
                if attempt < self._max_retries:
                    wait = (10.0 + 2 ** attempt + random.uniform(0, 5)) if code == 429 else 2 ** attempt
                    await asyncio.sleep(wait * self._backoff_base)
            except Exception as exc:  # network/transport — retryable, not billed
                last = exc
                logger.warning("WaveSpeed submit %d/%d error: %s",
                               attempt, self._max_retries, exc)
                if attempt < self._max_retries:
                    await asyncio.sleep((2 ** attempt) * self._backoff_base)
        raise WaveSpeedTransientError(
            f"WaveSpeed submit failed after {self._max_retries} retries: {last}")

    async def _poll(self, poll_url: str, max_wait: int = 600) -> str:
        """Poll until completed; return the first output URL. Re-polls on hiccups."""
        deadline = time.monotonic() + max_wait
        interval = 6
        async with httpx.AsyncClient(timeout=60, transport=self._transport) as c:
            while time.monotonic() < deadline:
                try:
                    r = await c.get(poll_url, headers=self._headers)
                    if r.status_code == 429 or r.status_code >= 500:
                        # Transient poll hiccup: re-poll the SAME (already created,
                        # billable) prediction. Do NOT raise transient here — that
                        # would make a batch runner re-submit and double-bill.
                        logger.warning("WaveSpeed poll %d, re-polling", r.status_code)
                        await asyncio.sleep(interval * self._backoff_base)
                        continue
                    r.raise_for_status()
                    d = r.json().get("data", r.json())
                except WaveSpeedEngineError:
                    raise
                except Exception as exc:  # network blip while polling — re-poll, no re-bill
                    logger.warning("WaveSpeed poll error, re-polling: %s", exc)
                    await asyncio.sleep(interval * self._backoff_base)
                    continue
                status = d.get("status")
                if status in ("completed", "succeeded"):
                    outs = d.get("outputs") or d.get("output") or []
                    if isinstance(outs, str):
                        outs = [outs]
                    if not outs:
                        raise WaveSpeedEngineError(f"completed but no outputs: {d}")
                    return outs[0]
                if status in ("failed", "error", "canceled"):
                    raise WaveSpeedEngineError(f"prediction {status}: {d.get('error')}")
                await asyncio.sleep(interval * self._backoff_base)
        raise WaveSpeedEngineError(f"poll timed out after {max_wait}s")

    async def _download(self, url: str, dest: Path) -> Path:
        """GET the output URL into ``dest`` (re-GET is free, no re-bill)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        last: Exception | None = None
        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(timeout=300, transport=self._dl_transport) as c:
                    r = await c.get(url)
                    r.raise_for_status()
                    dest.write_bytes(r.content)
                return dest
            except Exception as exc:  # noqa: BLE001
                last = exc
                await asyncio.sleep((2 ** attempt) * self._backoff_base)
        raise WaveSpeedEngineError(f"download failed: {last}")
