from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_safe_mutation_foundation import SafeMutationFoundation
from app.services.jarvis_decision_risk_engine import JarvisDecisionRiskEngine


def main() -> int:
    foundation = SafeMutationFoundation(project_root=PROJECT_ROOT)
    engine = JarvisDecisionRiskEngine(project_root=PROJECT_ROOT)

    low_candidate = foundation.create_candidate(
        goal="Add safe low-risk helper patch",
        reason="Local temp probe mutation for score testing",
        change_type="small_code_patch",
        target_files=["jarvis_stage3_artifacts/temp/decision_probe_low.py"],
        expected_effect="Create low-risk local code-backed mutation",
        risk_level="low",
        reversible=True,
        estimated_validation_scope="local_compile_and_health",
        operations=[
            {
                "op": "create_file",
                "path": "jarvis_stage3_artifacts/temp/decision_probe_low.py",
                "content": 'def value() -> str:\n    return "ok"\n',
                "create_if_missing": True,
            }
        ],
        metadata={"verify": True, "block": "decision_risk_engine"},
    )

    high_candidate = foundation.create_candidate(
        goal="Simulate high-risk multi-target patch",
        reason="Cross-file risky patch candidate for blocking test",
        change_type="migration",
        target_files=[
            "app/main.py",
            "app/services/jarvis_safe_mutation_foundation.py",
            "scripts/jarvis_safe_mutation_foundation_smoke.ps1",
            "jarvis_stage3_artifacts/temp/high_risk_probe.py",
        ],
        expected_effect="High-risk test only",
        risk_level="critical",
        reversible=False,
        estimated_validation_scope="unknown",
        operations=[
            {
                "op": "create_file",
                "path": "jarvis_stage3_artifacts/temp/high_risk_probe.py",
                "content": "x = 1\n",
                "create_if_missing": True,
            }
        ],
        metadata={"verify": True, "block": "decision_risk_engine"},
    )

    low_outcome = engine.decide(low_candidate)
    high_outcome = engine.decide(high_candidate)
    metrics = engine.collect_metrics()

    payload = {
        "low": {
            "recommended_action": low_outcome.recommended_action,
            "allowed": low_outcome.allowed,
            "gate_status": low_outcome.gate_status,
            "score": low_outcome.scorecard.total_score,
            "reasons": low_outcome.reasons,
        },
        "high": {
            "recommended_action": high_outcome.recommended_action,
            "allowed": high_outcome.allowed,
            "gate_status": high_outcome.gate_status,
            "score": high_outcome.scorecard.total_score,
            "reasons": high_outcome.reasons,
        },
        "metrics": metrics,
    }

    output_path = PROJECT_ROOT / "jarvis_stage3_artifacts" / "temp" / "jarvis_decision_risk_engine_verify_result.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))

    low_ok = low_outcome.recommended_action in {"apply", "artifact_only"} and low_outcome.scorecard.total_score >= 45
    high_ok = high_outcome.recommended_action == "blocked" and ("critical_risk_level" in high_outcome.reasons)

    return 0 if low_ok and high_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())