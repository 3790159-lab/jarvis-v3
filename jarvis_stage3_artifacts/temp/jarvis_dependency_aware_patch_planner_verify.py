from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_dependency_aware_patch_planner import JarvisDependencyAwarePatchPlanner


def main() -> int:
    planner = JarvisDependencyAwarePatchPlanner(project_root=PROJECT_ROOT)

    result = planner.build_and_execute(
        goal="Improve dependency-aware validation bundle",
        task="Create a bounded dependency-aware patch with generated module, verify script, and smoke script",
        patch_name="dependency_bundle_probe",
        estimated_lines_changed=90,
    )
    metrics = planner.collect_metrics()

    smoke_ok = False
    smoke_output = ""
    if result.status == "completed" and result.touched_files:
        execution_path = PROJECT_ROOT / "jarvis_stage3_artifacts" / "dependency_aware_patch_planner" / "executions" / f"{result.plan_id}.json"
        plan_path = PROJECT_ROOT / "jarvis_stage3_artifacts" / "dependency_aware_patch_planner" / "plans" / f"{result.plan_id}.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        smoke_file = PROJECT_ROOT / plan["smoke_file"]
        if smoke_file.exists():
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(smoke_file)],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=90,
            )
            smoke_ok = completed.returncode == 0
            smoke_output = (completed.stdout + "\n" + completed.stderr)[-1500:]

    payload = {
        "plan_id": result.plan_id,
        "status": result.status,
        "recommended_action": result.recommended_action,
        "mutation_id": result.mutation_id,
        "final_outcome": result.final_outcome,
        "apply_status": result.apply_status,
        "validation_status": result.validation_status,
        "rollback_status": result.rollback_status,
        "touched_files_count": len(result.touched_files),
        "smoke_ok": smoke_ok,
        "smoke_output_excerpt": smoke_output,
        "metrics": metrics,
    }

    output_path = PROJECT_ROOT / "jarvis_stage3_artifacts" / "temp" / "jarvis_dependency_aware_patch_planner_verify_result.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))

    ok = (
        result.status == "completed"
        and result.final_outcome == "accepted"
        and result.validation_status == "passed"
        and len(result.touched_files) >= 3
        and smoke_ok
        and metrics["completed_count"] >= 1
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())