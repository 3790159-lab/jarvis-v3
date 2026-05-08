from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from app.agents.backend_fix_agent import BackendFixAgent
from app.agents.full_creator_agent import FullCreatorAgent
from app.agents.night_mode_strategist import NightModeStrategist
from app.agents.qa_agent import QAAgent
from app.hooks.hooks import run_hooks


ARTIFACT_ROOT = Path("jarvis_stage3_artifacts/ultra_upgrade")
EVENT_LOG = ARTIFACT_ROOT / "ultra_events.jsonl"


def _now() -> str:
    return datetime.utcnow().isoformat()


def _log(event: Dict[str, Any]) -> None:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    with EVENT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def build_ultra_upgrade_plan(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = payload or {}

    run_hooks("before_ultra_upgrade_plan", {"payload": payload})

    backend = BackendFixAgent().run(payload)
    night = NightModeStrategist().run(payload)
    qa = QAAgent().run({"checks": ["compile", "health", "night_api", "artifact_log", "rollback_plan"]})

    plan = {
        "schema": "jarvis.ultra_upgrade.v1_3",
        "created_at": _now(),
        "status": "ok" if backend.status in ("ok", "needs_recovery") else "degraded",
        "payload": payload,
        "modules": {
            "backend_self_healing_2": backend.data,
            "night_mode_strategy": night.data,
            "qa_policy": qa.data,
        },
        "upgrade_tracks": [
            {
                "name": "auto_pipeline_builder",
                "goal": "Jarvis can generate safe task pipelines from goals.",
                "status": "foundation_ready",
            },
            {
                "name": "backend_self_healing",
                "goal": "Jarvis detects backend degradation and proposes recovery.",
                "status": "active_foundation",
            },
            {
                "name": "night_mode_safe_evolution",
                "goal": "Night Mode uses package-first checks and rollback plans.",
                "status": "active_foundation",
            },
            {
                "name": "full_creator",
                "goal": "Idea -> design -> code -> package -> QA -> improvement loop.",
                "status": "foundation_ready",
            },
        ],
        "next_actions": [
            "wire this plan before gateway plan execution",
            "wire after each night mode iteration",
            "generate package before risky apply",
            "create diff + rollback summaries",
        ],
    }

    _log(plan)
    run_hooks("after_ultra_upgrade_plan", {"status": plan["status"]})
    return plan


def build_full_creator_package(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = payload or {}
    run_hooks("before_full_creator_package", {"payload": payload})

    result = FullCreatorAgent().run(payload)

    response = {
        "schema": "jarvis.full_creator.v1_3",
        "created_at": _now(),
        "status": result.status,
        "agent": result.agent,
        "message": result.message,
        "data": result.data,
    }

    _log(response)
    run_hooks("after_full_creator_package", {"status": result.status})
    return response