# -*- coding: utf-8 -*-
"""Async Replicate API client for video generation and LoRA training."""
from __future__ import annotations

import asyncio
import io
import logging
import os
import random
import zipfile
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.replicate.com/v1"
_KLING_MODEL = "kwaivgi/kling-v2.1"
_FLUX_TRAINER_MODEL = "ostris/flux-dev-lora-trainer"
_FLUX_LORA_MODEL = "black-forest-labs/flux-dev-lora"
_FLUX_PRO_MODEL = "black-forest-labs/flux-1.1-pro"

# Approximate cost estimates per operation
_COST_KLING_PER_5SEC = 0.10
_COST_FLUX_TRAINING = 5.00
_COST_FLUX_INFERENCE = 0.02
_COST_FLUX_PRO = 0.04

# Реализм LoRA-фото персоны: lora_scale=1.0 — свит-спот (live-подбор
# 2026-07-12 на 2 seed): узнаваемость персоны + фото-вид. На 0.8 LoRA
# недовешена (лицо уплывает к дженерик-FLUX), на 1.2 начинается
# переобработка (blush/сглаживание). Override через конфиг (env).
_FLUX_LORA_SCALE_DEFAULT = float(os.getenv("JARVIS_FLUX_LORA_SCALE", "1.0"))
_FLUX_LORA_GUIDANCE_DEFAULT = float(os.getenv("JARVIS_FLUX_LORA_GUIDANCE", "4.0"))


