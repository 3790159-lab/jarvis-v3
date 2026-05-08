from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.jarvis_thinking_layer import think_and_enhance


router = APIRouter(prefix="/api/jarvis/brain", tags=["jarvis-brain"])


class ThinkRequest(BaseModel):
    task: str
    quality_target: str = Field(default="high")


@router.get("/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "jarvis_brain_router",
        "tools": [
            "brain.think",
            "brain.plan",
            "brain.internet_decision"
        ]
    }


@router.post("/think")
def think(req: ThinkRequest) -> Dict[str, Any]:
    return think_and_enhance(req.task, req.quality_target)


@router.post("/plan")
def plan(req: ThinkRequest) -> Dict[str, Any]:
    decision = think_and_enhance(req.task, req.quality_target)

    steps = [
        "Analyze task intent",
        "Decide if internet research is needed",
        "Enhance task with research if useful",
        "Choose execution tool/provider",
        "Run task asynchronously if long-running",
        "Save artifacts and report"
    ]

    return {
        "ok": True,
        "task": req.task,
        "quality_target": req.quality_target,
        "decision": decision,
        "plan": steps,
    }