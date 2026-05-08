from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from app.control_plane.autonomous_improvement import AutonomousImprovementController
from app.control_plane.external_call_guard import ExternalCallGuard
from app.control_plane.self_healing import SelfHealingEngine
from app.control_plane.service_resolver import ServiceResolver

router = APIRouter(prefix="/api/agent-mesh", tags=["agent-mesh-stability"])

BASE = Path("state/agent_mesh")
_guard = ExternalCallGuard(BASE)
_healing = SelfHealingEngine(BASE)
_resolver = ServiceResolver(BASE)
_autonomy = AutonomousImprovementController(BASE)
_autonomy.start_background()

@router.get("/service-guard/health")
def service_guard_health():
    return {"status": "ok", "guard": _guard.health()}

@router.get("/self-healing/health")
def self_healing_health():
    return {"status": "ok", "state": _healing.health()}

@router.post("/self-healing/tick")
def self_healing_tick(reason: str = "manual_api"):
    return {"status": "ok", "report": _healing.tick(reason=reason)}

@router.get("/service-resolution/example")
def service_resolution_example():
    return {
        "status": "ok",
        "examples": [
            {
                "task_type": "planning",
                "capability": "plan",
                "provider": "cloud",
                "resolved": _resolver.resolve_actual_service(
                    agent_id="planner_main",
                    capability="plan",
                    task_type="planning",
                    provider="cloud",
                    preferred_service="",
                    handoff_notes=[],
                ),
            },
            {
                "task_type": "codegen",
                "capability": "codegen",
                "provider": "cloud",
                "resolved": _resolver.resolve_actual_service(
                    agent_id="coding_agent_main",
                    capability="codegen",
                    task_type="codegen",
                    provider="cloud",
                    preferred_service="",
                    handoff_notes=[],
                ),
            },
            {
                "task_type": "memory_write",
                "capability": "memory_write",
                "provider": "ollama",
                "resolved": _resolver.resolve_actual_service(
                    agent_id="memory_agent_main",
                    capability="memory_write",
                    task_type="memory_write",
                    provider="ollama",
                    preferred_service="",
                    handoff_notes=[],
                ),
            },
        ],
    }