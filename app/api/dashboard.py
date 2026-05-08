from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pathlib import Path

from app.services.dashboard_service import dashboard_snapshot, mission_detail_snapshot

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parents[2]
DASHBOARD_HTML = BASE_DIR / "app" / "static" / "dashboard.html"


@router.get("/dashboard/data")
def dashboard_data():
    return dashboard_snapshot()


@router.get("/dashboard/missions/{mission_id}")
def dashboard_mission_detail(mission_id: str):
    data = mission_detail_snapshot(mission_id)
    if not data.get("mission"):
        raise HTTPException(status_code=404, detail="Mission not found")
    return data


@router.get("/dashboard")
def dashboard_page():
    return FileResponse(str(DASHBOARD_HTML))