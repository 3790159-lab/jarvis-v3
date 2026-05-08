from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.services.jarvis_n8n_bridge_client import get_jarvis_n8n_bridge_client

MODULE_ID = "jarvis_local_n8n_gateway_identity_v3"
MODULE_VERSION = "3.0.0"

app = FastAPI(title="Jarvis Local n8n Gateway Identity v3", version=MODULE_VERSION)
bridge = get_jarvis_n8n_bridge_client()


class CreateWebhookWorkflowRequest(BaseModel):
    name: Optional[str] = None
    webhook_path: Optional[str] = None


class ProbeWebhookRequest(BaseModel):
    payload: Dict[str, Any] = Field(default_factory=dict)


def route_paths() -> List[str]:
    paths: List[str] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if path:
            paths.append(path)
    return sorted(set(paths))


def _workflow_items(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    body = raw.get("body", {})
    if isinstance(body, dict):
        inner = body.get("body", {})
        if isinstance(inner, dict) and isinstance(inner.get("data"), list):
            return inner["data"]
    return []


def _workflow_webhook_path(wf: Dict[str, Any]) -> Optional[str]:
    nodes = wf.get("nodes")
    if not isinstance(nodes, list):
        return None
    for node in nodes:
        if isinstance(node, dict) and str(node.get("type")) == "n8n-nodes-base.webhook":
            params = node.get("parameters") or {}
            if isinstance(params, dict):
                return params.get("path")
    return None


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "healthy",
        "service": MODULE_ID,
        "version": MODULE_VERSION,
        "timestamp": int(time.time()),
        "bridge_base_url": bridge.bridge_base_url,
        "routes": len(route_paths()),
    }


@app.get("/__whoami")
def whoami() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": MODULE_ID,
        "version": MODULE_VERSION,
        "timestamp": int(time.time()),
        "marker": "IDENTITY_V3_ACTIVE",
        "routes": route_paths(),
    }


@app.get("/api/jarvis/n8n/version")
def version() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": MODULE_ID,
        "version": MODULE_VERSION,
        "timestamp": int(time.time()),
    }


@app.get("/api/jarvis/n8n/ping")
def ping() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": MODULE_ID,
        "version": MODULE_VERSION,
        "timestamp": int(time.time()),
        "has_bridge_health": "/api/jarvis/n8n/health" in route_paths(),
        "has_status_all": "/api/jarvis/n8n/status/all" in route_paths(),
        "has_workflows_summary": "/api/jarvis/n8n/workflows/summary" in route_paths(),
    }


@app.get("/api/jarvis/n8n/_routes")
def routes() -> Dict[str, Any]:
    paths = route_paths()
    return {
        "status": "ok",
        "service": MODULE_ID,
        "paths": paths,
        "jarvis_n8n_paths": [p for p in paths if p.startswith("/api/jarvis/n8n")],
    }


@app.get("/api/jarvis/n8n/health")
def bridge_health() -> Dict[str, Any]:
    try:
        return bridge.health()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/config")
def bridge_config() -> Dict[str, Any]:
    try:
        return bridge.config()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/public-api-check")
def public_api_check() -> Dict[str, Any]:
    try:
        return bridge.public_api_check()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/workflows")
def workflows(limit: int = 20) -> Dict[str, Any]:
    try:
        return bridge.workflows(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/workflows/summary")
def workflows_summary(limit: int = 20) -> Dict[str, Any]:
    try:
        raw = bridge.workflows(limit=limit)
        items = _workflow_items(raw)
        active = [w for w in items if bool(w.get("active"))]
        inactive = [w for w in items if not bool(w.get("active"))]
        recent = [
            {
                "id": w.get("id"),
                "name": w.get("name"),
                "active": w.get("active"),
                "updatedAt": w.get("updatedAt"),
                "createdAt": w.get("createdAt"),
                "webhook_path": _workflow_webhook_path(w),
                "triggerCount": w.get("triggerCount"),
            }
            for w in items
        ]
        return {
            "status": "ok",
            "service": MODULE_ID,
            "count_total": len(items),
            "count_active": len(active),
            "count_inactive": len(inactive),
            "recent": recent,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/status/all")
def status_all(limit: int = 20) -> Dict[str, Any]:
    try:
        gw = health()
        ping_data = ping()
        bh = bridge.health()
        bc = bridge.config()
        api = bridge.public_api_check()
        wf = bridge.workflows(limit=limit)
        items = _workflow_items(wf)

        bridge_body = bh.get("body", {}) if isinstance(bh, dict) else {}
        n8n_main_readiness = bridge_body.get("n8n_main_readiness", {}) if isinstance(bridge_body, dict) else {}

        return {
            "status": "ok",
            "service": MODULE_ID,
            "gateway": gw,
            "ping": ping_data,
            "bridge_health": bh,
            "bridge_config": bc,
            "public_api_check": api,
            "workflow_counts": {
                "total": len(items),
                "active": len([w for w in items if bool(w.get("active"))]),
                "inactive": len([w for w in items if not bool(w.get("active"))]),
            },
            "all_core_ok": (
                gw.get("status") == "healthy"
                and ping_data.get("status") == "ok"
                and bool(n8n_main_readiness.get("ok"))
                and bool((api.get("body") or {}).get("ok"))
            ),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/jarvis/n8n/workflows/create-webhook")
def create_webhook(request: CreateWebhookWorkflowRequest) -> Dict[str, Any]:
    try:
        return bridge.create_webhook_workflow(
            name=request.name,
            webhook_path=request.webhook_path,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/jarvis/n8n/workflows/activate/{workflow_id}")
def activate_workflow(workflow_id: str) -> Dict[str, Any]:
    try:
        return bridge.activate_workflow(workflow_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/jarvis/n8n/probe/{webhook_path}")
def probe_webhook(webhook_path: str, request: ProbeWebhookRequest) -> Dict[str, Any]:
    try:
        return bridge.probe_webhook(webhook_path, payload=request.payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/jarvis/n8n/smoke/local-webhook")
def smoke_local_webhook(request: CreateWebhookWorkflowRequest) -> Dict[str, Any]:
    try:
        return bridge.smoke_local_webhook(
            name=request.name,
            webhook_path=request.webhook_path,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))