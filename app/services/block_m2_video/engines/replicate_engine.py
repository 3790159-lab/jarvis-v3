# -*- coding: utf-8 -*-
"""Replicate wan i2v engine over raw ``httpx`` (no ``replicate`` SDK).

Structural twin of :class:`ReplicateSeedanceEngine`: submit → poll → download
against ``api.replicate.com`` with a transient/terminal error split for
money-safety (429/network = transient → retry; completed-failed / 4xx≠429 =
terminal, billable → never retry). Token-auth, base64 data-URI upload, browser
User-Agent (anti-tarpit). Never logs the token.

The SDK is deliberately NOT imported: it pulls pydantic-v1, which raises
``ConfigError`` under Python 3.14 and takes the whole animation path down with
it (every engine reaches through ``EngineRouter``, which imports this module).
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
from typing import Any

import httpx

from app.services.money_preflight import preflight_check

from .engine_protocol import VideoRequest, VideoResult, new_generation_id
from .errors import TerminalVideoError, TransientVideoError

logger = logging.getLogger(__name__)

_BASE = "https://api.replicate.com/v1"
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120 Safari/537.36"
)

# Per-second pricing (as of May 2026). Source: replicate.com/pricing.
# wan-2.2 is the default: recon flagged wan-2.5-i2v-fast as failing E002 on
# benign input (no captured log — recon-only), so 2.2 is the safer, reversible
# default. 2.5 stays selectable via ``model_id=...``.
REPLICATE_MODELS: list[dict[str, Any]] = [
    {"id": "wan-video/wan-2.2-i2v-fast", "cost_per_sec": 0.060},
    {"id": "wan-video/wan-2.5-i2v-fast", "cost_per_sec": 0.020},
]


class ReplicateEngineError(TerminalVideoError):
    """Terminal: prediction failed/canceled, 4xx (≠429), or bad input/output.

    Billable and/or deterministic — callers must NOT retry it.
    """


class ReplicateEngineTransientError(TransientVideoError):
    """429/network exhausted; no prediction succeeded → safe to retry."""


class ReplicateEngine:
    """Fast cloud wan i2v engine via the Replicate REST API."""

    engine_name = "replicate"

    def __init__(
        self,
        api_token: str | None = None,
        model_id: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        download_transport: httpx.BaseTransport | None = None,
        max_retries: int = 8,
        backoff_base: float = 1.0,
    ) -> None:
        self._token = (
            api_token
            or os.getenv("REPLICATE_API_TOKEN")
            or os.getenv("REPLICATE_API_KEY")
            or ""
        ).strip()
        if not self._token:
            raise ReplicateEngineError(
                "REPLICATE_API_TOKEN (or REPLICATE_API_KEY) is not set"
            )
        self._headers = {
            "Authorization": f"Token {self._token}",
            "Content-Type": "application/json",
            "User-Agent": _UA,
        }
        self.model_id = model_id or REPLICATE_MODELS[0]["id"]
        self.cost_per_sec = next(
            (m["cost_per_sec"] for m in REPLICATE_MODELS if m["id"] == self.model_id),
            0.04,
        )
        self._submit_url = f"{_BASE}/models/{self.model_id}/predictions"
        self._transport = transport
        self._dl_transport = download_transport
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    async def is_available(self) -> bool:
        return bool(self._token)

    async def generate(self, request: VideoRequest) -> VideoResult:
        start = time.monotonic()
        gen_id = request.generation_id or new_generation_id()
        seed = request.seed if request.seed is not None else int(time.time())

        if not request.input_image_path.exists():
            raise ReplicateEngineError(
                f"Input image not found: {request.input_image_path}"
            )

        logger.info(
            "ReplicateEngine: starting generation %s "
            "(persona=%s, model=%s, seconds=%d, seed=%d)",
            gen_id,
            request.persona_id,
            self.model_id,
            request.seconds,
            seed,
        )

        payload = {
            "input": {
                "image": self._data_uri(request.input_image_path),
                "prompt": request.prompt,
                "duration": request.seconds,
                "seed": seed,
            }
        }

        preflight_check(self.model_id, payload, required_keys=("image", "prompt"))
        pred_id = await self._submit_with_retry(payload)
        video_url = await self._poll(pred_id)
        out_path = await self._download(video_url, request.persona_id, gen_id)

        duration = time.monotonic() - start
        cost = self.cost_per_sec * request.seconds
        logger.info(
            "ReplicateEngine: generation %s done in %.1fs, cost=$%.3f",
            gen_id,
            duration,
            cost,
        )

        return VideoResult(
            generation_id=gen_id,
            persona_id=request.persona_id,
            output_path=out_path,
            engine=self.engine_name,
            model=self.model_id,
            seed=seed,
            cost_usd=cost,
            duration_sec=duration,
            timestamp=datetime.now(timezone.utc),
            prompt=request.prompt,
            seconds=request.seconds,
            extra={"video_url": video_url},
        )

    @staticmethod
    def _data_uri(path: Path) -> str:
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        b64 = base64.b64encode(path.read_bytes()).decode()
        return f"data:{mime};base64,{b64}"

    async def _submit_with_retry(self, payload: dict) -> str:
        preflight_check(self._submit_url, payload)
        last: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=120, transport=self._transport) as c:
                    r = await c.post(self._submit_url, headers=self._headers, json=payload)
                if r.status_code == 429:
                    raise httpx.HTTPStatusError("429", request=r.request, response=r)
                if 400 <= r.status_code < 500:
                    raise ReplicateEngineError(
                        f"Replicate rejected ({r.status_code}): {r.text[:300]}"
                    )
                r.raise_for_status()
                pid = r.json().get("id")
                if not pid:
                    raise ReplicateEngineError(f"no prediction id: {r.json()}")
                return pid
            except ReplicateEngineError:
                raise
            except httpx.HTTPStatusError as exc:
                last = exc
                code = exc.response.status_code if exc.response is not None else 0
                logger.warning(
                    "replicate submit %d/%d failed: %s", attempt, self._max_retries, exc
                )
                if attempt < self._max_retries:
                    wait = (
                        10.0 + 2 ** attempt + random.uniform(0, 5)
                        if code == 429
                        else 2 ** attempt
                    )
                    await asyncio.sleep(wait * self._backoff_base)
            except Exception as exc:  # noqa: BLE001 — network, retryable, not billed
                last = exc
                logger.warning(
                    "replicate submit %d/%d error: %s", attempt, self._max_retries, exc
                )
                if attempt < self._max_retries:
                    await asyncio.sleep((2 ** attempt) * self._backoff_base)
        raise ReplicateEngineTransientError(
            f"replicate submit failed after {self._max_retries}: {last}"
        )

    async def _poll(self, pred_id: str, max_wait: int = 600) -> str:
        poll_url = f"{_BASE}/predictions/{pred_id}"
        deadline = time.monotonic() + max_wait
        interval = 5
        async with httpx.AsyncClient(timeout=60, transport=self._transport) as c:
            while time.monotonic() < deadline:
                try:
                    r = await c.get(poll_url, headers=self._headers)
                    if r.status_code == 429 or r.status_code >= 500:
                        logger.warning("replicate poll %d, re-polling", r.status_code)
                        await asyncio.sleep(interval * self._backoff_base)
                        continue
                    r.raise_for_status()
                    d = r.json()
                except ReplicateEngineError:
                    raise
                except Exception as exc:  # noqa: BLE001 — re-poll, no re-bill
                    logger.warning("replicate poll error, re-polling: %s", exc)
                    await asyncio.sleep(interval * self._backoff_base)
                    continue
                status = d.get("status")
                if status == "succeeded":
                    return self._extract_url(d.get("output"))
                if status in ("failed", "canceled"):
                    raise ReplicateEngineError(
                        f"prediction {status}: {d.get('error', 'unknown error')}"
                    )
                await asyncio.sleep(interval * self._backoff_base)
        raise ReplicateEngineError(f"poll timed out after {max_wait}s")

    @staticmethod
    def _extract_url(output: Any) -> str:
        """Normalize a raw-JSON prediction output into a URL string.

        Replicate returns a bare URL string or a list of URL strings.
        """
        if isinstance(output, str) and output:
            return output
        if isinstance(output, list) and output and isinstance(output[0], str):
            return output[0]
        raise ReplicateEngineError(f"unexpected Replicate output: {output!r}")

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
        raise ReplicateEngineError(f"download failed: {last}")
