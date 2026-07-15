# -*- coding: utf-8 -*-
"""Async Replicate client for ComfyUI face-swap workflows.

Runs OUR ComfyUI face-swap graph (block_m2_face_swap/workflows/*.json) on the
managed ``comfyui/any-comfyui-workflow-a100`` model (A100 80GB) via the raw
Replicate REST API. Mirrors the submit/poll/retry pattern of
``replicate_video_client.ReplicateVideoClient`` so it slots into the same
provider conventions.

Step 1 of the RunPod->Replicate pivot: photo swap only. Video/occlusion later.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
from typing import Any

import httpx

from app.services.money_preflight import preflight_check

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.replicate.com/v1"
# A100 80GB variant — user wants RunPod-grade speed (~28s warm, ~$0.04/run).
# Community model: must use the version-based /v1/predictions endpoint
# (the /v1/models/{owner}/{name}/predictions endpoint is official-models only).
_FACESWAP_MODEL = "comfyui/any-comfyui-workflow-a100"
_FACESWAP_VERSION = "82c95ab88c3fe98772f23c42c8a6d54135a5d09546eefc75ee46805b017e8913"
_COST_FACESWAP = 0.04


class PredictionFailed(RuntimeError):
    """A prediction ran on Replicate but ended in failed/canceled status.

    This is a DETERMINISTIC, BILLABLE failure (the model executed) — callers
    must NOT retry it, or every retry re-runs the paid prediction.
    """


class FaceSwapProvider:
    """Async wrapper over Replicate for the ComfyUI face-swap workflow.

    Args:
        api_token: Replicate API token. Falls back to REPLICATE_API_TOKEN env var.

    Raises:
        RuntimeError: If no API token is available.
    """

    def __init__(self, api_token: str | None = None) -> None:
        self._token = (api_token or os.getenv("REPLICATE_API_TOKEN", "")).strip()
        if not self._token:
            raise RuntimeError("REPLICATE_API_TOKEN not set")
        self._headers = {
            "Authorization": f"Token {self._token}",
            "Content-Type": "application/json",
        }

    async def swap_photo(
        self,
        workflow_json: dict,
        input_zip: bytes,
    ) -> dict:
        """Run a ComfyUI face-swap graph and return the output image URL.

        Args:
            workflow_json: The ComfyUI workflow in API ("prompt") format. Its
                LoadImage nodes must reference filenames present in ``input_zip``
                (our face_swap_only.json uses "source.jpg" / "target.jpg").
            input_zip: Bytes of a zip archive containing the referenced images;
                any-comfyui-workflow extracts it into the ComfyUI input dir.

        Returns:
            {"image_url": str, "cost_usd": float}

        Raises:
            RuntimeError: If the prediction fails or all retries are exhausted.
            TimeoutError: If the prediction does not finish in time.
        """
        # cog-comfyui iterates the graph's top-level values as nodes and calls
        # .get() on each, so drop non-node keys (e.g. our "_comment" string).
        graph = {k: v for k, v in workflow_json.items() if isinstance(v, dict)}
        data_uri = "data:application/zip;base64," + base64.b64encode(input_zip).decode()
        payload = {
            "version": _FACESWAP_VERSION,
            "input": {
                "workflow_json": json.dumps(graph),
                "input_file": data_uri,
                "output_format": "png",
                "return_temp_files": False,
            },
        }
        output = await self._run_prediction(_FACESWAP_MODEL, payload)
        # any-comfyui-workflow returns a list of output file URLs (SaveImage results).
        image_url = output[0] if isinstance(output, (list, tuple)) else output
        if not image_url:
            raise RuntimeError(f"No output image in prediction result: {output!r}")
        return {"image_url": image_url, "cost_usd": _COST_FACESWAP}

    async def _run_prediction(
        self, model: str, payload: dict, max_retries: int = 5
    ) -> Any:
        """Submit a prediction and poll until completion, with backoff retry.

        Uses the version-based /v1/predictions endpoint (payload carries
        "version"), which works for community models like the A100 variant.
        """
        submit_url = f"{_BASE_URL}/predictions"
        last_err: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                pred_id = await self._submit(submit_url, payload)
                logger.debug("Faceswap prediction %s submitted (attempt %d)", pred_id, attempt)
                return await self._poll(pred_id)
            except PredictionFailed:
                # Billable, deterministic — re-running just wastes money. Fail now.
                raise
            except AssertionError:
                raise  # money-preflight: local payload bug, never retry
            except httpx.HTTPStatusError as exc:
                last_err = exc
                code = exc.response.status_code
                # Deterministic client errors (e.g. 404/422) won't fix on retry.
                if 400 <= code < 500 and code != 429:
                    raise RuntimeError(
                        f"Replicate rejected request for {model} ({code}): "
                        f"{exc.response.text[:300]}"
                    ) from exc
                logger.warning(
                    "Replicate attempt %d/%d for %s failed: %s",
                    attempt, max_retries, model, exc,
                )
                if attempt < max_retries:
                    if code == 429:
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
        preflight_check(url, payload, required_keys=("workflow_json", "input_file"))
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=payload, headers=self._headers)
            response.raise_for_status()
            data = response.json()
            pred_id = data.get("id")
            if not pred_id:
                raise RuntimeError(f"No prediction ID in Replicate response: {data}")
            return pred_id

    async def _poll(self, pred_id: str, max_wait: int = 600) -> Any:
        """Poll prediction status until succeeded/failed, returning the output."""
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
                    logger.debug("Faceswap prediction %s succeeded", pred_id)
                    return data.get("output")
                if status in ("failed", "canceled"):
                    raise PredictionFailed(
                        f"Prediction {pred_id} {status}: {data.get('error', 'unknown error')}"
                    )

                await asyncio.sleep(interval)
                waited += interval

        raise TimeoutError(f"Prediction {pred_id} timed out after {max_wait}s")
