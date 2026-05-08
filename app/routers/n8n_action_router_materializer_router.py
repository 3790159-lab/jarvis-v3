from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.n8n_action_router_materializer import N8NActionRouterMaterializer

router = APIRouter(prefix="/api/n8n/materializer", tags=["n8n-materializer"])
_service = N8NActionRouterMaterializer()


class ExecuteActionRequest(BaseModel):
    action: str
    payload: Dict[str, Any] = Field(default_factory=dict)


class PublishWorkflowRequest(BaseModel):
    name: Optional[str] = None
    webhook_path: Optional[str] = None
    response_text: Optional[str] = None
    probe_payload: Optional[Dict[str, Any]] = None


@router.get("/health")
def health() -> Dict[str, Any]:
    return _service.health()


@router.get("/debug-state")
def debug_state() -> Dict[str, Any]:
    return _service.debug_state()


@router.post("/dry-run-payload")
def dry_run_payload(request: PublishWorkflowRequest) -> Dict[str, Any]:
    return _service.dry_run_payload(
        {
            "name": request.name,
            "webhook_path": request.webhook_path,
            "response_text": request.response_text,
        }
    )


@router.post("/execute")
def execute(request: ExecuteActionRequest) -> Dict[str, Any]:
    try:
        return _service.handle(request.action, request.payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/publish-workflow")
def publish_workflow(request: PublishWorkflowRequest) -> Dict[str, Any]:
    try:
        return _service.handle(
            "publish:workflow",
            {
                "name": request.name,
                "webhook_path": request.webhook_path,
                "response_text": request.response_text,
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/publish-and-probe")
def publish_and_probe(request: PublishWorkflowRequest) -> Dict[str, Any]:
    try:
        return _service.handle(
            "publish_and_probe:workflow",
            {
                "name": request.name,
                "webhook_path": request.webhook_path,
                "response_text": request.response_text,
                "probe_payload": request.probe_payload or {},
            },
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))