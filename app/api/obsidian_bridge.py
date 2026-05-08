from __future__ import annotations

from fastapi import APIRouter

from app.models.obsidian_bridge import (
    AutoMissionMemoryRequest,
    AutoMissionMemoryResponse,
    ObsidianExportRequest,
    ObsidianExportResponse,
)
from app.services.memory.obsidian_bridge_service import ObsidianBridgeService

router = APIRouter(prefix="/api/obsidian", tags=["obsidian-bridge"])

_service = ObsidianBridgeService()


@router.post("/export-note", response_model=ObsidianExportResponse)
async def export_note(request: ObsidianExportRequest) -> ObsidianExportResponse:
    return _service.export_note(request)


@router.post("/auto-mission-memory", response_model=AutoMissionMemoryResponse)
async def auto_mission_memory(request: AutoMissionMemoryRequest) -> AutoMissionMemoryResponse:
    return _service.auto_write_mission_memory(request)
