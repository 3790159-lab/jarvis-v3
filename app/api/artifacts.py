from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from app.autonomy.artifact_builders import build_artifact
from app.autonomy.artifact_models import ArtifactTaskRequest

router = APIRouter(prefix="/api/artifacts", tags=["artifacts"])


@router.get("/health")
def artifacts_health() -> dict:
    return {
        "status": "healthy",
        "feature": "sandbox_artifacts",
        "supported_task_types": ["table", "site", "game", "youtube_pack"],
        "deliverable_upgrades": [
            "xlsx_tables",
            "site_bundle_v2",
            "game_bundle_v2",
            "youtube_pack_v2",
        ],
    }


@router.post("/build")
def build_artifact_endpoint(request: ArtifactTaskRequest) -> dict:
    try:
        project_root = Path(__file__).resolve().parents[2]
        result = build_artifact(project_root=project_root, request=request)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Artifact build failed: {exc}") from exc
