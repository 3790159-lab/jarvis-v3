from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_explainability_operator_control import JarvisExplainabilityOperatorControl


def main() -> int:
    layer = JarvisExplainabilityOperatorControl(project_root=PROJECT_ROOT)

    explanation = layer.build_explanation()
    snapshot = layer.build_control_snapshot(explanation)
    metrics = layer.collect_metrics()

    payload = {
        "system_state": explanation.system_state,
        "summary": explanation.summary,
        "what_changed_count": len(explanation.what_changed),
        "why_not_applied_count": len(explanation.why_not_applied),
        "next_best_actions_count": len(explanation.next_best_actions),
        "current_mode": snapshot.current_mode,
        "overall_health": snapshot.overall_health,
        "apply_lane_state": snapshot.apply_lane_state,
        "blocked_reasons_count": len(snapshot.blocked_reasons),
        "operator_message": snapshot.operator_message,
        "metrics": metrics,
    }

    output_path = PROJECT_ROOT / "jarvis_stage3_artifacts" / "temp" / "jarvis_explainability_operator_control_verify_result.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))

    ok = (
        isinstance(explanation.summary, str)
        and len(explanation.summary) > 0
        and isinstance(snapshot.operator_message, str)
        and len(snapshot.operator_message) > 0
        and metrics["explanations_count"] >= 1
        and metrics["snapshots_count"] >= 1
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())