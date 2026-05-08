from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from app.hooks.hooks import run_hooks
from app.services.claude_ecosystem_orchestrator import (
    run_after_mission,
    run_before_mission,
    run_before_night_mode,
    run_after_night_mode,
)
from app.services.ultra_upgrade_engine import build_ultra_upgrade_plan


ARTIFACT_ROOT = Path("jarvis_stage3_artifacts/claude_ecosystem/execution_bridge")
EVENT_LOG = ARTIFACT_ROOT / "bridge_events.jsonl"


def _now() -> str:
    return datetime.utcnow().isoformat()


def _log(event: Dict[str, Any]) -> None:
    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
    with EVENT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def run_safe_gateway_plan(
    project_root: str,
    limit: int = 5,
    executor_script: str = "scripts\\jarvis_gateway_plan_executor_v7_2.ps1",
) -> Dict[str, Any]:
    mission_id = f"gateway_plan_safe_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"

    preflight = run_before_mission(
        mission_id,
        {
            "executor_script": executor_script,
            "limit": limit,
            "mode": "safe_gateway_plan",
        },
    )

    ultra = build_ultra_upgrade_plan(
        {
            "source": "safe_gateway_plan",
            "mission_id": mission_id,
        }
    )

    command = [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        executor_script,
        "-ProjectRoot",
        project_root,
        "-Limit",
        str(limit),
    ]

    started_at = _now()
    run_hooks("before_safe_gateway_plan_subprocess", {"mission_id": mission_id, "command": command})

    proc = subprocess.run(
        command,
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=1800,
    )

    finished_at = _now()

    success = proc.returncode == 0

    postflight = run_after_mission(
        mission_id,
        {
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-4000:],
            "stderr_tail": proc.stderr[-4000:],
        },
        success=success,
    )

    run_hooks(
        "after_safe_gateway_plan_subprocess",
        {
            "mission_id": mission_id,
            "success": success,
            "returncode": proc.returncode,
        },
    )

    result = {
        "schema": "jarvis.execution_bridge.safe_gateway_plan.v1_3",
        "mission_id": mission_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "success": success,
        "returncode": proc.returncode,
        "preflight": preflight,
        "ultra": ultra,
        "postflight": postflight,
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }

    _log(result)
    return result


def run_safe_night_preflight(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = payload or {}
    pre = run_before_night_mode(payload)
    ultra = build_ultra_upgrade_plan({"source": "night_preflight", "payload": payload})
    result = {
        "schema": "jarvis.execution_bridge.night_preflight.v1_3",
        "created_at": _now(),
        "status": "ok",
        "preflight": pre,
        "ultra": ultra,
    }
    _log(result)
    return result


def run_safe_night_postflight(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = payload or {}
    post = run_after_night_mode(payload)
    result = {
        "schema": "jarvis.execution_bridge.night_postflight.v1_3",
        "created_at": _now(),
        "status": post.get("status", "ok"),
        "postflight": post,
    }
    _log(result)
    return result