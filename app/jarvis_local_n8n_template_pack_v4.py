from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.services.jarvis_n8n_bridge_client import get_jarvis_n8n_bridge_client

MODULE_ID = "jarvis_local_n8n_template_pack_v4"
MODULE_VERSION = "4.0.0"
PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
REGISTRY_PATH = PROJECT_ROOT / "jarvis_stage3_artifacts" / "n8n_templates" / "template_registry_v1.json"

app = FastAPI(title="Jarvis Local n8n Template Pack v4", version=MODULE_VERSION)
bridge = get_jarvis_n8n_bridge_client()


class TemplateCreateRequest(BaseModel):
    template_id: str
    name: Optional[str] = None
    webhook_path: Optional[str] = None
    activate: bool = False


class TemplateSmokeRequest(BaseModel):
    template_id: str
    name: Optional[str] = None
    webhook_path: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


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


def load_registry() -> Dict[str, Any]:
    if not REGISTRY_PATH.exists():
        raise RuntimeError(f"Template registry not found: {REGISTRY_PATH}")
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("Template registry is not a dict")
    if not isinstance(data.get("templates"), list):
        raise RuntimeError("Template registry has no templates list")
    return data


def templates_map() -> Dict[str, Dict[str, Any]]:
    registry = load_registry()
    result: Dict[str, Dict[str, Any]] = {}
    for item in registry.get("templates", []):
        if isinstance(item, dict) and item.get("id"):
            result[str(item["id"])] = item
    return result


def safe_slug(value: str) -> str:
    s = value.strip().lower()
    s = re.sub(r"[^a-z0-9\-]+", "-", s)
    s = re.sub(r"\-+", "-", s)
    s = s.strip("-")
    return s or "jarvis"


def unique_suffix() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def materialize_single_webhook_template(template: Dict[str, Any], *, name: Optional[str], webhook_path: Optional[str]) -> Dict[str, str]:
    name_prefix = safe_slug(str(template.get("name_prefix") or template["id"]))
    path_prefix = safe_slug(str(template.get("path_prefix") or template["id"]))
    suffix = unique_suffix()

    final_name = safe_slug(name) if name else f"{name_prefix}-{suffix}"
    final_path = safe_slug(webhook_path) if webhook_path else f"{path_prefix}-{suffix}"

    return {
        "workflow_name": final_name,
        "webhook_path": final_path,
    }


def workflow_items(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    body = raw.get("body", {})
    if isinstance(body, dict):
        inner = body.get("body", {})
        if isinstance(inner, dict) and isinstance(inner.get("data"), list):
            return inner["data"]
    return []


def workflow_webhook_path(wf: Dict[str, Any]) -> Optional[str]:
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
        "registry_path": str(REGISTRY_PATH),
    }


@app.get("/__whoami")
def whoami() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": MODULE_ID,
        "version": MODULE_VERSION,
        "timestamp": int(time.time()),
        "marker": "TEMPLATE_PACK_V4_ACTIVE",
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
        "has_templates": "/api/jarvis/n8n/templates" in route_paths(),
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


@app.get("/api/jarvis/n8n/templates")
def templates() -> Dict[str, Any]:
    registry = load_registry()
    return {
        "status": "ok",
        "service": MODULE_ID,
        "registry_id": registry.get("registry_id"),
        "registry_version": registry.get("version"),
        "count": len(registry.get("templates", [])),
        "templates": registry.get("templates", []),
    }


@app.get("/api/jarvis/n8n/templates/{template_id}")
def template_detail(template_id: str) -> Dict[str, Any]:
    mapping = templates_map()
    template = mapping.get(template_id)
    if not template:
        raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")
    return {
        "status": "ok",
        "service": MODULE_ID,
        "template": template,
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
        items = workflow_items(raw)
        active = [w for w in items if bool(w.get("active"))]
        inactive = [w for w in items if not bool(w.get("active"))]
        recent = [
            {
                "id": w.get("id"),
                "name": w.get("name"),
                "active": w.get("active"),
                "updatedAt": w.get("updatedAt"),
                "createdAt": w.get("createdAt"),
                "webhook_path": workflow_webhook_path(w),
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
        items = workflow_items(wf)

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


@app.post("/api/jarvis/n8n/templates/create")
def templates_create(request: TemplateCreateRequest) -> Dict[str, Any]:
    mapping = templates_map()
    template = mapping.get(request.template_id)
    if not template:
        raise HTTPException(status_code=404, detail=f"Template not found: {request.template_id}")

    kind = str(template.get("kind") or "")
    if kind != "single_webhook":
        raise HTTPException(status_code=400, detail=f"Unsupported template kind: {kind}")

    instance = materialize_single_webhook_template(
        template,
        name=request.name,
        webhook_path=request.webhook_path,
    )

    try:
        created = bridge.create_webhook_workflow(
            name=instance["workflow_name"],
            webhook_path=instance["webhook_path"],
        )
        workflow_id = (((created.get("body") or {}).get("workflow_id")))
        return {
            "status": "ok",
            "service": MODULE_ID,
            "template": template,
            "instance": instance,
            "create": created,
            "workflow_id": workflow_id,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/jarvis/n8n/templates/create-and-activate")
def templates_create_and_activate(request: TemplateCreateRequest) -> Dict[str, Any]:
    created = templates_create(request)
    workflow_id = created.get("workflow_id")
    if not workflow_id:
        raise HTTPException(status_code=500, detail="workflow_id missing after create")

    try:
        activated = bridge.activate_workflow(str(workflow_id))
        return {
            **created,
            "activate": activated,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/jarvis/n8n/templates/smoke")
def templates_smoke(request: TemplateSmokeRequest) -> Dict[str, Any]:
    create_req = TemplateCreateRequest(
        template_id=request.template_id,
        name=request.name,
        webhook_path=request.webhook_path,
        activate=True,
    )
    created = templates_create(create_req)
    workflow_id = created.get("workflow_id")
    instance = created.get("instance") or {}
    webhook_path = instance.get("webhook_path")

    if not workflow_id or not webhook_path:
        raise HTTPException(status_code=500, detail="workflow_id or webhook_path missing after create")

    try:
        activated = bridge.activate_workflow(str(workflow_id))
        time.sleep(4)
        probe = bridge.probe_webhook(
            str(webhook_path),
            payload=request.payload or {
                "source": MODULE_ID,
                "message": "hello from template smoke",
                "template_id": request.template_id,
            },
        )
        return {
            **created,
            "activate": activated,
            "probe": probe,
        }
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