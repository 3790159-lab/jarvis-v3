from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.jarvis_quality_hardening import JarvisQualityHardening


def main() -> int:
    hardening = JarvisQualityHardening(project_root=PROJECT_ROOT)
    report = hardening.run_quality_hardening(dry_run_cleanup=True)

    payload = {
        "status": report.status,
        "memory": report.idempotent_memory,
        "telegram": report.telegram_readiness,
        "sessions": {
            "status": report.session_summary.get("status"),
            "sessions_count": report.session_summary.get("sessions_count"),
            "completed_sessions": report.session_summary.get("completed_sessions"),
            "failed_sessions": report.session_summary.get("failed_sessions"),
            "total_applies": report.session_summary.get("total_applies"),
        },
        "retention": {
            "dry_run": report.retention.get("dry_run"),
            "total_cleanup_candidates": report.retention.get("total_cleanup_candidates"),
            "total_moved": report.retention.get("total_moved"),
        },
        "recommendations": report.recommendations,
    }

    out = PROJECT_ROOT / "jarvis_stage3_artifacts" / "temp" / "jarvis_quality_hardening_verify_result.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))

    ok = (
        report.status in {"ok", "ok_with_readiness_gap", "degraded"}
        and report.session_summary.get("status") in {"ok", "skipped"}
        and isinstance(report.recommendations, list)
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())