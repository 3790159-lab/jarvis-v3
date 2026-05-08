from __future__ import annotations

from fastapi import APIRouter

from app.models.auto_memory import (
    AutoMemoryMissionPayload,
    AutoMemoryRunResponse,
)
from app.services.memory.auto_memory_pipeline import AutoMemoryPipeline

router = APIRouter(prefix="/api/mission-memory", tags=["mission-memory"])

_pipeline = AutoMemoryPipeline()


@router.post("/run", response_model=AutoMemoryRunResponse)
async def run_auto_memory(payload: AutoMemoryMissionPayload) -> AutoMemoryRunResponse:
    return _pipeline.run(payload)
