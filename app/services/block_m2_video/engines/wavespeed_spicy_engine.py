# -*- coding: utf-8 -*-
"""WaveSpeed wan-2.6 spicy image-to-video engine (uncensored, managed).

Patient 429 backoff; WaveSpeedTransientError (429/network, no prediction created
-> retry/sweep) vs WaveSpeedEngineError (completed-but-failed / 4xx -> terminal,
billable, never retry). Local image sent as a base64 data-URI (no hosting).
Never logs the API key. Cost/snap come from WAVESPEED_CAPS.
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

from .capabilities import WAVESPEED_CAPS
from .engine_protocol import VideoRequest, VideoResult, new_generation_id
from .errors import TerminalVideoError, TransientVideoError

logger = logging.getLogger(__name__)

_BASE = "https://api.wavespeed.ai"
_MODEL = "alibaba/wan-2.6/image-to-video-spicy"
_SUBMIT = f"{_BASE}/api/v3/{_MODEL}"


class WaveSpeedEngineError(TerminalVideoError):
    """Terminal failure: prediction completed-but-failed, or a 4xx."""


class WaveSpeedTransientError(TransientVideoError):
    """429/network retries exhausted; NO prediction created -> safe to retry."""


class WaveSpeedSpicyEngine:
    engine_name = "wavespeed_spicy"

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
        self._headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        self._transport = transport
        self._dl_transport = download_transport
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    async def is_available(self) -> bool:
        return bool(self._key)

    async def generate(self, request: VideoRequest) -> VideoResult:
        start = time.monotonic()
        gen_id = request.generation_id or new_generation_id()
        seconds = WAVESPEED_CAPS.snap_duration(request.seconds)
        resolution = WAVESPEED_CAPS.snap_resolution(request.resolution)
        if not request.input_image_path.exists():
            raise WaveSpeedEngineError(f"input image not found: {request.input_image_path}")

        payload = {
            "image": self._data_uri(request.input_image_path),
            "prompt": request.prompt,
            "duration": seconds,
            "resolution": resolution,
            "negative_prompt": request.negative_prompt or "",
            "enable_prompt_expansion": request.enable_prompt_expansion,
        }
        if request.shot_type:
            payload["shot_type"] = request.shot_type
        if request.seed is not None:
            payload["seed"] = request.seed

        # Diagnostic: log EXACTLY what goes to WaveSpeed (no key, no image bytes).
        logger.info(
            "WaveSpeed submit: dur=%s res=%s expansion=%s shot=%s "
            "prompt(len=%d)=%r negative(len=%d)=%r",
            seconds, resolution, payload["enable_prompt_expansion"],
            payload.get("shot_type"), len(request.prompt or ""),
            (request.prompt or "")[:400], len(request.negative_prompt or ""),
            (request.negative_prompt or "")[:200],
        )
        poll_url = await self._submit_with_retry(payload)
        video_url = await self._poll(poll_url)
        out_path = await self._download(video_url, request.persona_id, gen_id)

        return VideoResult(
            generation_id=gen_id, persona_id=request.persona_id, output_path=out_path,
            engine=self.engine_name, model=_MODEL,
            seed=request.seed if request.seed is not None else -1,
            cost_usd=WAVESPEED_CAPS.cost_for(seconds, resolution),
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
        last: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=120, transport=self._transport) as c:
                    r = await c.post(_SUBMIT, headers=self._headers, json=payload)
                if r.status_code == 429:
                    raise httpx.HTTPStatusError("429", request=r.request, response=r)
                if 400 <= r.status_code < 500:
                    raise WaveSpeedEngineError(f"WaveSpeed rejected ({r.status_code}): {r.text[:300]}")
                r.raise_for_status()
                d = r.json().get("data", r.json())
                pid = d.get("id")
                return (d.get("urls") or {}).get("get") or f"{_BASE}/api/v3/predictions/{pid}/result"
            except WaveSpeedEngineError:
                raise
            except httpx.HTTPStatusError as exc:
                last = exc
                code = exc.response.status_code if exc.response is not None else 0
                logger.warning("WaveSpeed submit %d/%d failed: %s", attempt, self._max_retries, exc)
                if attempt < self._max_retries:
                    wait = (10.0 + 2 ** attempt + random.uniform(0, 5)) if code == 429 else 2 ** attempt
                    await asyncio.sleep(wait * self._backoff_base)
            except Exception as exc:  # network/transport — retryable, not billed
                last = exc
                logger.warning("WaveSpeed submit %d/%d error: %s", attempt, self._max_retries, exc)
                if attempt < self._max_retries:
                    await asyncio.sleep((2 ** attempt) * self._backoff_base)
        raise WaveSpeedTransientError(f"WaveSpeed submit failed after {self._max_retries} retries: {last}")

    async def _poll(self, poll_url: str, max_wait: int = 600) -> str:
        deadline = time.monotonic() + max_wait
        interval = 6
        async with httpx.AsyncClient(timeout=60, transport=self._transport) as c:
            while time.monotonic() < deadline:
                try:
                    r = await c.get(poll_url, headers=self._headers)
                    if r.status_code == 429 or r.status_code >= 500:
                        # Transient poll hiccup: re-poll the SAME (already created,
                        # billable) prediction. Do NOT raise a transient error here —
                        # that would make the batch runner re-submit and double-bill.
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

    async def _download(self, url: str, persona_id: str, gen_id: str) -> Path:
        out_dir = Path("state/personas/videos") / persona_id / gen_id
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / "output.mp4"
        last: Exception | None = None
        for attempt in range(1, 4):  # re-GET is free, no re-bill
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
