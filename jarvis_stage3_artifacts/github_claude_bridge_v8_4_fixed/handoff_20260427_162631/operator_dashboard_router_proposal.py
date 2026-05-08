"""
Jarvis Operator Dashboard router proposal.
Safe read-only API.
"""

from pathlib import Path
from typing import Any
from fastapi import APIRouter

router = APIRouter(prefix="/api/operator-dashboard", tags=["operator-dashboard"])

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def safe_read_json(relative_path: str, default: Any) -> Any:
    import json
    path = PROJECT_ROOT / relative_path
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception as exc:
        return {"ok": False, "error": str(exc), "path": relative_path}


@router.get("/health")
def dashboard_health():
    return {
        "ok": True,
        "service": "jarvis_operator_dashboard",
        "mode": "read_only",
    }


@router.get("/queue")
def dashboard_queue():
    return safe_read_json("state/jarvis_brain/action_queue_v6_4.json", {"items": []})


@router.get("/evidence")
def dashboard_evidence():
    return safe_read_json(
        "jarvis_stage3_artifacts/real_action_evidence/latest_real_action_evidence_report.json",
        {},
    )


@router.get("/gateway")
def dashboard_gateway():
    return safe_read_json(
        "jarvis_stage3_artifacts/unified_tool_gateway_v7_0/latest_gateway_smoke.json",
        {},
    )


@router.get("/full-creator")
def dashboard_full_creator():
    return safe_read_json(
        "jarvis_stage3_artifacts/full_creator_v8_0/latest_full_creator_manifest.json",
        {},
    )