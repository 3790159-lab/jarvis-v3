from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.jarvis_unified_night_bridge import JarvisUnifiedNightBridge


router = APIRouter(prefix="/api/unified-night", tags=["unified-night"])


class UnifiedNightRunRequest(BaseModel):
    max_iterations: int = Field(default=2, ge=1, le=8)
    degrade_on_failure: bool = True
    mode: str = "safe_unified_night_mode"


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _bridge() -> JarvisUnifiedNightBridge:
    return JarvisUnifiedNightBridge(project_root=_project_root())


@router.get("/health")
def unified_night_health() -> Dict[str, Any]:
    root = _project_root()
    required = [
        root / "app" / "services" / "jarvis_unified_autonomous_loop.py",
        root / "app" / "services" / "jarvis_unified_night_bridge.py",
        root / "app" / "services" / "jarvis_dependency_aware_patch_planner.py",
    ]
    missing = [str(p) for p in required if not p.exists()]
    return {
        "status": "healthy" if not missing else "degraded",
        "service": "jarvis_unified_night_router",
        "missing": missing,
        "project_root": str(root),
    }


@router.get("/metrics")
def unified_night_metrics() -> Dict[str, Any]:
    bridge = _bridge()
    return bridge.collect_metrics()


@router.post("/run")
def run_unified_night(payload: Optional[UnifiedNightRunRequest] = None) -> Dict[str, Any]:
    payload = payload or UnifiedNightRunRequest()
    bridge = _bridge()
    session = bridge.run_session(
        mode=payload.mode,
        max_iterations=payload.max_iterations,
        degrade_on_failure=payload.degrade_on_failure,
    )
    metrics = bridge.collect_metrics()
    return {
        "session": {
            "session_id": session.session_id,
            "mode": session.mode,
            "status": session.status,
            "completed_count": session.completed_count,
            "degraded_count": session.degraded_count,
            "failed_count": session.failed_count,
            "apply_count": session.apply_count,
            "next_best_actions": session.next_best_actions,
            "operator_summary": session.operator_summary,
            "iterations": [
                {
                    "index": it.index,
                    "goal": it.goal,
                    "status": it.status,
                    "recommended_action": it.recommended_action,
                    "apply_lane_state": it.apply_lane_state,
                    "mutation_outcome": it.mutation_outcome,
                    "blocked_reasons": it.blocked_reasons,
                    "next_best_action": it.next_best_action,
                    "operator_message": it.operator_message,
                    "loop_run_id": it.loop_run_id,
                }
                for it in session.iterations
            ],
            "created_at": session.created_at,
            "finished_at": session.finished_at,
        },
        "metrics": metrics,
    }


@router.get("/latest")
def latest_unified_night_session() -> Dict[str, Any]:
    root = _project_root()
    sessions_dir = root / "jarvis_stage3_artifacts" / "unified_night_bridge" / "sessions"
    if not sessions_dir.exists():
        return {"found": False, "reason": "sessions_dir_missing"}

    files = sorted(sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {"found": False, "reason": "no_sessions"}

    data = json.loads(files[0].read_text(encoding="utf-8"))
    return {
        "found": True,
        "path": str(files[0]),
        "session": data,
    }