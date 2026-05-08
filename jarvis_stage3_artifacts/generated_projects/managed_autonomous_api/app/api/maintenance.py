from __future__ import annotations

from fastapi import APIRouter

from app.services.maintenance import MaintenanceService


router = APIRouter(prefix="/api/maintenance", tags=["maintenance"])


@router.get("/health")
def maintenance_health():
    return {
        "status": "healthy",
        "service": "maintenance"
    }


@router.post("/normalize-legacy")
def normalize_legacy():
    service = MaintenanceService()
    return service.normalize_legacy_runs()


@router.post("/sync-all")
def sync_all():
    service = MaintenanceService()
    return service.sync_all()


@router.get("/audit")
def audit():
    service = MaintenanceService()
    return service.audit()
