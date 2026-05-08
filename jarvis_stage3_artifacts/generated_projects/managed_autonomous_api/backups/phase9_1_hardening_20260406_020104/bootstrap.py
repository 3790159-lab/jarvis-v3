from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.bootstrap_agent import BootstrapAgentService


router = APIRouter(prefix="/api/bootstrap", tags=["bootstrap"])


class BootstrapCreateRequest(BaseModel):
    template_name: str
    agent_id: str
    name: str
    owner: str = "system"


@router.get("/health")
def bootstrap_health():
    service = BootstrapAgentService()
    check = service.self_check()
    return {
        "status": "healthy" if check["status"] == "ok" else "degraded",
        "service": "bootstrap_agent",
        "templates_count": len(service.list_templates().get("templates", {}))
    }


@router.get("/templates")
def list_templates():
    service = BootstrapAgentService()
    return {
        "status": "ok",
        "templates": service.list_templates()
    }


@router.get("/self-check")
def bootstrap_self_check():
    service = BootstrapAgentService()
    return service.self_check()


@router.post("/create")
def bootstrap_create(payload: BootstrapCreateRequest):
    service = BootstrapAgentService()
    return service.create_from_template(
        template_name=payload.template_name,
        agent_id=payload.agent_id,
        name=payload.name,
        owner=payload.owner
    )
