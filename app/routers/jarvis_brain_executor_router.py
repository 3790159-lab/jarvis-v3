from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.jarvis_brain_executor import JarvisBrainExecutor


router = APIRouter(prefix="/api/brain-executor", tags=["jarvis-brain-executor"])


class ExecuteRequest(BaseModel):
    task: str
    dry_run: bool = False


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _executor() -> JarvisBrainExecutor:
    return JarvisBrainExecutor(_project_root())


@router.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": "jarvis_brain_executor",
        "capabilities": [
            "compile_task",
            "route_to_n8n_super_agent",
            "risk_gate",
            "store_execution_result",
            "human_summary",
        ],
    }


@router.post("/run")
def run(payload: ExecuteRequest) -> Dict[str, Any]:
    executor = _executor()
    result = executor.execute(payload.task, dry_run=payload.dry_run)
    return {
        "execution": result.__dict__,
        "summary": result.human_summary,
    }


@router.get("/latest")
def latest() -> Dict[str, Any]:
    return _executor().latest()