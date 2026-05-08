from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.autonomy.runtime_bridge_models import RuntimeBridgeSubmitRequest
from app.autonomy.runtime_bridge_service import RuntimeBridgeService

router = APIRouter(prefix="/api/runtime-bridge", tags=["runtime-bridge"])


def _service() -> RuntimeBridgeService:
    project_root = Path(__file__).resolve().parents[2]
    return RuntimeBridgeService(project_root=project_root)


@router.get("/health")
def health() -> dict:
    return {
        "status": "healthy",
        "feature": "runtime_bridge",
        "capabilities": [
            "goal_recording",
            "mission_recording",
            "classification_bridge",
            "artifact_execution_bridge",
            "status_lifecycle",
            "latest_runs_index",
            "planned_execution",
            "mission_cancellation",
        ],
    }


@router.post("/submit")
def submit(request: RuntimeBridgeSubmitRequest) -> dict:
    try:
        return _service().submit(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"runtime bridge submit failed: {exc}") from exc


@router.post("/missions/{mission_id}/execute")
def execute_planned_mission(mission_id: str) -> dict:
    try:
        return _service().execute_planned_mission(mission_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"runtime bridge mission execute failed: {exc}") from exc


@router.post("/missions/{mission_id}/cancel")
def cancel_mission(mission_id: str) -> dict:
    try:
        return _service().cancel_mission(mission_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"runtime bridge mission cancel failed: {exc}") from exc


@router.get("/goals/{goal_id}")
def get_goal(goal_id: str) -> dict:
    try:
        return _service().get_goal(goal_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"runtime bridge goal read failed: {exc}") from exc


@router.get("/missions/{mission_id}")
def get_mission(mission_id: str) -> dict:
    try:
        return _service().get_mission(mission_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"runtime bridge mission read failed: {exc}") from exc


@router.get("/runs")
def list_runs() -> dict:
    try:
        return _service().list_runs()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"runtime bridge runs read failed: {exc}") from exc
