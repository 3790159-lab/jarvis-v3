from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.jarvis_v5_content_factory import JarvisV5ContentFactory


router = APIRouter(prefix="/api/jarvis/v5/content-factory", tags=["jarvis-v5-content-factory"])


class ContentFactoryRequest(BaseModel):
    prompt: Optional[str] = None
    text_prompt: Optional[str] = None
    style_mode: Optional[str] = "luxury_safe"
    image_batch: Optional[int] = 4
    video_enabled: Optional[bool] = True
    video_model: Optional[str] = "kling-3"


@router.get("/health")
def health() -> Dict[str, Any]:
    return JarvisV5ContentFactory().health()


@router.post("/run")
def run(req: ContentFactoryRequest) -> Dict[str, Any]:
    return JarvisV5ContentFactory().run(req.model_dump())