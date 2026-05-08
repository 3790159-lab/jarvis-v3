from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_advanced_mutation_lane import JarvisAdvancedMutationLane


def main() -> int:
    lane = JarvisAdvancedMutationLane(project_root=PROJECT_ROOT)

    low = lane.decide(
        goal="Improve observability and logging",
        task="Add safer structured logging and diagnostics for a small service",
        target_files=["app/services/jarvis_memory_learning_layer.py"],
        support_files=[],
        estimated_lines_changed=45,
    )

    medium_low = lane.decide(
        goal="Improve validation with verify and smoke support",
        task="Create a coordinated validation patch with support files",
        target_files=["app/services/jarvis_external_systems_readiness.py"],
        support_files=[
            "jarvis_stage3_artifacts/temp/external_readiness_verify.py",
            "scripts/external_readiness_smoke.ps1",
        ],
        estimated_lines_changed=120,
    )

    risky = lane.decide(
        goal="Major migration and rewrite",
        task="Rewrite main router and delete old logic",
        target_files=["app/main.py", ".env"],
        support_files=[],
        estimated_lines_changed=300,
    )

    metrics = lane.collect_metrics()

    payload = {
        "low": {
            "patch_class": low.patch_class,
            "risk_lane": low.risk_lane,
            "recommended_action": low.recommended_action,
            "allowed": low.allowed,
            "confidence": low.confidence,
            "blast_radius": low.blast_radius.blast_radius_score,
            "reasons": low.reasons,
        },
        "medium_low": {
            "patch_class": medium_low.patch_class,
            "risk_lane": medium_low.risk_lane,
            "recommended_action": medium_low.recommended_action,
            "allowed": medium_low.allowed,
            "confidence": medium_low.confidence,
            "blast_radius": medium_low.blast_radius.blast_radius_score,
            "reasons": medium_low.reasons,
        },
        "risky": {
            "patch_class": risky.patch_class,
            "risk_lane": risky.risk_lane,
            "recommended_action": risky.recommended_action,
            "allowed": risky.allowed,
            "confidence": risky.confidence,
            "blast_radius": risky.blast_radius.blast_radius_score,
            "reasons": risky.reasons,
        },
        "metrics": metrics,
    }

    output_path = PROJECT_ROOT / "jarvis_stage3_artifacts" / "temp" / "jarvis_advanced_mutation_lane_verify_result.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))

    ok = (
        low.recommended_action in {"apply", "artifact_only"}
        and medium_low.recommended_action in {"apply", "artifact_only"}
        and risky.recommended_action == "blocked"
        and metrics["decisions_count"] >= 3
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())