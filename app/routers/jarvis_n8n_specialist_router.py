from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.jarvis_n8n_specialist import JarvisN8nSpecialist


router = APIRouter(prefix="/api/n8n-specialist", tags=["n8n-specialist"])


class N8nTaskRequest(BaseModel):
    task: str = "Prepare Jarvis n8n workflow blueprint"
    deploy: bool = False
    test_webhook: bool = False


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _agent() -> JarvisN8nSpecialist:
    return JarvisN8nSpecialist(project_root=_project_root())


@router.get("/health")
def health() -> Dict[str, Any]:
    readiness = _agent().readiness()
    return {
        "status": "healthy" if readiness.status != "not_ready" else "degraded",
        "service": "jarvis_n8n_specialist",
        "readiness": readiness.__dict__,
    }


@router.post("/run")
def run(payload: N8nTaskRequest) -> Dict[str, Any]:
    agent = _agent()
    result = agent.handle_task(
        task=payload.task,
        deploy=payload.deploy,
        test_webhook=payload.test_webhook,
    )
    return {
        "result": result.__dict__,
        "summary": agent.format_human_summary(result),
    }


@router.get("/latest")
def latest() -> Dict[str, Any]:
    agent = _agent()
    latest = agent.latest_result()
    if latest.get("found"):
        latest["summary"] = agent.format_human_summary(latest["result"])
    return latest