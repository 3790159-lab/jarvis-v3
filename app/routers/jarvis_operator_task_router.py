from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.jarvis_operator_task_center import JarvisOperatorTaskCenter


router = APIRouter(prefix="/api/operator", tags=["operator"])


class CreateTaskRequest(BaseModel):
    title: str
    objective: str
    priority: str = "normal"
    plan: Optional[List[str]] = None


class UpdateTaskRequest(BaseModel):
    status: Optional[str] = None
    stage: Optional[str] = None
    progress: Optional[int] = Field(default=None, ge=0, le=100)
    next_action: Optional[str] = None
    notes: Optional[List[str]] = None


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _center() -> JarvisOperatorTaskCenter:
    return JarvisOperatorTaskCenter(project_root=_project_root())


@router.get("/health")
def operator_health() -> Dict[str, Any]:
    return {"status": "healthy", "service": "jarvis_operator_task_center"}


@router.post("/tasks")
def create_task(payload: CreateTaskRequest) -> Dict[str, Any]:
    task = _center().create_task(
        title=payload.title,
        objective=payload.objective,
        priority=payload.priority,
        plan=payload.plan,
    )
    return {"ok": True, "task": task.__dict__}


@router.get("/tasks")
def list_tasks() -> Dict[str, Any]:
    return {"ok": True, "tasks": _center().list_tasks()}


@router.get("/tasks/{task_id}")
def get_task(task_id: str) -> Dict[str, Any]:
    return {"ok": True, "task": _center().get_task(task_id)}


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, payload: UpdateTaskRequest) -> Dict[str, Any]:
    return _center().update_task(
        task_id,
        status=payload.status,
        stage=payload.stage,
        progress=payload.progress,
        next_action=payload.next_action,
        notes=payload.notes,
    )


@router.get("/status")
def operator_status() -> Dict[str, Any]:
    return _center().operator_status()


@router.get("/next")
def operator_next() -> Dict[str, Any]:
    status = _center().operator_status()
    return {
        "ok": True,
        "next_actions": status.get("next_actions", []),
        "latest_night_session": status.get("latest_night_session"),
    }


@router.post("/telegram/report")
def send_telegram_report() -> Dict[str, Any]:
    return _center().send_operator_report()