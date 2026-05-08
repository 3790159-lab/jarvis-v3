from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException

from app.services.artifact_registry import (
    get_artifact_manifest,
    get_mission_result,
    list_mission_summaries,
)


router = APIRouter(tags=["artifacts_runtime"])


@router.get("/api/artifacts/health")
def artifacts_health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "artifacts_runtime",
        "features": [
            "artifact_manifest",
            "mission_result_packaging",
            "mission_result_lookup",
            "artifact_listing",
        ],
    }


@router.get("/api/artifacts/missions")
def list_artifact_missions() -> Dict[str, Any]:
    items = list_mission_summaries()
    return {
        "ok": True,
        "count": len(items),
        "items": items,
    }


@router.get("/api/artifacts/missions/{mission_id}/manifest")
def get_artifacts_manifest(mission_id: str) -> Dict[str, Any]:
    manifest = get_artifact_manifest(mission_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="Artifact manifest not found")
    return {
        "ok": True,
        "manifest": manifest,
    }


@router.get("/api/artifacts/missions/{mission_id}/result")
def get_artifacts_result(mission_id: str) -> Dict[str, Any]:
    result = get_mission_result(mission_id)
    if not result:
        raise HTTPException(status_code=404, detail="Mission result not found")
    return {
        "ok": True,
        "result": result,
    }