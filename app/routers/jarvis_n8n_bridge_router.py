from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.jarvis_n8n_bridge_client import get_jarvis_n8n_bridge_client

router = APIRouter(prefix="/api/jarvis/n8n", tags=["jarvis-n8n"])
_bridge = get_jarvis_n8n_bridge_client()


class CreateWebhookWorkflowRequest(BaseModel):
    name: Optional[str] = None
    webhook_path: Optional[str] = None


class ProbeWebhookRequest(BaseModel):
    payload: Dict[str, Any] = {}


@router.get("/health")
def bridge_health() -> Dict[str, Any]:
    try:
        return _bridge.health()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/config")
def bridge_config() -> Dict[str, Any]:
    try:
        return _bridge.config()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/public-api-check")
def public_api_check() -> Dict[str, Any]:
    try:
        return _bridge.public_api_check()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/workflows")
def workflows(limit: int = 20) -> Dict[str, Any]:
    try:
        return _bridge.workflows(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/workflows/create-webhook")
def create_webhook(request: CreateWebhookWorkflowRequest) -> Dict[str, Any]:
    try:
        return _bridge.create_webhook_workflow(
            name=request.name,
            webhook_path=request.webhook_path,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/workflows/activate/{workflow_id}")
def activate_workflow(workflow_id: str) -> Dict[str, Any]:
    try:
        return _bridge.activate_workflow(workflow_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/probe/{webhook_path}")
def probe_webhook(webhook_path: str, request: ProbeWebhookRequest) -> Dict[str, Any]:
    try:
        return _bridge.probe_webhook(webhook_path, payload=request.payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/smoke/local-webhook")
def smoke_local_webhook(request: CreateWebhookWorkflowRequest) -> Dict[str, Any]:
    try:
        return _bridge.smoke_local_webhook(
            name=request.name,
            webhook_path=request.webhook_path,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))