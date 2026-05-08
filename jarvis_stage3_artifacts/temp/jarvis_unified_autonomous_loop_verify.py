from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_unified_autonomous_loop import JarvisUnifiedAutonomousLoop


def main() -> int:
    loop = JarvisUnifiedAutonomousLoop(project_root=PROJECT_ROOT)

    result = loop.run(
        goal="Run unified autonomous loop with safe low-risk improvement context",
        task="Coordinate a safe mutation, refresh readiness, refresh memory, and produce operator-facing summary",
        priority="normal",
    )
    metrics = loop.collect_metrics()

    payload = {
        "loop_run_id": result.loop_run_id,
        "status": result.status,
        "coordination_status": result.coordination_status,
        "recommended_action": result.recommended_action,
        "apply_lane_state": result.apply_lane_state,
        "mutation_outcome": result.mutation_outcome,
        "blocked_reasons_count": len(result.blocked_reasons),
        "next_best_action": result.next_best_action,
        "operator_message": result.operator_message,
        "subsystem_statuses": result.subsystem_statuses,
        "phases_count": len(result.phases),
        "metrics": metrics,
    }

    output_path = PROJECT_ROOT / "jarvis_stage3_artifacts" / "temp" / "jarvis_unified_autonomous_loop_verify_result.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))

    ok = (
        result.status in {"completed", "degraded"}
        and result.coordination_status in {"completed", "degraded", "blocked"}
        and len(result.phases) >= 4
        and metrics["runs_count"] >= 1
        and "coordination" in result.subsystem_statuses
        and "memory_learning" in result.subsystem_statuses
        and "external_readiness" in result.subsystem_statuses
        and "explainability" in result.subsystem_statuses
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())