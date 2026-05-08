from __future__ import annotations

import json
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
OUT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "temp"
OUT_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")
OUT_PATH = OUT_DIR / f"jarvis_local_n8n_template_pack_v4_verify_{STAMP}.json"

BASE = "http://127.0.0.1:8020"

def req(method, url, json_body=None):
    r = requests.request(method, url, json=json_body, timeout=120, allow_redirects=False)
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
version = req("GET", f"{BASE}/api/jarvis/n8n/version")
status_all = req("GET", f"{BASE}/api/jarvis/n8n/status/all?limit=20")
summary = req("GET", f"{BASE}/api/jarvis/n8n/workflows/summary?limit=20")
templates = req("GET", f"{BASE}/api/jarvis/n8n/templates")
template_smoke = req(
    "POST",
    f"{BASE}/api/jarvis/n8n/templates/smoke",
    {
        "template_id": "webhook_inbox_v1",
        "name": f"webhook-inbox-v1-{STAMP}",
        "webhook_path": f"webhook-inbox-v1-{STAMP}",
        "payload": {
            "source": "jarvis_local_n8n_template_pack_v4_verify",
            "message": "hello from template smoke",
            "timestamp": STAMP,
        },
    },
)

report = {
    "health": health,
    "whoami": whoami,
    "version": version,
    "status_all": status_all,
    "summary": summary,
    "templates": templates,
    "template_smoke": template_smoke,
}

OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print("=== TEMPLATE PACK V4 HEALTH ===")
print(json.dumps(health, ensure_ascii=False, indent=2))
print()
print("=== TEMPLATE PACK V4 WHOAMI ===")
print(json.dumps(whoami, ensure_ascii=False, indent=2))
print()
print("=== TEMPLATE PACK V4 VERSION ===")
print(json.dumps(version, ensure_ascii=False, indent=2))
print()
print("=== TEMPLATE PACK V4 STATUS ALL ===")
print(json.dumps(status_all, ensure_ascii=False, indent=2))
print()
print("=== TEMPLATE PACK V4 WORKFLOWS SUMMARY ===")
print(json.dumps(summary, ensure_ascii=False, indent=2))
print()
print("=== TEMPLATE PACK V4 TEMPLATES ===")
print(json.dumps(templates, ensure_ascii=False, indent=2))
print()
print("=== TEMPLATE PACK V4 TEMPLATE SMOKE ===")
print(json.dumps(template_smoke, ensure_ascii=False, indent=2))
print()
print("=== REPORT PATH ===")
print(str(OUT_PATH))