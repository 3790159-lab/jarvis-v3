from __future__ import annotations

import json
import re
import shutil
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.jarvis_n8n_bridge_client import get_jarvis_n8n_bridge_client

MODULE_ID = "jarvis_local_n8n_director_pro_core"
MODULE_VERSION = "2.0.0"

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
TPL_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "n8n_templates"
REGISTRY_PATH = TPL_DIR / "director_pro_core_registry.json"
MEMORY_PATH = TPL_DIR / "director_pro_core_memory.json"
MAX_RECENT_EVENTS = 500
DEFAULT_WORKFLOW_SCAN_LIMIT = 200
DEFAULT_CLEANUP_OLDER_THAN_HOURS = 6
DEFAULT_CLEANUP_KEEP_PER_TEMPLATE = 3

BASELINE_TEMPLATES = [
    {
        "id": "webhook_inbox_v5",
        "title": "Webhook Inbox v5",
        "description": "General intake webhook for Jarvis events.",
        "purpose": "intake",
        "tags": ["stable", "intake", "webhook"],
        "path_prefix": "jarvis-inbox",
        "name_prefix": "jarvis-inbox",
        "smoke_payload": {"source": "webhook_inbox_v5", "message": "hello from webhook inbox v5"},
    },
    {
        "id": "operator_ack_v5",
        "title": "Operator Ack v5",
        "description": "Operator confirmation webhook.",
        "purpose": "operator",
        "tags": ["stable", "operator", "ack", "webhook"],
        "path_prefix": "jarvis-operator-ack",
        "name_prefix": "jarvis-operator-ack",
        "smoke_payload": {"source": "operator_ack_v5", "message": "hello from operator ack v5"},
    },
    {
        "id": "audit_hook_v5",
        "title": "Audit Hook v5",
        "description": "Audit/logging webhook.",
        "purpose": "audit",
        "tags": ["stable", "audit", "logging", "webhook"],
        "path_prefix": "jarvis-audit",
        "name_prefix": "jarvis-audit",
        "smoke_payload": {"source": "audit_hook_v5", "message": "hello from audit hook v5"},
    },
]

DEFAULT_MEMORY = {
    "memory_id": "jarvis_n8n_director_pro_core_memory",
    "version": 2,
    "template_stats": {},
    "recent_events": [],
    "workflow_inventory": {
        "last_synced_ts": None,
        "total": 0,
        "active": 0,
        "inactive": 0,
        "managed": 0,
        "unmanaged": 0,
        "templates": {},
    },
    "service_stats": {
        "total_success": 0,
        "total_failure": 0,
        "last_success_ts": None,
        "last_failure_ts": None,
    },
}

app = FastAPI(title="Jarvis Local n8n Director Pro Core", version=MODULE_VERSION)
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
    reuse_existing: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TemplateSmokeRequest(BaseModel):
    template_id: str
    name: Optional[str] = None
    webhook_path: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    reuse_existing: bool = False
    wait_seconds: int = 4


class ProbeWebhookRequest(BaseModel):
    payload: Dict[str, Any] = Field(default_factory=dict)


class GoalWorkflowRequest(BaseModel):
    goal: str
    purpose: str = ""
    tag: str = ""
    name: Optional[str] = None
    webhook_path: Optional[str] = None
    activate: bool = True
    reuse_existing: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class CleanupRequest(BaseModel):
    older_than_hours: int = DEFAULT_CLEANUP_OLDER_THAN_HOURS
    max_keep_per_template: int = DEFAULT_CLEANUP_KEEP_PER_TEMPLATE
    dry_run: bool = True
    deactivate_if_supported: bool = True
    delete_if_supported: bool = False
    limit: int = DEFAULT_WORKFLOW_SCAN_LIMIT


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def safe_slug(value: str) -> str:
    s = (value or "").strip().lower()
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


