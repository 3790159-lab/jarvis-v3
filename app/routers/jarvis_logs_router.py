from __future__ import annotations

from fastapi import APIRouter

from app.services.structured_logger import build_daily_report, read_today_log

router = APIRouter(prefix="/api/jarvis/logs", tags=["jarvis-logs"])


@router.get("/daily-report")
async def daily_report():
    entries = read_today_log()
    report = build_daily_report(entries)
    report["ok"] = True
    return report


@router.get("/today")
async def today_entries():
    entries = read_today_log()
    return {"ok": True, "count": len(entries), "entries": entries[-50:]}
