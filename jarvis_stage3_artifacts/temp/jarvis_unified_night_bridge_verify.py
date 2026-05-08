from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_unified_night_bridge import run_cli


def main() -> int:
    payload = run_cli(project_root=str(PROJECT_ROOT), max_iterations=2, degrade_on_failure=True)

    output_path = PROJECT_ROOT / "jarvis_stage3_artifacts" / "temp" / "jarvis_unified_night_bridge_verify_result.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))

    session = payload["session"]
    metrics = payload["metrics"]

    ok = (
        session["status"] in {"completed", "degraded"}
        and len(session["iterations"]) >= 1
        and metrics["sessions_count"] >= 1
        and "operator_summary" in session
        and len(session["operator_summary"]) > 0
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())