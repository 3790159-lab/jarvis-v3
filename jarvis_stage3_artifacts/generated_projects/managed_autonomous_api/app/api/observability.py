from __future__ import annotations

from fastapi import APIRouter

from app.services.observability import ObservabilityService


router = APIRouter(prefix="/api/observability", tags=["observability"])


@router.get("/health")
def observability_health():
    return {
        "status": "healthy",
        "service": "observability"
    }


@router.post("/snapshot")
def build_snapshot():
    service = ObservabilityService()
    snapshot = service.snapshot()
    return {
        "status": "ok",
        "snapshot": snapshot
    }


@router.get("/timeline")
def get_timeline():
    service = ObservabilityService()
    return {
        "status": "ok",
        "timeline": service.timeline()
    }


@router.get("/summary")
def get_summary():
    service = ObservabilityService()
    return service.system_summary()
