from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter

from app.services.time_brain import (
    DEFAULT_TZ,
    add_scheduled_task,
    dispatch_due_tasks,
    get_time_context,
    list_scheduled_tasks,
)


router = APIRouter(prefix="/api/time-brain", tags=["time-brain"])


@router.get("/now")
def api_now(timezone: str = DEFAULT_TZ) -> Dict[str, Any]:
    return get_time_context(timezone)


@router.get("/scheduled")
def api_scheduled(status: Optional[str] = None) -> Dict[str, Any]:
    return list_scheduled_tasks(status=status)


@router.post("/schedule")
def api_schedule(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "ok",
        "task": add_scheduled_task(
            task_id=payload["id"],
            title=payload["title"],
            due_at=payload["due_at"],
            timezone=payload.get("timezone", DEFAULT_TZ),
            lane=payload.get("lane", "scheduled"),
            priority=int(payload.get("priority", 50)),
            risk=payload.get("risk", "low"),
            details=payload.get("details", ""),
            gateway_plan=payload.get("gateway_plan"),
            recurrence=payload.get("recurrence"),
        ),
    }


@router.post("/dispatch-due")
def api_dispatch_due(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = payload or {}
    return dispatch_due_tasks(limit=int(payload.get("limit", 20)))