from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.tool_executor_runtime import execute_tool
from app.services.tool_registry import get_tool_registry


router = APIRouter(tags=["tools_runtime"])


class ToolExecuteRequest(BaseModel):
    tool: str
    payload: Dict[str, Any] = Field(default_factory=dict)


@router.get("/api/tools/health")
def tools_health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "tools_runtime",
        "tool_count": len(get_tool_registry()),
    }


@router.get("/api/tools/registry")
def tools_registry() -> Dict[str, Any]:
    return {
        "ok": True,
        "items": get_tool_registry(),
    }


@router.post("/api/tools/execute")
def tools_execute(payload: ToolExecuteRequest) -> Dict[str, Any]:
    result = execute_tool(payload.tool, payload.payload)
    return result