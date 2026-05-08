from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from app.services.claude_ecosystem_orchestrator import (
    run_after_mission,
    run_after_night_mode,
    run_before_mission,
    run_before_night_mode,
    run_ecosystem_preflight,
)
from app.services.claude_ecosystem_registry import get_claude_ecosystem_capabilities
from app.services.self_healing import check_backend, recommend_recovery


router = APIRouter(prefix="/api/claude-ecosystem", tags=["claude-ecosystem"])


@router.get("/health")
def ecosystem_health() -> Dict[str, Any]:
    health = check_backend()
    return {
        "status": "healthy" if health.ok else "degraded",
        "schema": "jarvis.claude_ecosystem.router.v1_2",
        "backend_health": health.to_dict(),
        "recovery": recommend_recovery(health),
    }


@router.get("/registry")
def ecosystem_registry() -> Dict[str, Any]:
    return get_claude_ecosystem_capabilities()


@router.post("/preflight")
def ecosystem_preflight(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return run_ecosystem_preflight(payload or {}, stage="api_preflight")


@router.post("/mission/{mission_id}/before")
def ecosystem_before_mission(mission_id: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return run_before_mission(mission_id, payload or {})


@router.post("/mission/{mission_id}/after")
def ecosystem_after_mission(mission_id: str, payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = payload or {}
    success = bool(payload.get("success", True))
    return run_after_mission(mission_id, payload, success=success)


@router.post("/night-mode/before")
def ecosystem_before_night_mode(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return run_before_night_mode(payload or {})


@router.post("/night-mode/after")
def ecosystem_after_night_mode(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return run_after_night_mode(payload or {})