from __future__ import annotations

from fastapi import APIRouter

from app.models.specialized_agents import (
    SpecializedExecutionRequest,
    SpecializedExecutionResult,
)
from app.services.ai.specialized_agent_service import SpecializedAgentService

router = APIRouter(prefix="/api/agents", tags=["specialized-agents"])

_service = SpecializedAgentService()


@router.post("/execute", response_model=SpecializedExecutionResult)
async def execute_specialized_agent(
    request: SpecializedExecutionRequest,
) -> SpecializedExecutionResult:
    return await _service.execute(request)


@router.post("/coding", response_model=SpecializedExecutionResult)
async def execute_coding(payload: dict) -> SpecializedExecutionResult:
    return await _service.execute_coding(
        title=payload.get("title", ""),
        description=payload.get("description", ""),
        objective=payload.get("objective", ""),
        metadata=payload.get("metadata", {}) or {},
    )


@router.post("/research", response_model=SpecializedExecutionResult)
async def execute_research(payload: dict) -> SpecializedExecutionResult:
    return await _service.execute_research(
        title=payload.get("title", ""),
        description=payload.get("description", ""),
        objective=payload.get("objective", ""),
        metadata=payload.get("metadata", {}) or {},
    )


@router.post("/reasoning", response_model=SpecializedExecutionResult)
async def execute_reasoning(payload: dict) -> SpecializedExecutionResult:
    return await _service.execute_reasoning(
        title=payload.get("title", ""),
        description=payload.get("description", ""),
        objective=payload.get("objective", ""),
        metadata=payload.get("metadata", {}) or {},
    )
