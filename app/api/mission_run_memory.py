from __future__ import annotations

from fastapi import APIRouter

from app.models.mission_run_memory import MissionRunMemoryRequest, MissionRunMemoryResponse
from app.services.memory.mission_run_memory_hook import MissionRunMemoryHook

router = APIRouter(prefix="/api/mission-run-memory", tags=["mission-run-memory"])

_hook = MissionRunMemoryHook()


@router.post("/write", response_model=MissionRunMemoryResponse)
async def write_mission_run_memory(request: MissionRunMemoryRequest) -> MissionRunMemoryResponse:
    return _hook.handle(
        mission_result=request.mission_result,
        objective=request.objective,
        metadata=request.metadata,
    )
