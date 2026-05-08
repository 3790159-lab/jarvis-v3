from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.autonomy.mission_graph_models import GraphMissionSubmitRequest
from app.autonomy.mission_graph_service import MissionGraphService

router = APIRouter(prefix="/api/mission-graph", tags=["mission-graph"])


def _service() -> MissionGraphService:
    project_root = Path(__file__).resolve().parents[2]
    return MissionGraphService(project_root=project_root)


@router.get("/health")
def health() -> dict:
    return {
        "status": "healthy",
        "feature": "mission_graph",
        "capabilities": [
            "multi_step_missions",
            "dependency_aware_execution",
            "ready_pending_running_statuses",
            "graph_templates",
            "step_level_outputs",
            "graph_run_index",
            "step_retry",
            "partial_rerun",
            "graph_cancellation",
            "idempotency_guard",
            "event_history",
            "bundle_packaging",
            "failure_injection_testing",
        ],
        "supported_graph_kinds": [
            "youtube_launch",
            "site_game_bundle",
            "content_bundle",
            "single_artifact",
        ],
    }


@router.post("/submit")
def submit(request: GraphMissionSubmitRequest) -> dict:
    try:
        return _service().submit(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"mission graph submit failed: {exc}") from exc


@router.post("/test/failure-graph")
def create_failure_graph() -> dict:
    try:
        return _service().create_failure_test_graph()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"mission graph failure test creation failed: {exc}") from exc


@router.post("/missions/{graph_mission_id}/execute")
def execute(graph_mission_id: str) -> dict:
    try:
        return _service().execute(graph_mission_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"mission graph execute failed: {exc}") from exc


@router.post("/missions/{graph_mission_id}/rerun")
def rerun(graph_mission_id: str) -> dict:
    try:
        return _service().rerun(graph_mission_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"mission graph rerun failed: {exc}") from exc


@router.post("/missions/{graph_mission_id}/cancel")
def cancel(graph_mission_id: str) -> dict:
    try:
        return _service().cancel(graph_mission_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"mission graph cancel failed: {exc}") from exc


@router.post("/missions/{graph_mission_id}/package")
def package(graph_mission_id: str) -> dict:
    try:
        return _service().package(graph_mission_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"mission graph package failed: {exc}") from exc


@router.post("/missions/{graph_mission_id}/steps/{step_id}/retry")
def retry_step(graph_mission_id: str, step_id: str) -> dict:
    try:
        return _service().retry_step(graph_mission_id, step_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"mission graph step retry failed: {exc}") from exc


@router.get("/missions/{graph_mission_id}")
def get_mission(graph_mission_id: str) -> dict:
    try:
        return _service().get_mission(graph_mission_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"mission graph read failed: {exc}") from exc


@router.get("/runs")
def runs() -> dict:
    try:
        return _service().list_runs()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"mission graph runs failed: {exc}") from exc
