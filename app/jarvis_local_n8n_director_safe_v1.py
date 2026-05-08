from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.jarvis_n8n_bridge_client import get_jarvis_n8n_bridge_client

MODULE_ID = "jarvis_local_n8n_director_safe_v1"
MODULE_VERSION = "1.0.0"

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
TPL_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "n8n_templates"
REGISTRY_PATH = TPL_DIR / "director_safe_registry_v1.json"
MEMORY_PATH = TPL_DIR / "director_safe_memory_v1.json"

MAX_RECENT_EVENTS = 200

app = FastAPI(title="Jarvis Local n8n Director Safe v1", version=MODULE_VERSION)
bridge = get_jarvis_n8n_bridge_client()


class RegisterTemplateRequest(BaseModel):
    id: str
    title: str
    description: str = ""
    purpose: str = ""
    tags: List[str] = Field(default_factory=list)
    path_prefix: str = ""
    name_prefix: str = ""
    smoke_payload: Dict[str, Any] = Field(default_factory=dict)


class TemplateRunRequest(BaseModel):
    template_id: str
    name: Optional[str] = None
    webhook_path: Optional[str] = None
    activate: bool = False


class TemplateSmokeRequest(BaseModel):
    template_id: str
    name: Optional[str] = None
    webhook_path: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def safe_slug(value: str) -> str:
    s = value.strip().lower()
    s = re.sub(r"[^a-z0-9\-]+", "-", s)
    s = re.sub(r"\-+", "-", s)
    s = s.strip("-")
    return s or "jarvis"


def route_paths() -> List[str]:
    paths: List[str] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if path:
            paths.append(path)
    return sorted(set(paths))


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def backup_file(path: Path) -> None:
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)


def save_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    ensure_parent(path)
    backup_file(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_registry() -> Dict[str, Any]:
    if not REGISTRY_PATH.exists():
        raise RuntimeError(f"Registry not found: {REGISTRY_PATH}")
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("Registry is not a dict")
    if not isinstance(data.get("templates"), list):
        raise RuntimeError("Registry has no templates list")
    return data


def load_memory() -> Dict[str, Any]:
    if not MEMORY_PATH.exists():
        data = {
            "memory_id": "jarvis_n8n_director_safe_memory_v1",
            "version": 1,
            "template_stats": {},
            "recent_events": []
        }
        save_json_atomic(MEMORY_PATH, data)
        return data
    data = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("Memory is not a dict")
    data.setdefault("template_stats", {})
    data.setdefault("recent_events", [])
    return data


def templates_map() -> Dict[str, Dict[str, Any]]:
    registry = load_registry()
    result: Dict[str, Dict[str, Any]] = {}
    for item in registry.get("templates", []):
        if isinstance(item, dict) and item.get("id"):
            result[str(item["id"])] = item
    return result


def update_memory(template_id: str, *, action: str, ok: bool, workflow_id: Optional[str] = None, webhook_path: Optional[str] = None, error: str = "") -> None:
    memory = load_memory()
    stats = memory.setdefault("template_stats", {})
    row = stats.setdefault(template_id, {
        "success_count": 0,
        "failure_count": 0,
        "register_count": 0,
        "create_count": 0,
        "activate_count": 0,
        "smoke_count": 0,
        "last_success_ts": None,
        "last_failure_ts": None,
        "last_workflow_id": None,
        "last_webhook_path": None
    })

    if action == "register":
        row["register_count"] += 1
    elif action == "create":
        row["create_count"] += 1
    elif action == "activate":
        row["activate_count"] += 1
    elif action == "smoke":
        row["smoke_count"] += 1

    if ok:
        row["success_count"] += 1
        row["last_success_ts"] = now_iso()
    else:
        row["failure_count"] += 1
        row["last_failure_ts"] = now_iso()

    if workflow_id:
        row["last_workflow_id"] = workflow_id
    if webhook_path:
        row["last_webhook_path"] = webhook_path

    events = memory.setdefault("recent_events", [])
    events.append({
        "ts": now_iso(),
        "template_id": template_id,
        "action": action,
        "ok": ok,
        "workflow_id": workflow_id,
        "webhook_path": webhook_path,
        "error": error
    })
    memory["recent_events"] = events[-MAX_RECENT_EVENTS:]

    save_json_atomic(MEMORY_PATH, memory)


def recommend_templates(*, purpose: str = "", tag: str = "") -> List[Dict[str, Any]]:
    mapping = templates_map()
    memory = load_memory()
    stats_map = memory.get("template_stats", {})

    purpose_slug = safe_slug(purpose) if purpose else ""
    tag_slug = safe_slug(tag) if tag else ""

    rows: List[Dict[str, Any]] = []
    for template_id, tpl in mapping.items():
        tpl_purpose = safe_slug(str(tpl.get("purpose") or ""))
        tpl_tags = [safe_slug(t) for t in tpl.get("tags", [])]
        stats = stats_map.get(template_id, {})

        score = 0
        if purpose_slug and tpl_purpose == purpose_slug:
            score += 10
        if tag_slug and tag_slug in tpl_tags:
            score += 5
        score += int(stats.get("success_count", 0)) * 2
        score -= int(stats.get("failure_count", 0)) * 3

        rows.append({
            "score": score,
            "template": tpl,
            "stats": stats
        })

    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows


def make_instance(template: Dict[str, Any], *, name: Optional[str], webhook_path: Optional[str]) -> Dict[str, str]:
    suffix = time.strftime("%Y%m%d-%H%M%S")
    name_prefix = safe_slug(str(template.get("name_prefix") or template["id"]))
    path_prefix = safe_slug(str(template.get("path_prefix") or template["id"]))

    workflow_name = safe_slug(name) if name else f"{name_prefix}-{suffix}"
    webhook = safe_slug(webhook_path) if webhook_path else f"{path_prefix}-{suffix}"

    return {
        "workflow_name": workflow_name,
        "webhook_path": webhook
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
                path = params.get("path")
                if isinstance(path, str) and path.strip():
                    return path.strip("/")
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
        "memory_path": str(MEMORY_PATH)
    }


@app.get("/__whoami")
def whoami() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": MODULE_ID,
        "version": MODULE_VERSION,
        "marker": "DIRECTOR_SAFE_V1_ACTIVE",
        "routes": route_paths()
    }


