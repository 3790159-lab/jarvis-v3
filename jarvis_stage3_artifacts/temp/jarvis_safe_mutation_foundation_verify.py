from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_safe_mutation_foundation import SafeMutationFoundation


def main() -> int:
    project_root = PROJECT_ROOT
    foundation = SafeMutationFoundation(project_root=project_root)

    temp_dir = project_root / "jarvis_stage3_artifacts" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    target_file = temp_dir / "safe_mutation_probe.py"
    target_file.write_text(
        'def probe_value() -> str:\n'
        '    return "before"\n',
        encoding="utf-8",
        newline="\n",
    )

    candidate = foundation.create_candidate(
        goal="Verify safe mutation foundation",
        reason="Low-risk local probe patch",
        change_type="small_code_patch",
        target_files=["jarvis_stage3_artifacts/temp/safe_mutation_probe.py"],
        expected_effect="Replace probe string using controlled mutation",
        risk_level="low",
        reversible=True,
        estimated_validation_scope="local_compile_and_health",
        operations=[
            {
                "op": "replace_text",
                "path": "jarvis_stage3_artifacts/temp/safe_mutation_probe.py",
                "pattern": r'"before"',
                "replacement": '"after"',
            }
        ],
        metadata={
            "block": "safe_mutation_foundation",
            "verify": True,
        },
    )

    result = foundation.execute_candidate(candidate)
    metrics = foundation.collect_metrics()

    accepted = result.final_outcome == "accepted"
    current_text = target_file.read_text(encoding="utf-8")
    payload = {
        "accepted": accepted,
        "final_outcome": result.final_outcome,
        "apply_status": result.apply_status,
        "validation_status": result.validation_status,
        "rollback_status": result.rollback_status,
        "touched_files": result.touched_files,
        "target_contains_after": '"after"' in current_text,
        "metrics": metrics,
    }

    output_path = project_root / "jarvis_stage3_artifacts" / "temp" / "safe_mutation_foundation_verify_result.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if accepted and payload["target_contains_after"] else 1


if __name__ == "__main__":
    raise SystemExit(main())