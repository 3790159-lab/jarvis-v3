from __future__ import annotations
import app.core.bootstrap_utf8  # noqa: F401

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.mission_engine import MissionEngine

router = APIRouter(prefix="/api", tags=["goals"])

engine = MissionEngine()


class GoalCreateRequest(BaseModel):
    objective: str = Field(min_length=1)
    constraints: dict[str, Any] = Field(default_factory=dict)


@router.post("/goals")
def create_goal(payload: GoalCreateRequest) -> dict[str, Any]:
    try:
        return engine.create_goal(payload.objective, payload.constraints)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"create_goal_failed: {exc}")


@router.get("/missions")
def list_missions() -> dict[str, Any]:
    missions = engine.list_missions()
    return {
        "count": len(missions),
        "items": missions,
    }


@router.get("/missions/{mission_id}")
def get_mission(mission_id: str) -> dict[str, Any]:
    mission = engine.get_mission(mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return mission


@router.post("/missions/{mission_id}/run")
def run_mission(mission_id: str) -> dict[str, Any]:
    try:
        return engine.run_mission(mission_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"run_mission_failed: {exc}")


@router.get("/missions/{mission_id}/logs")
def get_logs(mission_id: str) -> dict[str, Any]:
    return {
        "mission_id": mission_id,
        "logs": engine.get_logs(mission_id),
    }