class ReplicateVideoClient:
    """Async wrapper over Replicate REST API for video and LoRA operations.

    Args:
        api_token: Replicate API token. Falls back to REPLICATE_API_TOKEN env var.

    Raises:
        RuntimeError: If no API token is available.
    """

    def __init__(self, api_token: str | None = None) -> None:
        self._token = (api_token or os.getenv("REPLICATE_API_TOKEN", "")).strip()
        if not self._token:
            raise RuntimeError("REPLICATE_API_TOKEN not set")
        self._api_token = self._token
        self._headers = {
            "Authorization": f"Token {self._token}",
            "Content-Type": "application/json",
        }

    def _auth_headers(self) -> dict:
        return self._headers

    async def generate_kling_v21(
        self,
        image_url: str,
        prompt: str,
        duration: int = 5,
    ) -> dict:
        """Generate a video from an image using Kling v2.1.

        Args:
            image_url: URL of the source image.
            prompt: Text description of desired motion/animation.
            duration: Video duration in seconds (default 5).

        Returns:
            {"video_url": str, "cost_usd": float, "duration_sec": int}
        """
        payload = {
            "input": {
                "image": image_url,
                "prompt": prompt,
                "duration": duration,
            }
        }
        output = await self._run_prediction(_KLING_MODEL, payload)
        video_url = output if isinstance(output, str) else (output[0] if output else "")
        cost = _COST_KLING_PER_5SEC * (duration / 5)
        logger.info("Kling v2.1 complete: url=%s cost=$%.4f", video_url, cost)
        return {"video_url": video_url, "cost_usd": cost, "duration_sec": duration}

    async def train_flux_lora(
        self,
        images: list[str],
        trigger_word: str,
        steps: int = 1000,
        destination: str | None = None,
    ) -> dict:
        """Train FLUX LoRA via Replicate trainings API.

        Args:
            images: List of image URLs to train on.
            trigger_word: Unique token to trigger this LoRA in prompts.
            steps: Training steps (default 1000).
            destination: Replicate model path "owner/model-name" to save weights.
                         If None, defaults to "{username}/{trigger_word}-lora".

        Returns:
            {"weights_url": str, "cost_usd": float}
        """
        if destination is None:
            username = await self._get_username()
            destination = f"{username}/{trigger_word.lower().replace('_', '-')}-lora"

        await self._ensure_destination_exists(destination)

        logger.info("Creating ZIP archive from %d images", len(images))
        zip_bytes = await self._create_zip_from_urls(images)
        logger.info("ZIP archive size: %.2f MB", len(zip_bytes) / 1024 / 1024)

        logger.info("Uploading ZIP to Replicate file storage...")
        zip_url = await self._upload_zip_to_replicate(zip_bytes)
        logger.info("ZIP uploaded: %s", zip_url)

        payload = {
            "destination": destination,
            "input": {
                "input_images": zip_url,
                "trigger_word": trigger_word,
                "steps": steps,
            },
        }

        logger.info("Submitting training to %s with destination=%s", _FLUX_TRAINER_MODEL, destination)
        training_id = await self._submit_training(_FLUX_TRAINER_MODEL, payload)
        logger.info("Training submitted: id=%s, polling...", training_id)

        output = await self._poll_training(training_id)
        weights_url = output.get("weights") or output.get("version")

        if not weights_url:
            raise RuntimeError(f"Training succeeded but no weights URL in output: {output}")

        logger.info("Training complete: weights=%s", weights_url)
        return {"weights_url": weights_url, "cost_usd": _COST_FLUX_TRAINING}

    async def generate_flux_with_lora(
        self,
        prompt: str,
        lora_url: str,
        trigger_word: str,
        lora_scale: float | None = None,
        guidance: float | None = None,
    ) -> dict:
        """Generate an image with Flux using a trained LoRA.

        Args:
            prompt: Generation prompt (trigger_word is automatically prepended).
            lora_url: URL to the LoRA weights file.
            trigger_word: LoRA activation token.
            lora_scale: LoRA strength (default ``_FLUX_LORA_SCALE_DEFAULT`` —
                too high pulls the result toward digital-painting).
            guidance: Prompt guidance strength (default ``_FLUX_LORA_GUIDANCE_DEFAULT``).

        Returns:
            {"image_url": str, "cost_usd": float}
        """
        payload = {
            "input": {
                "prompt": f"{trigger_word} {prompt}",
                # ключ ``lora_weights`` — саме він у схемі flux-dev-lora; ключ
                # ``lora`` у схемі відсутній і мовчки дропався → LoRA не вантажилась.
                "lora_weights": lora_url,
                "lora_scale": lora_scale if lora_scale is not None else _FLUX_LORA_SCALE_DEFAULT,
                "guidance": guidance if guidance is not None else _FLUX_LORA_GUIDANCE_DEFAULT,
            }
        }
        output = await self._run_prediction(_FLUX_LORA_MODEL, payload)
        if isinstance(output, list) and output:
            image_url = output[0]
        elif isinstance(output, str):
            image_url = output
        else:
            image_url = ""
        cost = _COST_FLUX_INFERENCE
        logger.info("Flux+LoRA complete: url=%s cost=$%.4f", image_url, cost)
        return {"image_url": image_url, "cost_usd": cost}

    async def generate_flux_pro(
        self,
        prompt: str,
        width: int = 1024,
        height: int = 1024,
    ) -> dict:
        """Generate an image with Flux 1.1 Pro.

        Args:
            prompt: Text prompt for image generation.
            width: Output image width in pixels (default 1024).
            height: Output image height in pixels (default 1024).

        Returns:
            {"image_url": str, "cost_usd": float}
        """
        payload = {
            "input": {
                "prompt": prompt,
                "width": width,
                "height": height,
            }
        }
        output = await self._run_prediction(_FLUX_PRO_MODEL, payload)
        if isinstance(output, list) and output:
            image_url = output[0]
        elif isinstance(output, str):
            image_url = output
        else:
            image_url = ""
        logger.info("Flux 1.1 Pro complete: url=%s cost=$%.4f", image_url, _COST_FLUX_PRO)
        return {"image_url": image_url, "cost_usd": _COST_FLUX_PRO}

    async def _get_latest_version(self, model: str) -> str:
        """Fetch the latest version hash of a Replicate model."""
        url = f"https://api.replicate.com/v1/models/{model}"
        async with httpx.AsyncClient(timeout=30.0) as session:
            resp = await session.get(url, headers=self._auth_headers())
            resp.raise_for_status()
            return resp.json()["latest_version"]["id"]

    async def _submit_training(self, model: str, payload: dict) -> str:
        """Submit a training job. Returns training_id."""
        version = await self._get_latest_version(model)
        url = f"https://api.replicate.com/v1/models/{model}/versions/{version}/trainings"
        async with httpx.AsyncClient(timeout=60.0) as session:
            resp = await session.post(url, json=payload, headers=self._auth_headers())
            resp.raise_for_status()
            return resp.json()["id"]

    async def _create_zip_from_urls(self, image_urls: list[str]) -> bytes:
        """Download images and pack them into a ZIP archive (in memory)."""
        zip_buffer = io.BytesIO()
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as session:
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_STORED) as zf:
                for idx, url in enumerate(image_urls, 1):
                    logger.debug("Downloading image %d/%d for ZIP", idx, len(image_urls))
                    resp = await session.get(url)
                    resp.raise_for_status()
                    ext = ".webp" if "webp" in url else ".jpg"
                    zf.writestr(f"image_{idx:03d}{ext}", resp.content)
        zip_buffer.seek(0)
        return zip_buffer.read()

    async def _upload_zip_to_replicate(self, zip_bytes: bytes) -> str:
        """Upload ZIP to Replicate's file storage. Returns serving URL."""
        create_url = "https://api.replicate.com/v1/files"
        async with httpx.AsyncClient(timeout=60.0) as session:
            files = {"content": ("training_images.zip", zip_bytes, "application/zip")}
            resp = await session.post(
                create_url,
                files=files,
                headers={"Authorization": f"Token {self._api_token}"},
            )
            resp.raise_for_status()
            return resp.json()["urls"]["get"]

    async def _poll_training(self, training_id: str, max_wait_sec: int = 1800) -> dict:
        """Poll training until succeeded/failed/canceled. Default max wait 30 min."""
        url = f"https://api.replicate.com/v1/trainings/{training_id}"
        elapsed = 0
        while elapsed < max_wait_sec:
            async with httpx.AsyncClient(timeout=30.0) as session:
                resp = await session.get(url, headers=self._auth_headers())
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status")
                if status == "succeeded":
                    return data.get("output", {})
                if status in ("failed", "canceled"):
                    raise RuntimeError(f"Training {status}: {data.get('error')}")
            await asyncio.sleep(30)
            elapsed += 30
        raise RuntimeError(f"Training {training_id} timeout after {max_wait_sec}s")

    async def _get_username(self) -> str:
        """Fetch authenticated user's username from /v1/account."""
        url = "https://api.replicate.com/v1/account"
        async with httpx.AsyncClient(timeout=30.0) as session:
            resp = await session.get(url, headers=self._auth_headers())
            resp.raise_for_status()
            return resp.json()["username"]

    async def _ensure_destination_exists(self, destination: str) -> None:
        """Create the destination model on Replicate if it doesn't exist."""
        owner, name = destination.split("/", 1)
        url = f"https://api.replicate.com/v1/models/{owner}/{name}"
        async with httpx.AsyncClient(timeout=30.0) as session:
            resp = await session.get(url, headers=self._auth_headers())
            if resp.status_code == 404:
                create_payload = {
                    "owner": owner,
                    "name": name,
                    "visibility": "private",
                    "hardware": "cpu",
                }
                create_resp = await session.post(
                    "https://api.replicate.com/v1/models",
                    json=create_payload,
                    headers=self._auth_headers(),
                )
                create_resp.raise_for_status()
                logger.info("Created destination model: %s", destination)
            else:
                resp.raise_for_status()

    async def _run_prediction(
        self, model: str, payload: dict, max_retries: int = 5
    ) -> Any:
        """Submit a prediction and poll until completion, with exponential backoff retry.

        429 rate-limit responses use an aggressive backoff (minimum 10 s + jitter,
        or the Retry-After header value if present).  All other errors use the
        standard 2^attempt backoff.

        Args:
            model: Model identifier in "owner/name" format.
            payload: Prediction input payload.
            max_retries: Number of attempts before giving up (default 5).

        Returns:
            Model output (str, list, or dict depending on model).

        Raises:
            RuntimeError: If all retries are exhausted.
        """
        owner, name = model.split("/", 1)
        submit_url = f"{_BASE_URL}/models/{owner}/{name}/predictions"
        last_err: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                pred_id = await self._submit(submit_url, payload)
                logger.debug("Prediction %s submitted (attempt %d)", pred_id, attempt)
                return await self._poll(pred_id)
            except httpx.HTTPStatusError as exc:
                last_err = exc
                logger.warning(
                    "Replicate attempt %d/%d for %s failed: %s",
                    attempt, max_retries, model, exc,
                )
                if attempt < max_retries:
                    if exc.response.status_code == 429:
                        retry_after = exc.response.headers.get("Retry-After")
                        if retry_after:
                            wait = float(retry_after) + random.uniform(0, 2)
                        else:
                            wait = 10.0 + (2 ** attempt) + random.uniform(0, 5)
                        logger.warning("Rate limited (429), backing off %.1fs", wait)
                        await asyncio.sleep(wait)
                    else:
                        await asyncio.sleep(2 ** attempt)
            except Exception as exc:
                last_err = exc
                logger.warning(
                    "Replicate attempt %d/%d for %s failed: %s",
                    attempt, max_retries, model, exc,
                )
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)

        raise RuntimeError(
            f"Replicate API failed after {max_retries} retries for {model}: {last_err}"
        ) from last_err

    async def _submit(self, url: str, payload: dict) -> str:
        """POST prediction request and return the prediction ID."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=self._headers)
            response.raise_for_status()
            data = response.json()
            pred_id = data.get("id")
            if not pred_id:
                raise RuntimeError(f"No prediction ID in Replicate response: {data}")
            return pred_id

    async def _poll(self, pred_id: str, max_wait: int = 600) -> Any:
        """Poll prediction status until succeeded or failed.

        Args:
            pred_id: Replicate prediction ID.
            max_wait: Maximum seconds to wait before raising TimeoutError.

        Returns:
            The model output value.

        Raises:
            RuntimeError: If prediction fails or is canceled.
            TimeoutError: If max_wait seconds elapse without completion.
        """
        poll_url = f"{_BASE_URL}/predictions/{pred_id}"
        waited = 0
        interval = 5

        async with httpx.AsyncClient(timeout=30.0) as client:
            while waited < max_wait:
                response = await client.get(poll_url, headers=self._headers)
                response.raise_for_status()
                data = response.json()
                status = data.get("status")

                if status == "succeeded":
                    logger.debug("Prediction %s succeeded", pred_id)
                    return data.get("output")
                if status in ("failed", "canceled"):
                    raise RuntimeError(
                        f"Prediction {pred_id} {status}: {data.get('error', 'unknown error')}"
                    )

                await asyncio.sleep(interval)
                waited += interval

        raise TimeoutError(f"Prediction {pred_id} timed out after {max_wait}s")
