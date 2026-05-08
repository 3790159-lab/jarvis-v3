"""Photo Studio — main entry point for all photo generation operations."""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from app.services.replicate_models import (
    REPLICATE_MODELS,
    get_model,
    get_model_cost,
    get_models_for_task,
    list_model_names,
)
from app.services.image_library import (
    list_images,
    save_image_metadata,
    total_cost_for_user,
)


class PhotoStudio:
    """Unified entry point for all photo operations."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.api_key = os.getenv("REPLICATE_API_KEY", "").strip()

    # ------------------------------------------------------------------ config

    def is_configured(self) -> bool:
        """True when REPLICATE_API_KEY is present."""
        return bool(self.api_key)

    # ------------------------------------------------------------------ costs

    def get_total_cost(self, days: int = 30) -> float:
        """Total image generation cost for this user over the last N days."""
        return total_cost_for_user(self.user_id, days)

    def estimate_cost(self, mode: str) -> float:
        """Return estimated cost for a single generation in *mode*."""
        model = get_model(mode)
        return model["cost"] if model else 0.0

    def estimate_pipeline_cost(self, pipeline: str) -> float:
        """Return estimated total cost for a named composite pipeline."""
        costs: Dict[str, float] = {
            "restaurant": get_model_cost("flux_pro"),
            "party": get_model_cost("flux_pro_ultra"),
            "face_swap": get_model_cost("face_swap_basic"),
            "face_swap_polished": get_model_cost("face_swap_basic") + get_model_cost("gfpgan"),
            "personal_lora": get_model_cost("flux_lora"),
            "lora_plus_swap": get_model_cost("flux_lora") + get_model_cost("face_swap_basic") + get_model_cost("gfpgan"),
            "enhance": get_model_cost("gfpgan"),
            "img2img": get_model_cost("img2img"),
            "general": get_model_cost("flux_pro_ultra"),
        }
        return round(costs.get(pipeline, get_model_cost("flux_pro_ultra")), 4)

    # ------------------------------------------------------------------ library

    def get_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return recent image history for this user."""
        return list_images(self.user_id, limit=limit)

    def save_result(
        self,
        url: str,
        prompt: str,
        mode: str,
        cost: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Persist a generated image and return its ID."""
        if cost is None:
            cost = self.estimate_cost(mode)
        return save_image_metadata(
            url=url,
            prompt=prompt,
            mode=mode,
            cost=cost,
            user_id=self.user_id,
            metadata=metadata,
        )

    # ------------------------------------------------------------------ info

    def available_models(self) -> List[str]:
        """Return all available model names."""
        return list_model_names()

    def model_info(self, name: str) -> Optional[Dict[str, Any]]:
        """Return model config dict."""
        return get_model(name)

    def models_for_task(self, task: str) -> List[Dict[str, Any]]:
        """Return models suited for a specific task."""
        return get_models_for_task(task)

    def pipeline_summary(self) -> Dict[str, Any]:
        """Summary of available pipelines and their costs."""
        pipelines = ["restaurant", "party", "face_swap", "face_swap_polished",
                     "personal_lora", "lora_plus_swap", "enhance", "img2img", "general"]
        return {
            p: {
                "cost": self.estimate_pipeline_cost(p),
                "configured": self.is_configured(),
            }
            for p in pipelines
        }
