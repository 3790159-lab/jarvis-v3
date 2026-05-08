from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.core.supervisor import supervisor
from app.models.schemas import GoalRequest

router = APIRouter()


@router.get("/")
def root() -> dict:
    return {
        "name": "Jarvis V3 Supervisor",
        "status": "running",
    }


@router.get("/health")
def health() -> dict:
    return {
        "status": "healthy",
        "service": "jarvis_v3_supervisor",
    }


@router.post("/api/goals")
def create_goal(payload: GoalRequest) -> dict:
    return supervisor.create_mission(
        objective=payload.objective,
        constraints=payload.constraints,
    )


@router.get("/api/missions")
def list_missions(limit: int = Query(default=10, ge=1, le=50)) -> dict:
    return supervisor.list_missions(limit=limit)


@router.get("/api/missions/{mission_id}")
def get_mission(mission_id: str) -> dict:
    result = supervisor.get_mission(mission_id)

    if not result["found"]:
        raise HTTPException(status_code=404, detail="Mission not found")

    return result


@router.post("/api/missions/{mission_id}/run")
def run_mission(mission_id: str) -> dict:
    result = supervisor.run_mission(mission_id)

    if not result["found"]:
        raise HTTPException(status_code=404, detail="Mission not found")

    return result


@router.get("/api/missions/{mission_id}/logs")
def get_mission_logs(mission_id: str, limit: int = Query(default=50, ge=1, le=200)) -> dict:
    result = supervisor.get_logs(mission_id, limit=limit)

    if not result["found"]:
        raise HTTPException(status_code=404, detail="Mission not found")

    return result
