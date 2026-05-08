from __future__ import annotations

from typing import Optional, Dict, Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.multi_ai_orchestrator_v1 import run_multi_ai, health


router = APIRouter(tags=["Jarvis Multi-AI Orchestrator V1"])


class MultiAIRequest(BaseModel):
    message: Optional[str] = None
    text: Optional[str] = None
    prompt: Optional[str] = None
    mode: Optional[str] = None


@router.get("/api/multi-ai/health")
async def multi_ai_health() -> Dict[str, Any]:
    return health()


@router.post("/api/multi-ai/respond")
async def multi_ai_respond(req: MultiAIRequest) -> Dict[str, Any]:
    text = (req.message or req.text or req.prompt or "").strip()
    return run_multi_ai(text=text, mode=req.mode)


@router.post("/api/brain-v2/respond")
async def brain_v2_respond(req: MultiAIRequest) -> Dict[str, Any]:
    text = (req.message or req.text or req.prompt or "").strip()
    return run_multi_ai(text=text, mode=req.mode)