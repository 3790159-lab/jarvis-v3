from __future__ import annotations

from fastapi import APIRouter

from app.models.multi_step_execution import (
    MultiStepExecutionRequest,
    MultiStepExecutionResponse,
)
from app.services.execution.multi_step_execution_service import MultiStepExecutionService

router = APIRouter(prefix="/api/multi-step", tags=["multi-step-execution"])

_service = MultiStepExecutionService()


@router.post("/execute", response_model=MultiStepExecutionResponse)
async def execute_multi_step(request: MultiStepExecutionRequest) -> MultiStepExecutionResponse:
    return await _service.execute(request)