def backup_file(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    return backup


def save_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    ensure_parent(path)
    backup_file(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def json_clone(data: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(data, ensure_ascii=False))


def parse_json_file(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path.exists():
        save_json_atomic(path, json_clone(default))
        return json_clone(default)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
    except Exception:
        corrupt = path.with_suffix(path.suffix + f".corrupt-{int(time.time())}.bak")
        shutil.copy2(path, corrupt)
    save_json_atomic(path, json_clone(default))
    return json_clone(default)


def normalize_template(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    template_id = str(item.get("id") or "").strip()
    title = str(item.get("title") or "").strip()
    if not template_id or not title:
        return None
    description = str(item.get("description") or "").strip()
    purpose = safe_slug(str(item.get("purpose") or ""))
    tags = [safe_slug(str(t)) for t in item.get("tags", []) if str(t).strip()]
    smoke_payload = item.get("smoke_payload") if isinstance(item.get("smoke_payload"), dict) else {}
    path_prefix = safe_slug(str(item.get("path_prefix") or template_id))
    name_prefix = safe_slug(str(item.get("name_prefix") or template_id))
    return {
        "id": template_id,
        "title": title,
        "description": description,
        "purpose": purpose,
        "tags": tags,
        "path_prefix": path_prefix,
        "name_prefix": name_prefix,
        "smoke_payload": smoke_payload,
    }


def load_registry() -> Dict[str, Any]:
    registry = parse_json_file(
        REGISTRY_PATH,
        {"registry_id": "jarvis_n8n_director_pro_core_registry", "version": 2, "templates": BASELINE_TEMPLATES},
    )
    registry_id = str(registry.get("registry_id") or "jarvis_n8n_director_pro_core_registry")
    version = int(registry.get("version") or 2)
    template_map: Dict[str, Dict[str, Any]] = {}
    for raw in registry.get("templates", []):
        if isinstance(raw, dict):
            normalized = normalize_template(raw)
            if normalized:
                template_map[normalized["id"]] = normalized
    for raw in BASELINE_TEMPLATES:
        normalized = normalize_template(raw)
        if normalized:
            template_map[normalized["id"]] = normalized
    merged = {
        "registry_id": registry_id,
        "version": max(version, 2),
        "templates": sorted(template_map.values(), key=lambda x: x["id"]),
    }
    if merged != registry:
        save_json_atomic(REGISTRY_PATH, merged)
    return merged


def load_memory() -> Dict[str, Any]:
    memory = parse_json_file(MEMORY_PATH, DEFAULT_MEMORY)
    memory["memory_id"] = str(memory.get("memory_id") or DEFAULT_MEMORY["memory_id"])
    memory["version"] = max(int(memory.get("version") or 1), 2)
    memory.setdefault("template_stats", {})
    memory.setdefault("recent_events", [])
    memory.setdefault("workflow_inventory", json_clone(DEFAULT_MEMORY["workflow_inventory"]))
    memory.setdefault("service_stats", json_clone(DEFAULT_MEMORY["service_stats"]))
    inventory = memory["workflow_inventory"]
    for key, value in DEFAULT_MEMORY["workflow_inventory"].items():
        inventory.setdefault(key, json_clone(value) if isinstance(value, dict) else value)
    service_stats = memory["service_stats"]
    for key, value in DEFAULT_MEMORY["service_stats"].items():
        service_stats.setdefault(key, value)
    return memory


def save_memory(memory: Dict[str, Any]) -> None:
    memory["recent_events"] = memory.get("recent_events", [])[-MAX_RECENT_EVENTS:]
    save_json_atomic(MEMORY_PATH, memory)


def templates_map() -> Dict[str, Dict[str, Any]]:
    registry = load_registry()
    result: Dict[str, Dict[str, Any]] = {}
    for item in registry.get("templates", []):
        if isinstance(item, dict) and item.get("id"):
            result[str(item["id"])] = item
    return result


def ensure_template_stats(memory: Dict[str, Any], template_id: str) -> Dict[str, Any]:
    stats = memory.setdefault("template_stats", {})
    row = stats.setdefault(
        template_id,
        {
            "success_count": 0,
            "failure_count": 0,
            "register_count": 0,
            "render_count": 0,
            "create_count": 0,
            "activate_count": 0,
            "smoke_count": 0,
            "reuse_count": 0,
            "cleanup_count": 0,
            "planner_pick_count": 0,
            "last_success_ts": None,
            "last_failure_ts": None,
            "last_workflow_id": None,
            "last_webhook_path": None,
            "last_workflow_name": None,
        },
    )
    return row


def update_memory(
    template_id: str,
    *,
    action: str,
    ok: bool,
    workflow_id: Optional[str] = None,
    webhook_path: Optional[str] = None,
    workflow_name: Optional[str] = None,
    error: str = "",
    details: Optional[Dict[str, Any]] = None,
) -> None:
    memory = load_memory()
    row = ensure_template_stats(memory, template_id)
    service_stats = memory.setdefault("service_stats", {})
    if action == "register":
        row["register_count"] += 1
    elif action == "render":
        row["render_count"] += 1
    elif action == "create":
        row["create_count"] += 1
    elif action == "activate":
        row["activate_count"] += 1
    elif action == "smoke":
        row["smoke_count"] += 1
    elif action == "reuse":
        row["reuse_count"] += 1
    elif action == "cleanup":
        row["cleanup_count"] += 1
    elif action == "planner_pick":
        row["planner_pick_count"] += 1
    if ok:
        row["success_count"] += 1
        row["last_success_ts"] = now_iso()
        service_stats["total_success"] = int(service_stats.get("total_success", 0)) + 1
        service_stats["last_success_ts"] = row["last_success_ts"]
    else:
        row["failure_count"] += 1
        row["last_failure_ts"] = now_iso()
        service_stats["total_failure"] = int(service_stats.get("total_failure", 0)) + 1
        service_stats["last_failure_ts"] = row["last_failure_ts"]
    if workflow_id:
        row["last_workflow_id"] = workflow_id
    if webhook_path:
        row["last_webhook_path"] = webhook_path
    if workflow_name:
        row["last_workflow_name"] = workflow_name
    events = memory.setdefault("recent_events", [])
    events.append(
        {
            "ts": now_iso(),
            "template_id": template_id,
            "action": action,
            "ok": ok,
            "workflow_id": workflow_id,
            "webhook_path": webhook_path,
            "workflow_name": workflow_name,
            "error": error,
            "details": details or {},
        }
    )
    save_memory(memory)


def workflow_items(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
    if isinstance(raw.get("body"), dict):
        body = raw["body"]
        if isinstance(body.get("body"), dict) and isinstance(body["body"].get("data"), list):
            return body["body"]["data"]
        if isinstance(body.get("data"), list):
            return body["data"]
    if isinstance(raw.get("data"), list):
        return raw["data"]
    return []


def workflow_webhook_path(wf: Dict[str, Any]) -> Optional[str]:
    nodes = wf.get("nodes") if isinstance(wf.get("nodes"), list) else []
    for node in nodes:
        if isinstance(node, dict) and str(node.get("type")) == "n8n-nodes-base.webhook":
            params = node.get("parameters") or {}
            if isinstance(params, dict):
                path = params.get("path")
                if isinstance(path, str) and path.strip():
                    return path.strip("/")
    return None


def workflow_name(wf: Dict[str, Any]) -> str:
    return str(wf.get("name") or "").strip()


def iso_to_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def workflow_updated_dt(wf: Dict[str, Any]) -> Optional[datetime]:
    return iso_to_datetime(wf.get("updatedAt")) or iso_to_datetime(wf.get("createdAt"))


def workflow_age_hours(wf: Dict[str, Any]) -> Optional[float]:
    dt = workflow_updated_dt(wf)
    if not dt:
        return None
    delta = utcnow() - dt
    return round(delta.total_seconds() / 3600.0, 3)


def scan_workflows(limit: int = DEFAULT_WORKFLOW_SCAN_LIMIT) -> List[Dict[str, Any]]:
    raw = bridge.workflows(limit=limit)
    return workflow_items(raw)


def classify_template_for_workflow(wf: Dict[str, Any], mapping: Dict[str, Dict[str, Any]]) -> Optional[str]:
    name_slug = safe_slug(workflow_name(wf))
    path_slug = safe_slug(workflow_webhook_path(wf) or "")
    for template_id, tpl in mapping.items():
        path_prefix = safe_slug(str(tpl.get("path_prefix") or template_id))
        name_prefix = safe_slug(str(tpl.get("name_prefix") or template_id))
        if path_slug and (path_slug == path_prefix or path_slug.startswith(path_prefix + "-")):
            return template_id
        if name_slug and (name_slug == name_prefix or name_slug.startswith(name_prefix + "-")):
            return template_id
    return None


def workflow_summary_row(wf: Dict[str, Any], mapping: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    template_id = classify_template_for_workflow(wf, mapping)
    return {
        "id": wf.get("id"),
        "name": workflow_name(wf),
        "active": bool(wf.get("active")),
        "updatedAt": wf.get("updatedAt"),
        "createdAt": wf.get("createdAt"),
        "triggerCount": wf.get("triggerCount"),
        "webhook_path": workflow_webhook_path(wf),
        "template_id": template_id,
        "managed": bool(template_id),
        "age_hours": workflow_age_hours(wf),
    }


def sync_inventory(limit: int = DEFAULT_WORKFLOW_SCAN_LIMIT) -> Dict[str, Any]:
    mapping = templates_map()
    items = scan_workflows(limit=limit)
    rows = [workflow_summary_row(wf, mapping) for wf in items]
    inventory_templates: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"total": 0, "active": 0, "inactive": 0})
    managed = 0
    unmanaged = 0
    active = 0
    inactive = 0
    for row in rows:
        if row["active"]:
            active += 1
        else:
            inactive += 1
        if row["managed"]:
            managed += 1
            template_bucket = inventory_templates[row["template_id"]]
            template_bucket["total"] += 1
            if row["active"]:
                template_bucket["active"] += 1
            else:
                template_bucket["inactive"] += 1
        else:
            unmanaged += 1
    memory = load_memory()
    memory["workflow_inventory"] = {
        "last_synced_ts": now_iso(),
        "total": len(rows),
        "active": active,
        "inactive": inactive,
        "managed": managed,
        "unmanaged": unmanaged,
        "templates": dict(sorted(inventory_templates.items())),
    }
    save_memory(memory)
    return {
        "status": "ok",
        "service": MODULE_ID,
        "inventory": memory["workflow_inventory"],
        "workflows": rows,
    }


def find_existing_workflow(
    *,
    name: Optional[str],
    webhook_path: Optional[str],
    template: Optional[Dict[str, Any]] = None,
    limit: int = DEFAULT_WORKFLOW_SCAN_LIMIT,
) -> Optional[Dict[str, Any]]:
    target_name = safe_slug(name or "")
    target_path = safe_slug(webhook_path or "")
    mapping = templates_map()
    items = scan_workflows(limit=limit)
    best_match: Optional[Dict[str, Any]] = None
    for wf in items:
        row = workflow_summary_row(wf, mapping)
        row_name = safe_slug(row["name"])
        row_path = safe_slug(row.get("webhook_path") or "")
        if target_name and row_name == target_name:
            if template is None or row["template_id"] in (None, template["id"]):
                best_match = row
                break
        if target_path and row_path == target_path:
            if template is None or row["template_id"] in (None, template["id"]):
                best_match = row
                break
    return best_match


def make_instance(template: Dict[str, Any], *, name: Optional[str], webhook_path: Optional[str]) -> Dict[str, str]:
    suffix = time.strftime("%Y%m%d-%H%M%S")
    name_prefix = safe_slug(str(template.get("name_prefix") or template["id"]))
    path_prefix = safe_slug(str(template.get("path_prefix") or template["id"]))
    workflow_name = safe_slug(name) if name else f"{name_prefix}-{suffix}"
    webhook = safe_slug(webhook_path) if webhook_path else f"{path_prefix}-{suffix}"
    return {"workflow_name": workflow_name, "webhook_path": webhook}


def recommend_templates(*, purpose: str = "", tag: str = "", goal: str = "") -> List[Dict[str, Any]]:
    mapping = templates_map()
    memory = load_memory()
    stats_map = memory.get("template_stats", {})
    purpose_slug = safe_slug(purpose)
    tag_slug = safe_slug(tag)
    goal_slug = safe_slug(goal)
    rows: List[Dict[str, Any]] = []
    for template_id, tpl in mapping.items():
        tpl_purpose = safe_slug(str(tpl.get("purpose") or ""))
        tpl_tags = [safe_slug(t) for t in tpl.get("tags", [])]
        stats = stats_map.get(template_id, {})
        score = 0
        reasons: List[str] = []
        if purpose_slug and tpl_purpose == purpose_slug:
            score += 10
            reasons.append("purpose_match")
        if tag_slug and tag_slug in tpl_tags:
            score += 5
            reasons.append("tag_match")
        if goal_slug:
            if tpl_purpose and tpl_purpose in goal_slug:
                score += 7
                reasons.append("goal_mentions_purpose")
            for tpl_tag in tpl_tags:
                if tpl_tag and tpl_tag in goal_slug:
                    score += 2
                    reasons.append(f"goal_mentions_tag:{tpl_tag}")
            if "audit" in goal_slug and tpl_purpose == "audit":
                score += 5
            if "operator" in goal_slug and tpl_purpose == "operator":
                score += 5
            if "intake" in goal_slug and tpl_purpose == "intake":
                score += 5
            if "custom" in goal_slug and template_id.startswith("custom"):
                score += 4
        score += int(stats.get("success_count", 0)) * 2
        score -= int(stats.get("failure_count", 0)) * 3
        rows.append({"score": score, "template": tpl, "stats": stats, "reasons": sorted(set(reasons))})
    rows.sort(key=lambda x: (x["score"], x["template"]["id"]), reverse=True)
    return rows


def choose_template_for_goal(goal: str, purpose: str = "", tag: str = "") -> Dict[str, Any]:
    recommendations = recommend_templates(purpose=purpose, tag=tag, goal=goal)
    if recommendations:
        chosen = recommendations[0]
        return {
            "goal": goal,
            "purpose": purpose,
            "tag": tag,
            "chosen_template": chosen["template"],
            "score": chosen["score"],
            "reasons": chosen.get("reasons", []),
            "recommendations": recommendations[:5],
        }
    mapping = templates_map()
    fallback = mapping.get("webhook_inbox_v5")
    if fallback:
        return {
            "goal": goal,
            "purpose": purpose,
            "tag": tag,
            "chosen_template": fallback,
            "score": 0,
            "reasons": ["fallback_to_intake"],
            "recommendations": [],
        }
    raise RuntimeError("No templates available")


def template_preview_workflow(instance: Dict[str, str]) -> Dict[str, Any]:
    return {
        "name": instance["workflow_name"],
        "nodes": [
            {
                "name": "Jarvis Webhook",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 1,
                "position": [600, 300],
                "parameters": {
                    "httpMethod": "POST",
                    "path": instance["webhook_path"],
                    "options": {},
                },
            }
        ],
        "connections": {},
        "settings": {},
    }


def bridge_supports(method_name: str) -> bool:
    return callable(getattr(bridge, method_name, None))


def activate_workflow(workflow_id: str) -> Dict[str, Any]:
    if not bridge_supports("activate_workflow"):
        raise RuntimeError("bridge.activate_workflow is not available")
    return bridge.activate_workflow(str(workflow_id))


def create_workflow(name: str, webhook_path: str) -> Dict[str, Any]:
    if not bridge_supports("create_webhook_workflow"):
        raise RuntimeError("bridge.create_webhook_workflow is not available")
    return bridge.create_webhook_workflow(name=name, webhook_path=webhook_path)


def probe_webhook(webhook_path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not bridge_supports("probe_webhook"):
        raise RuntimeError("bridge.probe_webhook is not available")
    return bridge.probe_webhook(webhook_path, payload=payload)


def deactivate_workflow_if_supported(workflow_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    if bridge_supports("deactivate_workflow"):
        return True, "deactivate_workflow", bridge.deactivate_workflow(str(workflow_id))
    return False, "deactivate_workflow_not_supported", None


def delete_workflow_if_supported(workflow_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    if bridge_supports("delete_workflow"):
        return True, "delete_workflow", bridge.delete_workflow(str(workflow_id))
    if bridge_supports("archive_workflow"):
        return True, "archive_workflow", bridge.archive_workflow(str(workflow_id))
    return False, "delete_or_archive_not_supported", None


def create_or_reuse_template_workflow(request: TemplateRunRequest) -> Dict[str, Any]:
    mapping = templates_map()
    tpl = mapping.get(request.template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"Template not found: {request.template_id}")
    instance = make_instance(tpl, name=request.name, webhook_path=request.webhook_path)
    existing = None
    if request.reuse_existing:
        existing = find_existing_workflow(
            name=instance["workflow_name"],
            webhook_path=instance["webhook_path"],
            template=tpl,
        )
    if existing:
        update_memory(
            request.template_id,
            action="reuse",
            ok=True,
            workflow_id=str(existing.get("id") or ""),
            webhook_path=existing.get("webhook_path"),
            workflow_name=existing.get("name"),
            details={"reason": "exact_name_or_webhook_match"},
        )
        return {
            "status": "ok",
            "service": MODULE_ID,
            "template": tpl,
            "instance": instance,
            "reused_existing": True,
            "existing": existing,
            "workflow_id": existing.get("id"),
        }
    created = create_workflow(name=instance["workflow_name"], webhook_path=instance["webhook_path"])
    workflow_id = (created.get("body") or {}).get("workflow_id")
    update_memory(
        request.template_id,
        action="create",
        ok=True,
        workflow_id=workflow_id,
        webhook_path=instance["webhook_path"],
        workflow_name=instance["workflow_name"],
    )
    return {
        "status": "ok",
        "service": MODULE_ID,
        "template": tpl,
        "instance": instance,
        "reused_existing": False,
        "create": created,
        "workflow_id": workflow_id,
    }


def activate_template_workflow(template_id: str, workflow_id: str, workflow_name: str, webhook_path: str, already_active: bool) -> Dict[str, Any]:
    if already_active:
        update_memory(
            template_id,
            action="activate",
            ok=True,
            workflow_id=workflow_id,
            webhook_path=webhook_path,
            workflow_name=workflow_name,
            details={"skipped": True, "reason": "already_active"},
        )
        return {"skipped": True, "reason": "already_active"}
    activated = activate_workflow(workflow_id)
    update_memory(
        template_id,
        action="activate",
        ok=True,
        workflow_id=workflow_id,
        webhook_path=webhook_path,
        workflow_name=workflow_name,
    )
    return activated


def cleanup_candidates(older_than_hours: int, max_keep_per_template: int, limit: int) -> Dict[str, Any]:
    mapping = templates_map()
    items = scan_workflows(limit=limit)
    rows = [workflow_summary_row(wf, mapping) for wf in items]
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    unmanaged: List[Dict[str, Any]] = []
    for row in rows:
        if row["template_id"]:
            grouped[row["template_id"]].append(row)
        else:
            unmanaged.append(row)
    candidates: List[Dict[str, Any]] = []
    for template_id, bucket in grouped.items():
        bucket.sort(key=lambda r: (r.get("updatedAt") or "", r.get("createdAt") or "", r.get("id") or ""), reverse=True)
        for idx, row in enumerate(bucket):
            age = row.get("age_hours")
            if idx >= max_keep_per_template and (age is None or age >= older_than_hours):
                candidates.append({**row, "cleanup_reason": "keep_limit_exceeded"})
    candidates.sort(key=lambda r: (r.get("template_id") or "", r.get("updatedAt") or ""))
    return {
        "status": "ok",
        "service": MODULE_ID,
        "policy": {
            "older_than_hours": older_than_hours,
            "max_keep_per_template": max_keep_per_template,
            "limit": limit,
        },
        "inventory": sync_inventory(limit=limit)["inventory"],
        "candidates": candidates,
        "unmanaged_count": len(unmanaged),
    }


@app.get("/health")
def health() -> Dict[str, Any]:
    memory = load_memory()
    inventory = memory.get("workflow_inventory", {})
    return {
        "status": "healthy",
        "service": MODULE_ID,
        "version": MODULE_VERSION,
        "timestamp": int(time.time()),
        "bridge_base_url": getattr(bridge, "bridge_base_url", None),
        "routes": len(route_paths()),
        "registry_path": str(REGISTRY_PATH),
        "memory_path": str(MEMORY_PATH),
        "inventory_last_synced_ts": inventory.get("last_synced_ts"),
    }


@app.get("/__whoami")
def whoami() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": MODULE_ID,
        "version": MODULE_VERSION,
        "marker": "DIRECTOR_PRO_CORE_ACTIVE",
        "routes": route_paths(),
    }


@app.get("/api/jarvis/n8n/version")
def version() -> Dict[str, Any]:
    return {"status": "ok", "service": MODULE_ID, "version": MODULE_VERSION}


@app.get("/api/jarvis/n8n/abilities")
def abilities() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": MODULE_ID,
        "abilities": [
            "health_and_status",
            "workflow_summary",
            "workflow_inventory",
            "workflow_reuse",
            "template_registry",
            "template_registration",
            "template_render_preview",
            "template_create",
            "template_create_and_activate",
            "template_smoke",
            "template_memory",
            "template_recommendations",
            "planner_recommend",
            "planner_create_from_goal",
            "cleanup_report",
            "cleanup_apply",
            "webhook_probe",
            "memory_sync",
        ],
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
def workflows(limit: int = DEFAULT_WORKFLOW_SCAN_LIMIT) -> Dict[str, Any]:
    try:
        return bridge.workflows(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/workflows/summary")
def workflows_summary(limit: int = DEFAULT_WORKFLOW_SCAN_LIMIT) -> Dict[str, Any]:
    try:
        mapping = templates_map()
        items = scan_workflows(limit=limit)
        rows = [workflow_summary_row(wf, mapping) for wf in items]
        return {
            "status": "ok",
            "service": MODULE_ID,
            "count_total": len(rows),
            "count_active": len([r for r in rows if r["active"]]),
            "count_inactive": len([r for r in rows if not r["active"]]),
            "recent": rows,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/workflows/inventory")
def workflows_inventory(limit: int = DEFAULT_WORKFLOW_SCAN_LIMIT) -> Dict[str, Any]:
    try:
        return sync_inventory(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/workflows/find")
def workflows_find(name: str = "", webhook_path: str = "", limit: int = DEFAULT_WORKFLOW_SCAN_LIMIT) -> Dict[str, Any]:
    try:
        existing = find_existing_workflow(name=name, webhook_path=webhook_path, template=None, limit=limit)
        return {
            "status": "ok",
            "service": MODULE_ID,
            "found": bool(existing),
            "existing": existing,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/status/all")
def status_all(limit: int = DEFAULT_WORKFLOW_SCAN_LIMIT) -> Dict[str, Any]:
    try:
        gw = health()
        bh = bridge.health()
        bc = bridge.config()
        api = bridge.public_api_check()
        inventory = sync_inventory(limit=limit)
        cleanup = cleanup_candidates(
            older_than_hours=DEFAULT_CLEANUP_OLDER_THAN_HOURS,
            max_keep_per_template=DEFAULT_CLEANUP_KEEP_PER_TEMPLATE,
            limit=limit,
        )
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
                "total": inventory["inventory"]["total"],
                "active": inventory["inventory"]["active"],
                "inactive": inventory["inventory"]["inactive"],
                "managed": inventory["inventory"]["managed"],
                "unmanaged": inventory["inventory"]["unmanaged"],
            },
            "cleanup_candidate_count": len(cleanup["candidates"]),
            "all_core_ok": (
                gw.get("status") == "healthy"
                and bool(readiness.get("ok"))
                and bool((api.get("body") or {}).get("ok"))
            ),
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
            "templates": registry.get("templates", []),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/template-recommendations")
def template_recommendations(
    purpose: str = Query(default=""),
    tag: str = Query(default=""),
    goal: str = Query(default=""),
) -> Dict[str, Any]:
    try:
        return {
            "status": "ok",
            "service": MODULE_ID,
            "purpose": purpose,
            "tag": tag,
            "goal": goal,
            "recommendations": recommend_templates(purpose=purpose, tag=tag, goal=goal),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/planner/recommend")
def planner_recommend(goal: str, purpose: str = "", tag: str = "") -> Dict[str, Any]:
    try:
        return {
            "status": "ok",
            "service": MODULE_ID,
            **choose_template_for_goal(goal=goal, purpose=purpose, tag=tag),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/memory")
def memory() -> Dict[str, Any]:
    try:
        return {"status": "ok", "service": MODULE_ID, "memory": load_memory()}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/jarvis/n8n/templates/register")
def template_register(request: RegisterTemplateRequest) -> Dict[str, Any]:
    try:
        existing_registry = load_registry()
        already_exists = any(tpl.get("id") == request.id for tpl in existing_registry.get("templates", []))
        entry = normalize_template(request.model_dump())
        if not entry:
            raise ValueError("template id/title is invalid")
        templates = [tpl for tpl in existing_registry.get("templates", []) if tpl.get("id") != entry["id"]]
        templates.append(entry)
        existing_registry["templates"] = sorted(templates, key=lambda x: x["id"])
        existing_registry["version"] = max(int(existing_registry.get("version") or 1), 2)
        save_json_atomic(REGISTRY_PATH, existing_registry)
        update_memory(entry["id"], action="register", ok=True)
        return {
            "status": "ok",
            "service": MODULE_ID,
            "created_new": not already_exists,
            "template": entry,
        }
    except Exception as exc:
        try:
            update_memory(request.id, action="register", ok=False, error=str(exc))
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/jarvis/n8n/templates/render")
def template_render(request: TemplateRunRequest) -> Dict[str, Any]:
    try:
        mapping = templates_map()
        tpl = mapping.get(request.template_id)
        if not tpl:
            raise HTTPException(status_code=404, detail=f"Template not found: {request.template_id}")
        instance = make_instance(tpl, name=request.name, webhook_path=request.webhook_path)
        workflow = template_preview_workflow(instance)
        update_memory(
            request.template_id,
            action="render",
            ok=True,
            webhook_path=instance["webhook_path"],
            workflow_name=instance["workflow_name"],
        )
        return {
            "status": "ok",
            "service": MODULE_ID,
            "template": tpl,
            "instance": instance,
            "workflow": workflow,
        }
    except HTTPException:
        raise
    except Exception as exc:
        try:
            update_memory(request.template_id, action="render", ok=False, error=str(exc))
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/jarvis/n8n/templates/create")
def template_create(request: TemplateRunRequest) -> Dict[str, Any]:
    try:
        return create_or_reuse_template_workflow(request)
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
    template = created.get("template") or {}
    workflow_id = created.get("workflow_id")
    instance = created.get("instance") or {}
    if not workflow_id:
        raise HTTPException(status_code=500, detail="workflow_id missing after create/reuse")
    try:
        already_active = bool((created.get("existing") or {}).get("active"))
        activated = activate_template_workflow(
            template_id=template["id"],
            workflow_id=str(workflow_id),
            workflow_name=instance.get("workflow_name") or (created.get("existing") or {}).get("name") or "",
            webhook_path=instance.get("webhook_path") or (created.get("existing") or {}).get("webhook_path") or "",
            already_active=already_active,
        )
        return {**created, "activate": activated}
    except Exception as exc:
        try:
            update_memory(
                template["id"],
                action="activate",
                ok=False,
                workflow_id=str(workflow_id),
                webhook_path=instance.get("webhook_path"),
                workflow_name=instance.get("workflow_name"),
                error=str(exc),
            )
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/jarvis/n8n/templates/smoke")
def template_smoke(request: TemplateSmokeRequest) -> Dict[str, Any]:
    create_req = TemplateRunRequest(
        template_id=request.template_id,
        name=request.name,
        webhook_path=request.webhook_path,
        activate=True,
        reuse_existing=request.reuse_existing,
    )
    created = template_create(create_req)
    template = created.get("template") or {}
    workflow_id = created.get("workflow_id")
    instance = created.get("instance") or {}
    webhook_path = instance.get("webhook_path") or (created.get("existing") or {}).get("webhook_path")
    workflow_name = instance.get("workflow_name") or (created.get("existing") or {}).get("name")
    if not workflow_id:
        raise HTTPException(status_code=500, detail="workflow_id missing after create/reuse")
    try:
        already_active = bool((created.get("existing") or {}).get("active"))
        activated = activate_template_workflow(
            template_id=template["id"],
            workflow_id=str(workflow_id),
            workflow_name=workflow_name or "",
            webhook_path=webhook_path or "",
            already_active=already_active,
        )
        payload = {}
        default_smoke = template.get("smoke_payload") or {}
        if isinstance(default_smoke, dict):
            payload.update(default_smoke)
        payload.update(request.payload or {})
        if request.wait_seconds > 0:
            time.sleep(min(max(request.wait_seconds, 0), 15))
        probe = probe_webhook(str(webhook_path), payload=payload) if webhook_path else {"skipped": True}
        update_memory(
            template["id"],
            action="smoke",
            ok=True,
            workflow_id=str(workflow_id),
            webhook_path=webhook_path,
            workflow_name=workflow_name,
        )
        result = {**created, "activate": activated, "probe": probe}
        return result
    except Exception as exc:
        try:
            update_memory(
                template["id"],
                action="smoke",
                ok=False,
                workflow_id=str(workflow_id),
                webhook_path=webhook_path,
                workflow_name=workflow_name,
                error=str(exc),
            )
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/templates/{template_id}")
def template_detail(template_id: str) -> Dict[str, Any]:
    try:
        mapping = templates_map()
        tpl = mapping.get(template_id)
        if not tpl:
            raise HTTPException(status_code=404, detail=f"Template not found: {template_id}")
        return {"status": "ok", "service": MODULE_ID, "template": tpl}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/jarvis/n8n/planner/create-from-goal")
def planner_create_from_goal(request: GoalWorkflowRequest) -> Dict[str, Any]:
    try:
        plan = choose_template_for_goal(goal=request.goal, purpose=request.purpose, tag=request.tag)
        chosen = plan["chosen_template"]
        update_memory(chosen["id"], action="planner_pick", ok=True, details={"goal": request.goal})
        run_request = TemplateRunRequest(
            template_id=chosen["id"],
            name=request.name,
            webhook_path=request.webhook_path,
            activate=request.activate,
            reuse_existing=request.reuse_existing,
            metadata=request.metadata,
        )
        result = template_create_and_activate(run_request) if request.activate else template_create(run_request)
        return {
            "status": "ok",
            "service": MODULE_ID,
            "plan": plan,
            "result": result,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/jarvis/n8n/lifecycle/cleanup-report")
def lifecycle_cleanup_report(
    older_than_hours: int = DEFAULT_CLEANUP_OLDER_THAN_HOURS,
    max_keep_per_template: int = DEFAULT_CLEANUP_KEEP_PER_TEMPLATE,
    limit: int = DEFAULT_WORKFLOW_SCAN_LIMIT,
) -> Dict[str, Any]:
    try:
        return cleanup_candidates(
            older_than_hours=max(0, older_than_hours),
            max_keep_per_template=max(1, max_keep_per_template),
            limit=max(1, limit),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/jarvis/n8n/lifecycle/cleanup/apply")
def lifecycle_cleanup_apply(request: CleanupRequest) -> Dict[str, Any]:
    try:
        report = cleanup_candidates(
            older_than_hours=max(0, request.older_than_hours),
            max_keep_per_template=max(1, request.max_keep_per_template),
            limit=max(1, request.limit),
        )
        actions: List[Dict[str, Any]] = []
        for candidate in report["candidates"]:
            workflow_id = str(candidate.get("id") or "")
            template_id = str(candidate.get("template_id") or "unmanaged")
            if request.dry_run:
                actions.append({**candidate, "action": "dry_run", "ok": True})
                continue
            performed = False
            if request.deactivate_if_supported and candidate.get("active"):
                ok, method, resp = deactivate_workflow_if_supported(workflow_id)
                actions.append({**candidate, "action": method, "ok": ok, "response": resp})
                performed = performed or ok
            if request.delete_if_supported:
                ok, method, resp = delete_workflow_if_supported(workflow_id)
                actions.append({**candidate, "action": method, "ok": ok, "response": resp})
                performed = performed or ok
            if performed:
                update_memory(
                    template_id,
                    action="cleanup",
                    ok=True,
                    workflow_id=workflow_id,
                    webhook_path=candidate.get("webhook_path"),
                    workflow_name=candidate.get("name"),
                )
            else:
                actions.append({**candidate, "action": "skipped", "ok": False, "reason": "bridge_cleanup_methods_not_supported"})
        synced = sync_inventory(limit=max(1, request.limit))
        return {
            "status": "ok",
            "service": MODULE_ID,
            "dry_run": request.dry_run,
            "report": report,
            "actions": actions,
            "inventory_after": synced["inventory"],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/jarvis/n8n/probe/{webhook_path}")
def probe_webhook_route(webhook_path: str, request: ProbeWebhookRequest) -> Dict[str, Any]:
    try:
        return probe_webhook(webhook_path, payload=request.payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

# === JARVIS DIRECTOR LIFECYCLE TUNEUP INSTALL ===
try:
    from app.jarvis_director_lifecycle_tuneup import install as _jarvis_install_director_lifecycle_tuneup
    _jarvis_install_director_lifecycle_tuneup(app)
except Exception as _jarvis_director_lifecycle_tuneup_exc:
    print(f"[jarvis_director_lifecycle_tuneup] install skipped: {_jarvis_director_lifecycle_tuneup_exc}")
