from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from app.control_plane.autonomy_control import AutonomyControl
from app.control_plane.n8n_manager import N8NManager
from app.control_plane.trace_rebuilder import TraceRebuilder

router = APIRouter(prefix="/api/agent-mesh", tags=["agent-mesh-real-exec"])

BASE = Path("state/agent_mesh")
_n8n = N8NManager(BASE)
_rebuilder = TraceRebuilder(BASE)
_autonomy = AutonomyControl(BASE)

@router.get("/n8n/health")
def n8n_health():
    return {"status": "ok", "n8n": _n8n.health()}

@router.post("/n8n/verify")
def n8n_verify():
    return {"status": "ok", "result": _n8n.verify_api()}

@router.post("/n8n/test-webhook")
def n8n_test_webhook(dry_run: bool = True):
    payload = {
        "source": "jarvis_v16",
        "ping": True,
        "dry_run": dry_run,
        "message": "Jarvis V16 n8n verification"
    }
    return {"status": "ok", "result": _n8n.test_webhook(payload=payload, dry_run=dry_run)}

@router.post("/n8n/promote-live")
def n8n_promote_live(enabled: bool = True):
    return {"status": "ok", "result": _n8n.promote_live(enabled)}

@router.post("/traces/rebuild")
def traces_rebuild():
    return {"status": "ok", "result": _rebuilder.rebuild()}

@router.post("/autonomy/enable")
def autonomy_enable(interval_seconds: int = 600):
    return {"status": "ok", "result": _autonomy.enable(interval_seconds=interval_seconds)}

@router.post("/autonomy/disable")
def autonomy_disable():
    return {"status": "ok", "result": _autonomy.disable()}

@router.get("/real-exec/health")
def real_exec_health():
    return {
        "status": "ok",
        "n8n": _n8n.health(),
        "autonomy": _autonomy.load(),
    }