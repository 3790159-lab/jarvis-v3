from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.dependencies import get_mission_repository
from app.services.mission_runner import MissionRunnerService

router = APIRouter(prefix="/api/missions", tags=["missions"])


@router.post("/{mission_id}/run")
async def run_mission(mission_id: str):
    repository = get_mission_repository()
    service = MissionRunnerService(repository)

    try:
        result = await service.run_mission(mission_id)
        return result.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Mission run failed: {exc}") from exc