from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI

MODULE_ID = "jarvis_director_lifecycle_tuneup"
MODULE_VERSION = "1.0.0"
PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
TEMPLATES_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "n8n_templates"
MEMORY_PATH = TEMPLATES_DIR / "director_pro_core_memory.json"
REGISTRY_PATH = TEMPLATES_DIR / "director_pro_core_registry.json"

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def load_json(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def install(app: FastAPI) -> None:
    if getattr(app.state, "_jarvis_director_lifecycle_tuneup_installed", False):
        return

    app.state._jarvis_director_lifecycle_tuneup_installed = True
    app.state._jarvis_director_lifecycle_tuneup_installed_at = now_iso()

    @app.get("/api/jarvis/n8n/lifecycle/policy")
    def _jarvis_lifecycle_policy() -> Dict[str, Any]:
        return {
            "status": "ok",
            "service": "jarvis_local_n8n_director_pro_core",
            "module_id": MODULE_ID,
            "version": MODULE_VERSION,
            "installed_at": app.state._jarvis_director_lifecycle_tuneup_installed_at,
            "policy": {
                "package_first": True,
                "manual_merge_required": True,
                "registry_path": str(REGISTRY_PATH),
                "memory_path": str(MEMORY_PATH),
            },
        }

    @app.get("/api/jarvis/n8n/lifecycle/managed-summary")
    def _jarvis_managed_summary() -> Dict[str, Any]:
        memory = load_json(MEMORY_PATH, {})
        workflow_inventory = memory.get("workflow_inventory") or {}
        return {
            "status": "ok",
            "service": "jarvis_local_n8n_director_pro_core",
            "module_id": MODULE_ID,
            "inventory": workflow_inventory,
            "template_stats": memory.get("template_stats") or {},
            "service_stats": memory.get("service_stats") or {},
        }

    @app.get("/api/jarvis/n8n/lifecycle/audit-snapshot")
    def _jarvis_audit_snapshot() -> Dict[str, Any]:
        memory = load_json(MEMORY_PATH, {})
        registry = load_json(REGISTRY_PATH, {})
        templates = registry.get("templates")
        if isinstance(templates, dict):
            registry_count = len(templates)
        elif isinstance(registry, dict):
            registry_count = len(registry)
        else:
            registry_count = 0
        return {
            "status": "ok",
            "service": "jarvis_local_n8n_director_pro_core",
            "module_id": MODULE_ID,
            "registry_count": registry_count,
            "recent_events_count": len(memory.get("recent_events") or []),
            "inventory_last_synced_ts": (memory.get("workflow_inventory") or {}).get("last_synced_ts"),
            "captured_at": now_iso(),
        }