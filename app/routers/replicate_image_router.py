"""FastAPI router: Replicate FLUX 1.1 Pro image generation."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/jarvis/image", tags=["image-gen"])


class ImageGenRequest(BaseModel):
    prompt: str
    num_images: int = 1
    aspect_ratio: str = "9:16"
    style: str = "realistic"


class ImageGenResponse(BaseModel):
    urls: List[str]
    provider: str
    status: str


@router.get("/health")
def image_gen_health() -> dict:
    from app.services.replicate_image_gen import is_replicate_configured
    return {
        "status": "ok",
        "replicate_configured": is_replicate_configured(),
    }


@router.post("/generate", response_model=ImageGenResponse)
def generate_images(req: ImageGenRequest) -> ImageGenResponse:
    """Generate images via Replicate FLUX 1.1 Pro with fallback."""
    urls, provider = _try_generate(req.prompt, req.num_images, req.aspect_ratio, req.style)
    return ImageGenResponse(urls=urls, provider=provider, status="ok")


def _try_generate(
    prompt: str,
    num_images: int,
    aspect_ratio: str,
    style: str,
) -> tuple[List[str], str]:
    from app.services.replicate_image_gen import generate_images_replicate, is_replicate_configured
    import logging
    logger = logging.getLogger(__name__)

    if is_replicate_configured():
        try:
            urls = generate_images_replicate(prompt, num_images, aspect_ratio, style)
            return urls, "replicate"
        except Exception as e:
            logger.warning("Replicate failed: %s", e)

    raise RuntimeError("Все провайдеры генерации недоступны. Установите REPLICATE_API_KEY.")
