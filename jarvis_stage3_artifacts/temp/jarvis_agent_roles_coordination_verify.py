from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_agent_roles_coordination import AgentRolesCoordinator


def main() -> int:
    coordinator = AgentRolesCoordinator(project_root=PROJECT_ROOT)

    run = coordinator.run(
        goal="Improve observability with a low-risk coordinated mutation",
        task="Create a safe single-file probe through supervisor planner coder validator risk guardian archivist chain",
        priority="normal",
        target_paths=["jarvis_stage3_artifacts/temp/agent_roles_coordination_probe.py"],
    )

    metrics = coordinator.collect_metrics()

    payload = {
        "run_id": run.run_id,
        "status": run.status,
        "recommended_action": run.recommended_action,
        "accepted_mutation_id": run.accepted_mutation_id,
        "final_reason": run.final_reason,
        "next_best_action": run.next_best_action,
        "role_records_count": len(run.role_records),
        "handoffs_count": len(run.handoffs),
        "metrics": metrics,
    }

    output_path = PROJECT_ROOT / "jarvis_stage3_artifacts" / "temp" / "jarvis_agent_roles_coordination_verify_result.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))

    ok = (
        run.status in {"completed", "degraded"}
        and run.recommended_action in {"apply", "artifact_only"}
        and len(run.role_records) >= 5
        and len(run.handoffs) >= 4
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())