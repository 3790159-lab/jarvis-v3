"""Face Swap Engine — basic swap, polished swap, and face enhancement."""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict

logger = logging.getLogger(__name__)

_PREDICTIONS_BASE = "https://api.replicate.com/v1/predictions"
_FACE_SWAP_VERSION = "d1d6ea8c8be89d664a07a457526f7128109dee7030fdac424788d762c71ed111"  # cdingram/face-swap
_FACE_SWAP_REACTOR_VERSION = "278a81e7ebb22db98bcba54de985d22cc1abeead2754eb1f2af717247be69b34"  # codeplugtech/face-swap
_GFPGAN_URL = "https://api.replicate.com/v1/models/tencentarc/gfpgan/predictions"


def _get_api_key() -> str:
    key = os.getenv("REPLICATE_API_KEY", "").strip()
    if not key:
        raise ValueError("REPLICATE_API_KEY not set")
    return key


def _post_prediction(url: str, payload: Dict[str, Any], api_key: str) -> str:
    """Submit a prediction and return its ID."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            prediction = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode(errors="replace")[:500]
        except Exception:
            pass
        if exc.code == 404:
            raise RuntimeError(
                "Face swap модель временно недоступна. "
                "Попробуй /enhance вместо /faceswap "
                "или подожди когда модель обновится."
            ) from exc
        raise RuntimeError(f"Replicate submit failed HTTP {exc.code}: {body}") from exc

    pred_id = prediction.get("id")
    if not pred_id:
        raise RuntimeError(f"Replicate returned no prediction id: {prediction}")
    return pred_id


def poll_replicate(prediction_id: str, api_key: str, max_wait: int = 120) -> str:
    """Poll Replicate until prediction succeeds. Returns output URL."""
    for _ in range(max_wait):
        time.sleep(1)
        req = urllib.request.Request(
            f"{_PREDICTIONS_BASE}/{prediction_id}",
            headers={"Authorization": f"Token {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = json.loads(resp.read())
        state = status.get("status", "")
        if state == "succeeded":
            output = status.get("output")
            return output[0] if isinstance(output, list) else output
        if state in ("failed", "canceled"):
            raise RuntimeError(
                f"Replicate prediction {prediction_id} {state}: {status.get('error')}"
            )
    raise TimeoutError(f"Replicate prediction {prediction_id} timed out after {max_wait}s")


def face_swap_basic(source_image_url: str, target_image_url: str) -> str:
    """Quick face swap — put source face onto target image. Uses cdingram/face-swap."""
    api_key = _get_api_key()
    payload = {
        "version": _FACE_SWAP_VERSION,
        "input": {
            "swap_image": source_image_url,
            "input_image": target_image_url,
        },
    }
    pred_id = _post_prediction(_PREDICTIONS_BASE, payload, api_key)
    logger.info("Face swap basic: prediction %s submitted", pred_id)
    return poll_replicate(pred_id, api_key)


def face_swap_reactor(source_image_url: str, target_image_url: str) -> str:
    """Higher quality face swap using codeplugtech/face-swap."""
    api_key = _get_api_key()
    payload = {
        "version": _FACE_SWAP_REACTOR_VERSION,
        "input": {
            "swap_image": source_image_url,
            "input_image": target_image_url,
        },
    }
    pred_id = _post_prediction(_PREDICTIONS_BASE, payload, api_key)
    logger.info("Face swap reactor: prediction %s submitted", pred_id)
    return poll_replicate(pred_id, api_key)


def enhance_face(image_url: str, scale: int = 2) -> str:
    """Apply GFPGAN v1.4 to enhance face quality."""
    api_key = _get_api_key()
    payload = {
        "input": {
            "img": image_url,
            "version": "v1.4",
            "scale": scale,
        }
    }
    pred_id = _post_prediction(_GFPGAN_URL, payload, api_key)
    logger.info("GFPGAN enhance: prediction %s submitted", pred_id)
    return poll_replicate(pred_id, api_key)


def face_swap_with_polish(source_url: str, target_url: str) -> str:
    """Face swap + GFPGAN polish pipeline for highest quality."""
    swapped = face_swap_basic(source_url, target_url)
    polished = enhance_face(swapped)
    return polished


def estimate_swap_cost(polished: bool = False) -> float:
    """Return estimated cost for the swap pipeline."""
    basic = 0.005
    gfpgan = 0.002
    return round(basic + (gfpgan if polished else 0.0), 4)
