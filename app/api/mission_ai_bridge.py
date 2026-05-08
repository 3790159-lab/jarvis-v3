from __future__ import annotations

from fastapi import APIRouter

from app.models.mission_ai import (
    MissionAIRequest,
    MissionAIResponse,
    MissionAITask,
    TaskClassificationResult,
)
from app.services.ai.mission_ai_orchestrator import MissionAIOrchestrator

router = APIRouter(prefix="/api/mission-ai", tags=["mission-ai"])

_service = MissionAIOrchestrator()


@router.post("/classify-task", response_model=TaskClassificationResult)
async def classify_task(payload: dict) -> TaskClassificationResult:
    task = MissionAITask(
        task_id=payload.get("task_id"),
        title=payload.get("title", ""),
        description=payload.get("description", ""),
        metadata=payload.get("metadata", {}) or {},
    )
    objective = payload.get("objective", "") or ""
    return _service.classify_task(task=task, objective=objective)


@router.post("/execute", response_model=MissionAIResponse)
async def execute_mission_ai(request: MissionAIRequest) -> MissionAIResponse:
    return await _service.execute(request)
