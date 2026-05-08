"""LoRA Manager — training, status tracking, and generation with custom models."""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_LORAS_PATH = Path("state/loras/index.json")
_TRAININGS_URL = "https://api.replicate.com/v1/trainings"
_PREDICTIONS_BASE = "https://api.replicate.com/v1/predictions"
_FLUX_LORA_MODEL = "https://api.replicate.com/v1/models/lucataco/flux-dev-lora/predictions"
_TRAINER_MODEL_OWNER = "ostris"
_TRAINER_MODEL_NAME = "flux-dev-lora-trainer"

LORA_TRAINING_COST = 10.0
MIN_TRAINING_IMAGES = 10
RECOMMENDED_TRAINING_IMAGES = 15


def _get_api_key() -> str:
    key = os.getenv("REPLICATE_API_KEY", "").strip()
    if not key:
        raise ValueError("REPLICATE_API_KEY not set")
    return key


def _load_db() -> Dict[str, Any]:
    if not _LORAS_PATH.exists():
        return {"loras": []}
    try:
        return json.loads(_LORAS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"loras": []}


def _save_db(data: Dict[str, Any]) -> None:
    _LORAS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LORAS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_loras(user_id: str) -> List[Dict[str, Any]]:
    """Return all LoRA models belonging to *user_id*."""
    data = _load_db()
    return [l for l in data.get("loras", []) if l.get("user_id") == user_id]


def get_lora(user_id: str, name: str) -> Optional[Dict[str, Any]]:
    """Get a specific LoRA by name for a user."""
    return next((l for l in list_loras(user_id) if l["name"] == name), None)


def start_lora_training(
    user_id: str,
    name: str,
    image_urls: List[str],
    trigger_word: str = "DANIIL",
) -> Dict[str, Any]:
    """Kick off LoRA training on Replicate. Returns training metadata."""
    if len(image_urls) < MIN_TRAINING_IMAGES:
        raise ValueError(
            f"Need at least {MIN_TRAINING_IMAGES} images, got {len(image_urls)}"
        )
    api_key = _get_api_key()

    destination = f"{user_id}/{name.lower().replace(' ', '-')}"
    payload = json.dumps({
        "destination": destination,
        "input": {
            "input_images": image_urls,
            "trigger_word": trigger_word,
            "steps": 1000,
        },
    }).encode()

    req = urllib.request.Request(
        f"https://api.replicate.com/v1/models/{_TRAINER_MODEL_OWNER}/{_TRAINER_MODEL_NAME}/trainings",
        data=payload,
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    training_id = "pending"
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            training_id = result.get("id", "pending")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode(errors="replace")[:500]
        except Exception:
            pass
        logger.error("LoRA training submit failed: HTTP %s — %s", exc.code, body)
        training_id = "error"

    entry = {
        "user_id": user_id,
        "name": name,
        "trigger_word": trigger_word,
        "training_id": training_id,
        "destination": destination,
        "status": "training" if training_id not in ("pending", "error") else training_id,
        "image_count": len(image_urls),
        "created_at": datetime.utcnow().isoformat(),
        "cost": LORA_TRAINING_COST,
    }
    data = _load_db()
    # Remove any existing entry with same name for this user
    data["loras"] = [
        l for l in data["loras"]
        if not (l["user_id"] == user_id and l["name"] == name)
    ]
    data["loras"].append(entry)
    _save_db(data)

    return {"training_id": training_id, "estimated_time_minutes": 30, "cost": LORA_TRAINING_COST}


def check_lora_status(training_id: str) -> str:
    """Check the status of a training run. Returns status string."""
    if training_id in ("pending", "error"):
        return training_id
    api_key = _get_api_key()
    req = urllib.request.Request(
        f"{_TRAININGS_URL}/{training_id}",
        headers={"Authorization": f"Token {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        return data.get("status", "unknown")
    except Exception as exc:
        logger.error("check_lora_status error: %s", exc)
        return "unknown"


def update_lora_status(user_id: str, name: str, status: str) -> None:
    """Update persisted status for a LoRA."""
    data = _load_db()
    for lora in data["loras"]:
        if lora.get("user_id") == user_id and lora.get("name") == name:
            lora["status"] = status
    _save_db(data)


def delete_lora(user_id: str, name: str) -> bool:
    """Remove a LoRA record for a user. Returns True if found."""
    data = _load_db()
    before = len(data["loras"])
    data["loras"] = [
        l for l in data["loras"]
        if not (l.get("user_id") == user_id and l.get("name") == name)
    ]
    if len(data["loras"]) < before:
        _save_db(data)
        return True
    return False


def generate_with_lora(lora_name: str, prompt: str, user_id: str) -> str:
    """Generate an image using a user's trained LoRA model."""
    lora = get_lora(user_id, lora_name)
    if not lora:
        raise ValueError(f"LoRA '{lora_name}' not found for user '{user_id}'")
    if lora.get("status") != "succeeded":
        raise ValueError(f"LoRA '{lora_name}' is not ready (status: {lora.get('status')})")

    api_key = _get_api_key()
    trigger = lora["trigger_word"]
    enhanced_prompt = f"{trigger}, {prompt}, photorealistic, high quality"

    payload = json.dumps({
        "input": {
            "prompt": enhanced_prompt,
            "hf_lora": lora.get("destination", ""),
            "aspect_ratio": "1:1",
            "output_format": "jpg",
        },
    }).encode()

    req = urllib.request.Request(
        _FLUX_LORA_MODEL,
        data=payload,
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
            body = exc.read().decode(errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"LoRA generation failed: HTTP {exc.code}: {body}") from exc

    pred_id = prediction.get("id")
    if not pred_id:
        raise RuntimeError(f"No prediction id returned: {prediction}")

    from app.services.face_swap import poll_replicate
    return poll_replicate(pred_id, api_key)
