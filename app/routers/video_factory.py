from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.video_factory_service import VideoFactoryService
from app.prompts.video_prompt_builder import build_i2v_prompt


router = APIRouter(prefix="/api/video-factory", tags=["video-factory"])


class I2VRunRequest(BaseModel):
    image_path: str = Field(..., description="Local path to input image")
    positive_prompt: Optional[str] = None
    negative_prompt: Optional[str] = None

    subject: str = "adult person (20+)"
    scene: str = "cozy modern cafe, warm cinematic lighting"
    action: str = "a small natural smile appears"

    width: int = 560
    height: int = 720
    frames: int = 121
    fps: int = 30
    steps: int = 8

    wait: bool = True
    timeout_sec: int = 3600


@router.get("/health")
def health():
    return VideoFactoryService().health()


@router.post("/i2v/run")
def run_i2v(req: I2VRunRequest):
    try:
        prompt = req.positive_prompt or build_i2v_prompt(
            subject=req.subject,
            scene=req.scene,
            action=req.action,
            duration_sec=max(3, int(req.frames / max(req.fps, 1))),
        )

        return VideoFactoryService().run_i2v(
            image_path=req.image_path,
            positive_prompt=prompt,
            negative_prompt=req.negative_prompt,
            width=req.width,
            height=req.height,
            frames=req.frames,
            fps=req.fps,
            steps=req.steps,
            wait=req.wait,
            timeout_sec=req.timeout_sec,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))