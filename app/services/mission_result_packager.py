from __future__ import annotations

from typing import Any, Dict, List

from app.services.artifact_registry import collect_mission_artifacts, save_mission_result


def package_mission_result(
    mission_id: str,
    objective: str,
    steps: List[Dict[str, Any]],
    final_summary: str,
    ok: bool,
    error: str | None,
) -> Dict[str, Any]:
    manifest = collect_mission_artifacts(mission_id=mission_id, steps=steps)

    completed_steps = 0
    failed_steps = 0
    for step in steps or []:
        status = str((step or {}).get("status") or "").strip().lower()
        if status == "completed":
            completed_steps += 1
        elif status == "failed":
            failed_steps += 1

    packaged = {
        "mission_id": mission_id,
        "objective": objective,
        "ok": ok,
        "error": error,
        "final_summary": final_summary,
        "step_count": len(steps or []),
        "completed_steps": completed_steps,
        "failed_steps": failed_steps,
        "artifact_manifest": manifest,
        "steps": steps,
    }

    save_mission_result(mission_id, packaged)
    return packaged