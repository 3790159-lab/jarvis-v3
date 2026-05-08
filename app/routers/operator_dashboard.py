from pathlib import Path
from typing import Any, Dict

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
        return {
            "ok": False,
            "error": str(exc),
            "path": relative_path,
        }


def file_exists(relative_path: str) -> bool:
    return (PROJECT_ROOT / relative_path).exists()


@router.get("/health")
def dashboard_health() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "jarvis_operator_dashboard",
        "mode": "read_only",
        "project_root": str(PROJECT_ROOT),
        "available_sources": {
            "queue": file_exists("state/jarvis_brain/action_queue_v6_4.json"),
            "evidence": file_exists("jarvis_stage3_artifacts/real_action_evidence/latest_real_action_evidence_report.json"),
            "gateway": file_exists("jarvis_stage3_artifacts/unified_tool_gateway_v7_0/latest_gateway_smoke.json"),
            "full_creator": file_exists("jarvis_stage3_artifacts/full_creator_v8_0/latest_full_creator_manifest.json"),
            "backend_self_healing": file_exists("jarvis_stage3_artifacts/backend_self_healing_v8_2/latest_backend_self_healing_report.json"),
        },
    }


@router.get("/queue")
def dashboard_queue() -> Any:
    return safe_read_json("state/jarvis_brain/action_queue_v6_4.json", {"items": []})


@router.get("/evidence")
def dashboard_evidence() -> Any:
    return safe_read_json(
        "jarvis_stage3_artifacts/real_action_evidence/latest_real_action_evidence_report.json",
        {},
    )


@router.get("/gateway")
def dashboard_gateway() -> Any:
    return safe_read_json(
        "jarvis_stage3_artifacts/unified_tool_gateway_v7_0/latest_gateway_smoke.json",
        {},
    )


@router.get("/full-creator")
def dashboard_full_creator() -> Any:
    return safe_read_json(
        "jarvis_stage3_artifacts/full_creator_v8_0/latest_full_creator_manifest.json",
        {},
    )


@router.get("/self-healing")
def dashboard_self_healing() -> Any:
    return safe_read_json(
        "jarvis_stage3_artifacts/backend_self_healing_v8_2/latest_backend_self_healing_report.json",
        {},
    )


@router.get("/summary")
def dashboard_summary() -> Dict[str, Any]:
    queue = safe_read_json("state/jarvis_brain/action_queue_v6_4.json", {"items": []})
    evidence = safe_read_json(
        "jarvis_stage3_artifacts/real_action_evidence/latest_real_action_evidence_report.json",
        {},
    )
    gateway = safe_read_json(
        "jarvis_stage3_artifacts/unified_tool_gateway_v7_0/latest_gateway_smoke.json",
        {},
    )

    items = queue.get("items", []) if isinstance(queue, dict) else []

    return {
        "ok": True,
        "queue": {
            "total": len(items),
            "pending": len([x for x in items if x.get("status") == "pending"]),
            "completed": len([x for x in items if x.get("status") == "completed"]),
            "failed": len([x for x in items if x.get("status") == "failed"]),
            "blocked": len([x for x in items if x.get("status") == "blocked"]),
        },
        "truth_guard": evidence.get("truth_guard", {}) if isinstance(evidence, dict) else {},
        "backend_summary": evidence.get("backend", {}).get("summary", {}) if isinstance(evidence, dict) else {},
        "gateway": {
            "catalog_count": gateway.get("catalog_count") if isinstance(gateway, dict) else None,
            "smoke_passed": gateway.get("smoke_passed") if isinstance(gateway, dict) else None,
            "smoke_total": gateway.get("smoke_total") if isinstance(gateway, dict) else None,
        },
    }