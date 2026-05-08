from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.tool_router import select_tool


router = APIRouter(tags=["tool_routing"])


class ToolRoutingPreviewRequest(BaseModel):
    objective: str
    step: Dict[str, Any] = Field(default_factory=dict)


@router.get("/api/tools/router/health")
def tool_router_health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "tool_routing",
        "features": [
            "manual_override",
            "heuristic_routing",
            "llm_routing",
            "fallback_candidates",
            "routing_reason",
            "routing_confidence",
        ],
    }


@router.post("/api/tools/router/plan")
def tool_router_plan(payload: ToolRoutingPreviewRequest) -> Dict[str, Any]:
    plan = select_tool(payload.objective, payload.step)
    return {
        "ok": True,
        "plan": plan,
    }