from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_memory_learning_layer import JarvisMemoryLearningLayer


def main() -> int:
    layer = JarvisMemoryLearningLayer(project_root=PROJECT_ROOT)

    events = layer.ingest_all()
    insights = layer.build_module_insights(events)
    snapshot = layer.build_learning_snapshot(events, insights)
    recommendation = layer.recommend_next_best_action(snapshot)
    metrics = layer.collect_metrics()

    payload = {
        "events_count": len(events),
        "insights_count": len(insights),
        "snapshot_id": snapshot.snapshot_id,
        "accepted_mutations": snapshot.accepted_mutations,
        "completed_runs": snapshot.completed_runs,
        "top_success_modules": snapshot.top_success_modules[:5],
        "top_problem_modules": snapshot.top_problem_modules[:5],
        "recommendation": recommendation,
        "metrics": metrics,
    }

    output_path = PROJECT_ROOT / "jarvis_stage3_artifacts" / "temp" / "jarvis_memory_learning_layer_verify_result.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))

    ok = (
        len(events) > 0
        and metrics["events_count"] > 0
        and snapshot.snapshot_id.startswith("learn_")
        and isinstance(recommendation, str)
        and len(recommendation) > 0
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())