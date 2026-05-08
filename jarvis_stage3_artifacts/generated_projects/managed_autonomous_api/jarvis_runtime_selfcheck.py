from __future__ import annotations

import json
from pathlib import Path

from app.main import app


ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "artifacts" / "jarvis_control"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = OUT_DIR / "runtime_selfcheck.json"

required_routes = {
    "/health": {"GET"},
    "/api/execution/health": {"GET"},
    "/api/execution/missions/{mission_id}/run": {"POST"},
    "/api/tools/health": {"GET"},
    "/api/tools/config": {"GET"},
    "/api/feedback/health": {"GET"},
    "/api/feedback/evaluate": {"POST"},
    "/api/mission-memory-v2/health": {"GET"},
    "/api/mission-memory-v2/record-success": {"POST"},
    "/api/hitl/health": {"GET"},
    "/api/hitl/approvals": {"GET", "POST"},
    "/api/runtime/routes": {"GET"},
}

present = {}
for route in app.routes:
    path = getattr(route, "path", None)
    methods = set(getattr(route, "methods", []) or [])
    if path:
        present[path] = methods

checks = []
all_ok = True

for path, expected_methods in required_routes.items():
    actual_methods = present.get(path, set())
    ok = expected_methods.issubset(actual_methods)
    if not ok:
        all_ok = False
    checks.append({
        "path": path,
        "expected_methods": sorted(expected_methods),
        "actual_methods": sorted(actual_methods),
        "ok": ok,
    })

payload = {
    "status": "healthy" if all_ok else "needs_attention",
    "checks": checks,
    "route_count": len(present),
}

OUT_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))
