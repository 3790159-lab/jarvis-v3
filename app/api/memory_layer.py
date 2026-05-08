from __future__ import annotations

from fastapi import APIRouter

from app.models.memory_layer import (
    MemoryListResponse,
    MemoryWriteRequest,
    MemoryWriteResponse,
    MissionSummaryRequest,
    MissionSummaryResponse,
)
from app.services.memory.semantic_memory_service import SemanticMemoryService

router = APIRouter(prefix="/api/memory-layer", tags=["memory-layer"])

_service = SemanticMemoryService()


@router.post("/write", response_model=MemoryWriteResponse)
async def write_memory(request: MemoryWriteRequest) -> MemoryWriteResponse:
    return _service.write_note(request)


@router.post("/mission-summary", response_model=MissionSummaryResponse)
async def write_mission_summary(request: MissionSummaryRequest) -> MissionSummaryResponse:
    return _service.write_mission_summary(request)


@router.get("/list/{category}", response_model=MemoryListResponse)
async def list_memory(category: str) -> MemoryListResponse:
    return _service.list_category(category)