@app.get("/api/jarvis/n8n/version")
def version() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": MODULE_ID,
        "version": MODULE_VERSION
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
                "triggerCount": w.get("triggerCount")
            }
            for w in items
        ]
        return {
            "status": "ok",
            "service": MODULE_ID,
            "count_total": len(items),
            "count_active": len(active),
            "count_inactive": len(inactive),
            "recent": recent
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/status/all")
def status_all(limit: int = 20) -> Dict[str, Any]:
    try:
        gw = health()
        bh = bridge.health()
        bc = bridge.config()
        api = bridge.public_api_check()
        wf = bridge.workflows(limit=limit)
        items = workflow_items(wf)

        bridge_body = bh.get("body", {}) if isinstance(bh, dict) else {}
        readiness = bridge_body.get("n8n_main_readiness", {}) if isinstance(bridge_body, dict) else {}

        return {
            "status": "ok",
            "service": MODULE_ID,
            "gateway": gw,
            "bridge_health": bh,
            "bridge_config": bc,
            "public_api_check": api,
            "workflow_counts": {
                "total": len(items),
                "active": len([w for w in items if bool(w.get("active"))]),
                "inactive": len([w for w in items if not bool(w.get("active"))])
            },
            "all_core_ok": (
                gw.get("status") == "healthy"
                and bool(readiness.get("ok"))
                and bool((api.get("body") or {}).get("ok"))
            )
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/templates")
def templates() -> Dict[str, Any]:
    try:
        registry = load_registry()
        return {
            "status": "ok",
            "service": MODULE_ID,
            "registry_id": registry.get("registry_id"),
            "registry_version": registry.get("version"),
            "count": len(registry.get("templates", [])),
            "templates": registry.get("templates", [])
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/templates/{template_id}")
def template_detail(template_id: str) -> Dict[str, Any]:
    try:
        mapping = templates_map()
        tpl = mapping.get(template_id)
        if not tpl:
            raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")
        return {
            "status": "ok",
            "service": MODULE_ID,
            "template": tpl
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/memory")
def memory() -> Dict[str, Any]:
    try:
        return {
            "status": "ok",
            "service": MODULE_ID,
            "memory": load_memory()
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/templates/recommend")
def templates_recommend(
    purpose: str = Query(default=""),
    tag: str = Query(default="")
) -> Dict[str, Any]:
    try:
        return {
            "status": "ok",
            "service": MODULE_ID,
            "purpose": purpose,
            "tag": tag,
            "recommendations": recommend_templates(purpose=purpose, tag=tag)
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/jarvis/n8n/templates/register")
def template_register(request: RegisterTemplateRequest) -> Dict[str, Any]:
    try:
        template_id = request.id.strip()
        if not template_id or not re.fullmatch(r"[a-zA-Z0-9_\-]+", template_id):
            raise ValueError("template id must match [a-zA-Z0-9_-]+")
        title = request.title.strip()
        if not title:
            raise ValueError("template title is required")

        entry = {
            "id": template_id,
            "title": title,
            "description": request.description.strip(),
            "purpose": request.purpose.strip(),
            "tags": [safe_slug(t) for t in request.tags if str(t).strip()],
            "path_prefix": safe_slug(request.path_prefix) if request.path_prefix else safe_slug(template_id),
            "name_prefix": safe_slug(request.name_prefix) if request.name_prefix else safe_slug(template_id),
            "smoke_payload": request.smoke_payload
        }

        registry = load_registry()
        templates = registry.get("templates", [])
        replaced = False
        for idx, item in enumerate(templates):
            if isinstance(item, dict) and item.get("id") == template_id:
                templates[idx] = entry
                replaced = True
                break
        if not replaced:
            templates.append(entry)

        registry["templates"] = templates
        save_json_atomic(REGISTRY_PATH, registry)
        update_memory(template_id, action="register", ok=True)

        return {
            "status": "ok",
            "service": MODULE_ID,
            "created_new": (not replaced),
            "template": entry
        }
    except Exception as exc:
        try:
            update_memory(request.id, action="register", ok=False, error=str(exc))
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/jarvis/n8n/templates/create")
def template_create(request: TemplateRunRequest) -> Dict[str, Any]:
    try:
        mapping = templates_map()
        tpl = mapping.get(request.template_id)
        if not tpl:
            raise HTTPException(status_code=404, detail=f"Template not found: {request.template_id}")

        instance = make_instance(tpl, name=request.name, webhook_path=request.webhook_path)
        created = bridge.create_webhook_workflow(
            name=instance["workflow_name"],
            webhook_path=instance["webhook_path"]
        )
        workflow_id = ((created.get("body") or {}).get("workflow_id"))

        update_memory(
            request.template_id,
            action="create",
            ok=True,
            workflow_id=workflow_id,
            webhook_path=instance["webhook_path"]
        )

        return {
            "status": "ok",
            "service": MODULE_ID,
            "template": tpl,
            "instance": instance,
            "create": created,
            "workflow_id": workflow_id
        }
    except HTTPException:
        raise
    except Exception as exc:
        try:
            update_memory(request.template_id, action="create", ok=False, error=str(exc))
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/jarvis/n8n/templates/create-and-activate")
def template_create_and_activate(request: TemplateRunRequest) -> Dict[str, Any]:
    created = template_create(request)
    workflow_id = created.get("workflow_id")
    instance = created.get("instance") or {}

    if not workflow_id:
        raise HTTPException(status_code=500, detail="workflow_id missing after create")

    try:
        activated = bridge.activate_workflow(str(workflow_id))
        update_memory(
            request.template_id,
            action="activate",
            ok=True,
            workflow_id=workflow_id,
            webhook_path=instance.get("webhook_path")
        )
        return {
            **created,
            "activate": activated
        }
    except Exception as exc:
        try:
            update_memory(request.template_id, action="activate", ok=False, workflow_id=workflow_id, error=str(exc))
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/jarvis/n8n/templates/smoke")
def template_smoke(request: TemplateSmokeRequest) -> Dict[str, Any]:
    create_req = TemplateRunRequest(
        template_id=request.template_id,
        name=request.name,
        webhook_path=request.webhook_path,
        activate=True
    )
    created = template_create(create_req)
    workflow_id = created.get("workflow_id")
    instance = created.get("instance") or {}
    webhook_path = instance.get("webhook_path")
    template = created.get("template") or {}

    if not workflow_id:
        raise HTTPException(status_code=500, detail="workflow_id missing after create")

    try:
        activated = bridge.activate_workflow(str(workflow_id))

        payload = {}
        default_smoke = template.get("smoke_payload") or {}
        if isinstance(default_smoke, dict):
            payload.update(default_smoke)
        payload.update(request.payload or {})

        time.sleep(4)
        probe = bridge.probe_webhook(str(webhook_path), payload=payload)

        update_memory(
            request.template_id,
            action="smoke",
            ok=True,
            workflow_id=workflow_id,
            webhook_path=webhook_path
        )

        return {
            **created,
            "activate": activated,
            "probe": probe
        }
    except Exception as exc:
        try:
            update_memory(request.template_id, action="smoke", ok=False, workflow_id=workflow_id, webhook_path=webhook_path, error=str(exc))
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(exc))