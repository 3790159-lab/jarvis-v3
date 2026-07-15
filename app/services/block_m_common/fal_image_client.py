# -*- coding: utf-8 -*-
"""Async fal.ai client for FLUX.2 LoRA inference (persona photos).

Отдельный провайдер от Replicate (``ReplicateVideoClient``): персона Веры
мигрирована на FLUX.2 (`fal-ai/flux-2/lora`), нативный de-wax которого
выигрывает у draft-1000+magic-refiner (spike 2026-07-14, self-consistency
v2=0.733 на 2000 шагах + per-image captions). ⚠️ magic-refiner на FLUX.2
ЗАПРЕЩЁН (убивает identity 0.52→0.31) — этот путь БЕЗ пост-ген рефайна.

Протокол fal-queue (submit→poll→result) поверх raw ``httpx`` — как
``ReplicateVideoClient`` (никакого fal-client SDK в зависимостях).
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Optional

import httpx

from app.services.money_preflight import preflight_check

_GEN_MODEL = "fal-ai/flux-2/lora"
_QUEUE_BASE = "https://queue.fal.run"
# FLUX.2 LoRA-инференс ~$0.02/кадр без рефайнера (spike 2026-07-14).
_COST_FLUX2 = 0.02


class FalImageClient:
    """Async-обёртка над fal-queue REST для FLUX.2 LoRA-генерации.

    Args:
        api_key: fal-ключ. Фолбэк на ``FAL_KEY`` env.
        transport: необязательный ``httpx`` transport (для тестов —
            ``httpx.MockTransport``); прод оставляет ``None``.

    Raises:
        RuntimeError: если ключ недоступен.
    """

    def __init__(
        self, api_key: Optional[str] = None, transport: Any = None
    ) -> None:
        self._key = (api_key or os.getenv("FAL_KEY", "")).strip()
        if not self._key:
            raise RuntimeError("FAL_KEY not set")
        self._transport = transport

    def _headers(self) -> dict:
        return {"Authorization": f"Key {self._key}", "Content-Type": "application/json"}

    async def generate_flux2_lora(
        self,
        prompt: str,
        lora_url: str,
        lora_scale: float = 1.15,
        guidance: float = 3.0,
        image_size: str = "portrait_4_3",
        steps: int = 28,
        seed: Optional[int] = None,
    ) -> dict:
        """Сгенерировать кадр FLUX.2 с персона-LoRA.

        Args:
            prompt: Полный промпт (trigger-токен уже вшит вызывающим кодом).
            lora_url: URL весов LoRA (``.safetensors`` на fal.media).
            lora_scale: Сила LoRA (1.15 — свит-спот прод-свипа, self-cos 0.782).
            guidance: guidance_scale.
            image_size: пресет размера fal (``portrait_4_3``).
            steps: num_inference_steps.
            seed: необязательный сид (детерминизм проб/свипов).

        Returns:
            ``{"image_url": str, "cost_usd": float}``.
        """
        payload: dict = {
            "prompt": prompt,
            "loras": [{"path": lora_url, "scale": lora_scale}],
            "image_size": image_size,
            "num_images": 1,
            "guidance_scale": guidance,
            "num_inference_steps": steps,
            "output_format": "jpeg",
            "enable_safety_checker": False,
        }
        if seed is not None:
            payload["seed"] = seed
        preflight_check(_GEN_MODEL, payload, required_keys=("prompt", "loras"))
        result = await self._submit_and_poll(_GEN_MODEL, payload)
        images = result.get("images") or []
        image_url = images[0].get("url", "") if images else ""
        return {"image_url": image_url, "cost_usd": _COST_FLUX2}

    async def _submit_and_poll(
        self,
        model: str,
        payload: dict,
        poll_every: float = 3.0,
        max_polls: int = 120,
    ) -> dict:
        """fal-queue: POST job → poll status → GET result. Сеть изолирована здесь
        (тесты мокают этот метод или подставляют ``MockTransport``)."""
        preflight_check(model, payload)
        headers = self._headers()
        async with httpx.AsyncClient(transport=self._transport, timeout=90) as client:
            sub_resp = await client.post(
                f"{_QUEUE_BASE}/{model}", headers=headers, json=payload
            )
            sub = sub_resp.json()
            if sub.get("status") not in ("IN_QUEUE", "IN_PROGRESS", "COMPLETED"):
                raise RuntimeError(
                    f"fal submit failed [{sub_resp.status_code}]: {str(sub)[:300]}"
                )
            status_url = sub["status_url"]
            resp_url = sub["response_url"]
            for _ in range(max_polls):
                st_resp = await client.get(status_url, headers=headers)
                st = st_resp.json().get("status")
                if st == "COMPLETED":
                    res = await client.get(resp_url, headers=headers)
                    return res.json()
                if st in ("FAILED", "CANCELLED", "ERROR"):
                    res = await client.get(resp_url, headers=headers)
                    raise RuntimeError(f"fal job {st}: {str(res.json())[:300]}")
                await asyncio.sleep(poll_every)
            raise TimeoutError(
                f"fal {model} did not finish in {poll_every * max_polls:.0f}s"
            )
