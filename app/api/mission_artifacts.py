from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.autonomy.mission_artifact_models import MissionArtifactRequest
from app.autonomy.mission_artifact_service import MissionArtifactService

router = APIRouter(prefix="/api/mission-artifacts", tags=["mission-artifacts"])


def _service() -> MissionArtifactService:
    project_root = Path(__file__).resolve().parents[2]
    return MissionArtifactService(project_root=project_root)


@router.get("/health")
def health() -> dict:
    return {
        "status": "healthy",
        "feature": "mission_artifact_integration",
        "supported_task_types": ["table", "site", "game", "youtube_pack"],
        "supported_actions": ["classify", "execute"],
    }


@router.post("/classify")
def classify(request: MissionArtifactRequest) -> dict:
    try:
        return _service().classify(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"classification failed: {exc}") from exc


@router.post("/execute")
def execute(request: MissionArtifactRequest) -> dict:
    try:
        return _service().execute(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"mission artifact execution failed: {exc}") from exc
