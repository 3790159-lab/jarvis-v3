from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_external_systems_readiness import JarvisExternalSystemsReadiness


def main() -> int:
    readiness = JarvisExternalSystemsReadiness(project_root=PROJECT_ROOT)
    profiles = readiness.assess_all()
    metrics = readiness.collect_metrics()

    payload = {
        "profiles_count": len(profiles),
        "ready_count": sum(1 for p in profiles if p.ready),
        "not_ready_count": sum(1 for p in profiles if not p.ready),
        "system_names": [p.system_name for p in profiles],
        "modes": [p.mode for p in profiles],
        "next_best_actions": [p.next_best_action for p in profiles[:5]],
        "metrics": metrics,
    }

    output_path = PROJECT_ROOT / "jarvis_stage3_artifacts" / "temp" / "jarvis_external_systems_readiness_verify_result.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))

    ok = (
        len(profiles) >= 5
        and metrics["profiles_count"] >= 5
        and "telegram_bridge" in payload["system_names"]
        and "n8n_local" in payload["system_names"]
        and "google_workspace" in payload["system_names"]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())