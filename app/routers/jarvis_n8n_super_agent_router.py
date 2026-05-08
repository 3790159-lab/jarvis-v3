from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.jarvis_n8n_super_agent import JarvisN8nSuperAgent


router = APIRouter(prefix="/api/n8n-super-agent", tags=["n8n-super-agent"])


class N8nSuperRunRequest(BaseModel):
    task: str
    workflow_kind: Optional[str] = None
    activate: bool = True
    test_webhook: bool = True


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _agent() -> JarvisN8nSuperAgent:
    return JarvisN8nSuperAgent(_project_root())


@router.get("/health")
def health() -> Dict[str, Any]:
    agent = _agent()
    readiness = agent.specialist.readiness()
    return {
        "status": "healthy" if readiness.status == "ready_for_deploy" else "degraded",
        "service": "jarvis_n8n_super_agent",
        "n8n_readiness": readiness.__dict__,
    }


@router.post("/run")
def run(payload: N8nSuperRunRequest) -> Dict[str, Any]:
    result = _agent().run(
        user_task=payload.task,
        workflow_kind=payload.workflow_kind,
        activate=payload.activate,
        test_webhook=payload.test_webhook,
    )
    return {
        "result": result.__dict__,
        "summary": result.summary,
    }


@router.get("/latest")
def latest() -> Dict[str, Any]:
    return _agent().latest_run()