from __future__ import annotations

import json
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
OUT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "temp"
OUT_DIR.mkdir(parents=True, exist_ok=True)

STAMP = time.strftime("%Y%m%d_%H%M%S")
OUT_PATH = OUT_DIR / f"jarvis_local_n8n_director_pro_core_verify_v2_{STAMP}.json"
BASE = "http://127.0.0.1:8024"


def req(method: str, url: str, json_body=None):
    r = requests.request(method, url, json=json_body, timeout=180, allow_redirects=False)
    try:
        body = r.json()
    except Exception:
        body = r.text
    return {
        "status_code": r.status_code,
        "ok": 200 <= r.status_code < 300,
        "body": body,
    }


health = req("GET", f"{BASE}/health")
whoami = req("GET", f"{BASE}/__whoami")
abilities = req("GET", f"{BASE}/api/jarvis/n8n/abilities")
status_all = req("GET", f"{BASE}/api/jarvis/n8n/status/all?limit=200")
register_custom = req(
    "POST",
    f"{BASE}/api/jarvis/n8n/templates/register",
    {
        "id": "custom_hook_v2",
        "title": "Custom Hook v2",
        "description": "Custom user registered template",
        "purpose": "custom",
        "tags": ["custom", "webhook", "learning"],
        "path_prefix": "custom-hook-v2",
        "name_prefix": "custom-hook-v2",
        "smoke_payload": {
            "source": "custom_hook_v2",
            "message": "hello from custom hook v2",
        },
    },
)
planner_recommend = req(
    "GET",
    f"{BASE}/api/jarvis/n8n/planner/recommend?goal=Need%20a%20custom%20webhook%20for%20operator%20events",
)
render_custom = req(
    "POST",
    f"{BASE}/api/jarvis/n8n/templates/render",
    {
        "template_id": "custom_hook_v2",
        "name": f"custom-hook-v2-{STAMP}",
        "webhook_path": f"custom-hook-v2-{STAMP}",
        "activate": False,
        "reuse_existing": True,
        "metadata": {},
    },
)
inventory_before = req("GET", f"{BASE}/api/jarvis/n8n/workflows/inventory?limit=200")
cleanup_report = req(
    "GET",
    f"{BASE}/api/jarvis/n8n/lifecycle/cleanup-report?older_than_hours=0&max_keep_per_template=3&limit=200",
)
custom_smoke = req(
    "POST",
    f"{BASE}/api/jarvis/n8n/templates/smoke",
    {
        "template_id": "custom_hook_v2",
        "name": f"custom-hook-v2-{STAMP}",
        "webhook_path": f"custom-hook-v2-{STAMP}",
        "payload": {
            "source": "jarvis_local_n8n_director_pro_core_verify_v2",
            "message": "hello from custom director pro core smoke v2",
            "timestamp": STAMP,
        },
        "reuse_existing": False,
        "wait_seconds": 4,
    },
)
memory_after = req("GET", f"{BASE}/api/jarvis/n8n/memory")
inventory_after = req("GET", f"{BASE}/api/jarvis/n8n/workflows/inventory?limit=200")

report = {
    "health": health,
    "whoami": whoami,
    "abilities": abilities,
    "status_all": status_all,
    "register_custom": register_custom,
    "planner_recommend": planner_recommend,
    "render_custom": render_custom,
    "inventory_before": inventory_before,
    "cleanup_report": cleanup_report,
    "custom_smoke": custom_smoke,
    "memory_after": memory_after,
    "inventory_after": inventory_after,
}
OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

for title, payload in report.items():
    print()
    print(f"=== {title.upper()} ===")
    print(json.dumps(payload, ensure_ascii=False, indent=2))

print()
print("=== REPORT PATH ===")
print(str(OUT_PATH))