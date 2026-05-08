"""Replicate model catalog — central registry of all available models."""
from __future__ import annotations

from typing import Dict, Any, Optional, List

REPLICATE_MODELS: Dict[str, Dict[str, Any]] = {
    # --- Generation ---
    "flux_pro": {
        "url": "https://api.replicate.com/v1/models/black-forest-labs/flux-1.1-pro/predictions",
        "cost": 0.04,
        "best_for": ["objects", "abstract", "default"],
        "supports_aspect_ratio": True,
    },
    "flux_pro_ultra": {
        "url": "https://api.replicate.com/v1/models/black-forest-labs/flux-1.1-pro-ultra/predictions",
        "cost": 0.06,
        "best_for": ["people", "landscape", "premium"],
        "supports_aspect_ratio": True,
    },

    # --- Face Swap ---
    "face_swap_basic": {
        "url": "https://api.replicate.com/v1/models/omniedgeio/face-swap/predictions",
        "cost": 0.005,
        "best_for": ["quick_swap"],
        "supports_aspect_ratio": False,
    },
    "face_swap_reactor": {
        "url": "https://api.replicate.com/v1/models/codeplugtech/face-swap/predictions",
        "cost": 0.01,
        "best_for": ["polished_swap"],
        "supports_aspect_ratio": False,
    },
    "photomaker": {
        "url": "https://api.replicate.com/v1/models/tencentarc/photomaker/predictions",
        "cost": 0.05,
        "best_for": ["character_consistency"],
        "supports_aspect_ratio": False,
    },

    # --- Polishing ---
    "gfpgan": {
        "url": "https://api.replicate.com/v1/models/tencentarc/gfpgan/predictions",
        "cost": 0.002,
        "best_for": ["face_polish"],
        "supports_aspect_ratio": False,
    },

    # --- Image-to-Image ---
    "img2img": {
        "url": "https://api.replicate.com/v1/models/black-forest-labs/flux-redux-dev/predictions",
        "cost": 0.025,
        "best_for": ["style_transfer", "enhance"],
        "supports_aspect_ratio": True,
    },

    # --- LoRA ---
    "flux_lora": {
        "url": "https://api.replicate.com/v1/models/lucataco/flux-dev-lora/predictions",
        "cost": 0.03,
        "best_for": ["lora_generation"],
        "supports_aspect_ratio": True,
    },
    "flux_lora_training": {
        "url": "https://api.replicate.com/v1/models/ostris/flux-dev-lora-trainer/trainings",
        "cost": 10.0,
        "best_for": ["lora_training"],
        "supports_aspect_ratio": False,
    },
}


def get_model(name: str) -> Optional[Dict[str, Any]]:
    """Return model config by name, or None if not found."""
    return REPLICATE_MODELS.get(name)


def get_models_for_task(task: str) -> List[Dict[str, Any]]:
    """Return all models that support a given task/tag."""
    return [
        {"name": k, **v}
        for k, v in REPLICATE_MODELS.items()
        if task in v.get("best_for", [])
    ]


def list_model_names() -> List[str]:
    """Return all registered model names."""
    return list(REPLICATE_MODELS.keys())


def get_cheapest_for_task(task: str) -> Optional[Dict[str, Any]]:
    """Return cheapest model for a given task."""
    candidates = get_models_for_task(task)
    if not candidates:
        return None
    return min(candidates, key=lambda m: m["cost"])


def get_model_cost(name: str) -> float:
    """Return cost for model, or 0.0 if not found."""
    model = get_model(name)
    return model["cost"] if model else 0.0
