from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.tool_chain_planner import plan_tool_chain


router = APIRouter(tags=["tool_chains"])


class ToolChainPreviewRequest(BaseModel):
    objective: str
    step: Dict[str, Any] = Field(default_factory=dict)


@router.get("/api/tools/chains/health")
def tool_chains_health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "tool_chains",
        "features": [
            "manual_chain",
            "heuristic_chain",
            "single_tool_fallback",
            "multi_tool_planning",
            "chain_preview",
        ],
    }


@router.post("/api/tools/chains/plan")
def tool_chains_plan(payload: ToolChainPreviewRequest) -> Dict[str, Any]:
    plan = plan_tool_chain(payload.objective, payload.step)
    return {
        "ok": True,
        "plan": plan,
    }