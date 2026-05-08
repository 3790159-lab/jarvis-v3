from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.jarvis_ai_engineer import ai_engineer_review, recommend_next_provider

router = APIRouter(prefix="/api/jarvis/ai-engineer", tags=["jarvis-ai-engineer"])


class EngineerRequest(BaseModel):
    task: str
    mode: str = Field(default="safe_plan")


class ProviderRequest(BaseModel):
    goal: str


@router.get("/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "jarvis_ai_engineer",
        "tools": [
            "ai_engineer.review",
            "ai_engineer.recommend_provider"
        ]
    }


@router.post("/review")
def review(req: EngineerRequest) -> Dict[str, Any]:
    return ai_engineer_review(req.task, req.mode)


@router.post("/recommend-provider")
def recommend(req: ProviderRequest) -> Dict[str, Any]:
    return recommend_next_provider(req.goal)