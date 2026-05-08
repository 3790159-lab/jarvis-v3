from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from app.control_plane.autonomous_improvement import AutonomousImprovementController
from app.control_plane.claude_payload_probe import ClaudePayloadProbe
from app.control_plane.improvement_journal import ImprovementJournal
from app.control_plane.improvement_planner import ImprovementPlanner
from app.control_plane.improvement_registry import ImprovementRegistry
from app.control_plane.n8n_manager import N8NManager

router = APIRouter(prefix="/api/agent-mesh", tags=["agent-mesh-improvement"])

BASE = Path("state/agent_mesh")
_autonomy = AutonomousImprovementController(BASE)
_planner = ImprovementPlanner(BASE)
_registry = ImprovementRegistry(BASE)
_journal = ImprovementJournal(BASE)
_claude_probe = ClaudePayloadProbe(BASE)
_n8n = N8NManager(BASE)
_autonomy.start_background()

@router.get("/improvement/health")
def improvement_health():
    return {
        "status": "ok",
        "planner_report": _planner.load_report(),
        "registry": _registry.list_items(),
        "autonomy": _autonomy.health(),
        "claude_probe": _claude_probe.health(),
        "n8n": _n8n.health(),
    }

@router.get("/improvement/proposals")
def improvement_proposals():
    return {
        "status": "ok",
        "report": _planner.load_report(),
        "registry": _registry.list_items(),
    }

@router.post("/improvement/tick")
def improvement_tick(reason: str = "manual_api"):
    return {
        "status": "ok",
        "report": _autonomy.run_safe_tick(reason=reason),
    }

@router.get("/improvement/registry")
def improvement_registry():
    return {
        "status": "ok",
        "items": _registry.list_items(),
    }

@router.post("/improvement/continuous/start")
def improvement_continuous_start(interval_seconds: int = 120):
    return {
        "status": "ok",
        "result": _autonomy.start_continuous(interval_seconds=interval_seconds),
    }

@router.post("/improvement/continuous/stop")
def improvement_continuous_stop():
    return {
        "status": "ok",
        "result": _autonomy.stop_continuous(),
    }

@router.get("/improvement/continuous/status")
def improvement_continuous_status():
    return {
        "status": "ok",
        "result": _autonomy.health(),
    }

@router.get("/improvement/journal")
def improvement_journal(limit: int = 30):
    return {
        "status": "ok",
        "items": _journal.tail(limit=limit),
    }

@router.get("/claude-probe/health")
def claude_probe_health():
    return {
        "status": "ok",
        "probe": _claude_probe.health(),
    }