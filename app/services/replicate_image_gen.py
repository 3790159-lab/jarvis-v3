"""Replicate FLUX 1.1 Pro image generation service."""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import List

from app.services.money_preflight import preflight_check

# Correct endpoint: /v1/models/<owner>/<name>/predictions — no "version" hash needed
_FLUX_MODEL_URL = "https://api.replicate.com/v1/models/black-forest-labs/flux-1.1-pro/predictions"
_PREDICTIONS_BASE = "https://api.replicate.com/v1/predictions"

_MODELS = {
    "people": "https://api.replicate.com/v1/models/black-forest-labs/flux-1.1-pro-ultra/predictions",
    "landscape": "https://api.replicate.com/v1/models/black-forest-labs/flux-1.1-pro-ultra/predictions",
    "default": "https://api.replicate.com/v1/models/black-forest-labs/flux-1.1-pro/predictions",
}

PEOPLE_KEYWORDS = [
    # Russian stems cover all case forms (девушка/девушки/девушку etc.)
    "девушк", "женщин", "парн", "мужчин", "человек",
    "ребёнк", "ребенк", "портрет", "лиц",
    "girl", "woman", "man", "guy", "person", "people",
    "child", "portrait", "face",
]

LANDSCAPE_KEYWORDS = [
    # Russian stems cover all case forms
    "пейзаж", "природ", "гор", "мор", "океан", "лес",
    "город", "улиц", "архитектур", "здани",
    "landscape", "nature", "mountains", "ocean", "forest",
    "city", "street", "architecture", "building",
]

logger = logging.getLogger(__name__)


def _post_with_retry(
    url: str,
    payload: dict,
    api_key: str,
    max_retries: int = 3,
) -> dict:
    """POST to Replicate API with automatic retry on HTTP 429."""
    preflight_check(url, payload, required_keys=("prompt",))
    for attempt in range(max_retries):
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
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                body = ""
                try:
                    body = e.read().decode(errors="replace")
                except Exception:
                    pass
                retry_after = 10
                try:
                    err_data = json.loads(body)
                    retry_after = int(err_data.get("retry_after", 10))
                except Exception:
                    pass
                if attempt < max_retries - 1:
                    logger.warning(
                        "Replicate rate limit (429), retrying in %ds (attempt %d/%d)",
                        retry_after, attempt + 1, max_retries,
                    )
                    time.sleep(retry_after + 1)
                    continue
                raise RuntimeError(
                    "Replicate rate limit достигнут. "
                    "Похоже, у тебя меньше $5 кредита — "
                    "добавь баланс на https://replicate.com/account/billing "
                    "чтобы вернуть нормальный лимит."
                ) from e
            raise
    raise RuntimeError("Replicate: все попытки исчерпаны")


def _select_model(prompt: str) -> str:
    """Choose best Replicate model based on prompt content."""
    p = prompt.lower()
    if any(k in p for k in PEOPLE_KEYWORDS):
        return _MODELS["people"]
    if any(k in p for k in LANDSCAPE_KEYWORDS):
        return _MODELS["landscape"]
    return _MODELS["default"]


# Марки заборони фотореалістичних людей у ``visual_style`` (nopersona-шлях
# мержить topic+visual_style в один текст ДО виклику _enhance_prompt, див.
# ``_ig_gen_photo_prompt`` — тому перевіряємо ЦІЛИЙ вхідний prompt: сцена
# (visual_style) розведена з темою поста саме тут, а не окремим полем).
_NO_PHOTOREALISTIC_PEOPLE_MARKERS = [
    "без фотореалістичних людей",
    "без фотореалистичных людей",
    "no photorealistic people",
    "without photorealistic people",
]


def _forbids_photorealistic_people(prompt: str) -> bool:
    p = prompt.lower()
    return any(marker in p for marker in _NO_PHOTOREALISTIC_PEOPLE_MARKERS)


def _enhance_prompt(prompt: str) -> str:
    """Enhance user prompt for photorealistic results."""
    if _forbids_photorealistic_people(prompt):
        return prompt

    p = prompt.lower()
    is_people = any(k in p for k in PEOPLE_KEYWORDS)

    # Тільки ПОЗИТИВНІ фото-якорі. Інлайн-негативи ("no anime/illustration/cgi")
    # прибрано: FLUX не має negative_prompt, а згадка терміна у позитивному
    # промпті ПРИЗИВАЄ його (той самий бекфайр, що доведено на persona-шляху
    # 2026-07-12: з негативами → аніме+watermark, без → фото).
    if is_people:
        return (
            f"{prompt}, "
            "photorealistic portrait photography, "
            "professional photoshoot, natural lighting, "
            "shallow depth of field, sharp focus on subject, "
            "real human, hyperrealistic skin texture, "
            "shot on Canon EOS R5, 85mm f/1.4 lens, "
            "DSLR quality, magazine cover quality, 8k resolution"
        )

    return (
        f"{prompt}, "
        "photorealistic photography, professional camera, "
        "natural lighting, sharp focus, high detail, "
        "shot on Canon EOS R5, 50mm lens, f/1.8, "
        "DSLR quality, hyperrealistic, "
        "real photo, 4k resolution, professional photo"
    )


def generate_images_replicate(
    prompt: str,
    num_images: int = 1,
    aspect_ratio: str = "9:16",
    style: str = "realistic",
) -> List[str]:
    """Generate images via Replicate FLUX 1.1 Pro. Returns list of image URLs."""
    api_key = os.getenv("REPLICATE_API_KEY", "").strip()
    if not api_key:
        raise ValueError("REPLICATE_API_KEY not set")

    enhanced = _enhance_prompt(prompt)
    logger.info("Replicate: submitting %d image(s), prompt=%r", num_images, enhanced[:80])

    urls: List[str] = []
    for i in range(max(1, num_images)):
        model_url = _select_model(prompt)
        payload_dict = {
            "input": {
                "prompt": enhanced,
                "aspect_ratio": aspect_ratio,
                "output_format": "jpg",
                "output_quality": 90,
                "safety_tolerance": 2,
            },
        }
        try:
            prediction = _post_with_retry(model_url, payload_dict, api_key)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode(errors="replace")[:500]
            except Exception:
                pass
            raise RuntimeError(f"Replicate submit failed HTTP {e.code}: {body}") from e

        prediction_id = prediction.get("id")
        if not prediction_id:
            raise RuntimeError(f"Replicate returned no prediction id: {prediction}")

        logger.info("Replicate: prediction %s submitted (image %d/%d)", prediction_id, i + 1, num_images)
        url = _poll_prediction(prediction_id, api_key)
        urls.append(url)

    return urls


def _poll_prediction(prediction_id: str, api_key: str, max_wait: int = 60) -> str:
    """Poll until prediction succeeds; raise on failure or timeout."""
    for _ in range(max_wait):
        time.sleep(1)
        status_req = urllib.request.Request(
            f"{_PREDICTIONS_BASE}/{prediction_id}",
            headers={"Authorization": f"Token {api_key}"},
        )
        with urllib.request.urlopen(status_req, timeout=10) as resp:
            status = json.loads(resp.read())

        state = status.get("status", "")
        if state == "succeeded":
            output = status.get("output")
            if isinstance(output, list):
                return output[0]
            return output
        if state in ("failed", "canceled"):
            raise RuntimeError(f"Replicate prediction {prediction_id} {state}: {status.get('error')}")

    raise TimeoutError(f"Replicate prediction {prediction_id} timed out after {max_wait}s")


def is_replicate_configured() -> bool:
    return bool(os.getenv("REPLICATE_API_KEY", "").strip())
