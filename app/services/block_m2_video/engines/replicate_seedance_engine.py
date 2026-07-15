# -*- coding: utf-8 -*-
"""Seedance (bytedance/seedance-1-pro-fast) image-to-video — Replicate, SFW/censored.

Структурный близнец WaveSpeedSpicyEngine: submit→poll→download, transient/terminal
split (429/network = transient→retry/sweep; completed-failed / 4xx≠429 = terminal,
billable→never retry). Replicate Token-auth + base64 data-URI + browser UA
(анти-tarpit). Cost/snap из SEEDANCE_CAPS. Никогда не логирует токен.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.services.money_preflight import preflight_check

from .capabilities import SEEDANCE_CAPS
from .engine_protocol import VideoRequest, VideoResult, new_generation_id
from .errors import TerminalVideoError, TransientVideoError

logger = logging.getLogger(__name__)

_BASE = "https://api.replicate.com/v1"
_MODEL = "bytedance/seedance-1-pro-fast"
_SUBMIT = f"{_BASE}/models/{_MODEL}/predictions"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


class ReplicateSeedanceEngineError(TerminalVideoError):
    """Terminal: prediction failed/canceled, or 4xx (≠429). Billable, never retry."""


class ReplicateSeedanceTransientError(TransientVideoError):
    """429/network exhausted; no prediction succeeded -> safe to retry."""


class ReplicateSeedanceEngine:
    engine_name = "replicate_seedance"

    def __init__(
        self,
        api_token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        download_transport: httpx.BaseTransport | None = None,
        max_retries: int = 8,
        backoff_base: float = 1.0,
    ) -> None:
        self._token = (api_token or os.getenv("REPLICATE_API_TOKEN", "")).strip()
        if not self._token:
            raise ReplicateSeedanceEngineError("REPLICATE_API_TOKEN not set")
        self._headers = {
            "Authorization": f"Token {self._token}",
            "Content-Type": "application/json",
            "User-Agent": _UA,
        }
        self._transport = transport
        self._dl_transport = download_transport
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    async def is_available(self) -> bool:
        return bool(self._token)

    async def generate(self, request: VideoRequest) -> VideoResult:
        start = time.monotonic()
        gen_id = request.generation_id or new_generation_id()
        seconds = SEEDANCE_CAPS.snap_duration(request.seconds)
        resolution = SEEDANCE_CAPS.snap_resolution(request.resolution)
        if not request.input_image_path.exists():
            raise ReplicateSeedanceEngineError(f"input image not found: {request.input_image_path}")

        payload = {"input": {
            "image": self._data_uri(request.input_image_path),
            "prompt": request.prompt,
            "duration": seconds,
            "resolution": resolution,
        }}
        if request.seed is not None:
            payload["input"]["seed"] = request.seed

        preflight_check(_MODEL, payload, required_keys=("image", "prompt"))
        pred_id = await self._submit_with_retry(payload)
        video_url = await self._poll(pred_id)
        out_path = await self._download(video_url, request.persona_id, gen_id)

        return VideoResult(
            generation_id=gen_id, persona_id=request.persona_id, output_path=out_path,
            engine=self.engine_name, model=_MODEL,
            seed=request.seed if request.seed is not None else -1,
            cost_usd=SEEDANCE_CAPS.cost_for(seconds, resolution),
            duration_sec=time.monotonic() - start, timestamp=datetime.now(timezone.utc),
            prompt=request.prompt, seconds=seconds,
            extra={"video_url": video_url, "resolution": resolution},
        )

    @staticmethod
    def _data_uri(path: Path) -> str:
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        b64 = base64.b64encode(path.read_bytes()).decode()
        return f"data:{mime};base64,{b64}"

    async def _submit_with_retry(self, payload: dict) -> str:
        preflight_check(_SUBMIT, payload)
        last: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=120, transport=self._transport) as c:
                    r = await c.post(_SUBMIT, headers=self._headers, json=payload)
                if r.status_code == 429:
                    raise httpx.HTTPStatusError("429", request=r.request, response=r)
                if 400 <= r.status_code < 500:
                    raise ReplicateSeedanceEngineError(f"Replicate rejected ({r.status_code}): {r.text[:300]}")
                r.raise_for_status()
                pid = r.json().get("id")
                if not pid:
                    raise ReplicateSeedanceEngineError(f"no prediction id: {r.json()}")
                return pid
            except ReplicateSeedanceEngineError:
                raise
            except httpx.HTTPStatusError as exc:
                last = exc
                code = exc.response.status_code if exc.response is not None else 0
                logger.warning("seedance submit %d/%d failed: %s", attempt, self._max_retries, exc)
                if attempt < self._max_retries:
                    wait = (10.0 + 2 ** attempt + random.uniform(0, 5)) if code == 429 else 2 ** attempt
                    await asyncio.sleep(wait * self._backoff_base)
            except Exception as exc:  # noqa: BLE001 — network, retryable, not billed
                last = exc
                logger.warning("seedance submit %d/%d error: %s", attempt, self._max_retries, exc)
                if attempt < self._max_retries:
                    await asyncio.sleep((2 ** attempt) * self._backoff_base)
        raise ReplicateSeedanceTransientError(f"seedance submit failed after {self._max_retries}: {last}")

    async def _poll(self, pred_id: str, max_wait: int = 600) -> str:
        poll_url = f"{_BASE}/predictions/{pred_id}"
        deadline = time.monotonic() + max_wait
        interval = 5
        async with httpx.AsyncClient(timeout=60, transport=self._transport) as c:
            while time.monotonic() < deadline:
                try:
                    r = await c.get(poll_url, headers=self._headers)
                    if r.status_code == 429 or r.status_code >= 500:
                        logger.warning("seedance poll %d, re-polling", r.status_code)
                        await asyncio.sleep(interval * self._backoff_base)
                        continue
                    r.raise_for_status()
                    d = r.json()
                except ReplicateSeedanceEngineError:
                    raise
                except Exception as exc:  # noqa: BLE001 — re-poll, no re-bill
                    logger.warning("seedance poll error, re-polling: %s", exc)
                    await asyncio.sleep(interval * self._backoff_base)
                    continue
                status = d.get("status")
                if status == "succeeded":
                    out = d.get("output")
                    if isinstance(out, list):
                        out = out[0] if out else None
                    if not out:
                        raise ReplicateSeedanceEngineError(f"succeeded but no output: {d}")
                    return out
                if status in ("failed", "canceled"):
                    raise ReplicateSeedanceEngineError(f"prediction {status}: {d.get('error')}")
                await asyncio.sleep(interval * self._backoff_base)
        raise ReplicateSeedanceEngineError(f"poll timed out after {max_wait}s")

    async def _download(self, url: str, persona_id: str, gen_id: str) -> Path:
        out_dir = Path("state/personas/videos") / persona_id / gen_id
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / "output.mp4"
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
        raise ReplicateSeedanceEngineError(f"download failed: {last}")
