from __future__ import annotations

from fastapi import APIRouter

from app.models.external_executor import (
    CloudExecutorHealthResponse,
    ExternalExecutorRequest,
    ExternalExecutorResponse,
)
from app.services.ai.external_executor_service import ExternalExecutorService

router = APIRouter(prefix="/api/external-executor", tags=["external-executor"])

_service = ExternalExecutorService()


@router.get("/health", response_model=CloudExecutorHealthResponse)
async def external_executor_health() -> CloudExecutorHealthResponse:
    return _service.health()


@router.post("/execute", response_model=ExternalExecutorResponse)
async def external_executor_execute(request: ExternalExecutorRequest) -> ExternalExecutorResponse:
    return await _service.execute(request)
