from __future__ import annotations

import json
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
OUT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "temp"
OUT_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")
OUT_PATH = OUT_DIR / f"jarvis_local_n8n_director_safe_v1_verify_{STAMP}.json"

BASE = "http://127.0.0.1:8022"

def req(method, url, json_body=None):
    r = requests.request(method, url, json=json_body, timeout=120, allow_redirects=False)
    try:
        body = r.json()
    except Exception:
        body = r.text
    return {
        "status_code": r.status_code,
        "ok": 200 <= r.status_code < 300,
        "body": body
    }

health = req("GET", f"{BASE}/health")
whoami = req("GET", f"{BASE}/__whoami")
version = req("GET", f"{BASE}/api/jarvis/n8n/version")
status_all = req("GET", f"{BASE}/api/jarvis/n8n/status/all?limit=20")
templates = req("GET", f"{BASE}/api/jarvis/n8n/templates")
register_custom = req(
    "POST",
    f"{BASE}/api/jarvis/n8n/templates/register",
    {
        "id": "custom_hook_v1",
        "title": "Custom Hook v1",
        "description": "Custom registered template",
        "purpose": "custom",
        "tags": ["custom", "webhook", "learning"],
        "path_prefix": "custom-hook-v1",
        "name_prefix": "custom-hook-v1",
        "smoke_payload": {
            "source": "custom_hook_v1",
            "message": "hello from custom hook v1"
        }
    }
)
recommend_custom = req("GET", f"{BASE}/api/jarvis/n8n/templates/recommend?purpose=custom")
custom_smoke = req(
    "POST",
    f"{BASE}/api/jarvis/n8n/templates/smoke",
    {
        "template_id": "custom_hook_v1",
        "name": f"custom-hook-v1-{STAMP}",
        "webhook_path": f"custom-hook-v1-{STAMP}",
        "payload": {
            "source": "jarvis_local_n8n_director_safe_v1_verify",
            "message": "hello from custom smoke",
            "timestamp": STAMP
        }
    }
)
memory_after_smoke = req("GET", f"{BASE}/api/jarvis/n8n/memory")

report = {
    "health": health,
    "whoami": whoami,
    "version": version,
    "status_all": status_all,
    "templates": templates,
    "register_custom": register_custom,
    "recommend_custom": recommend_custom,
    "custom_smoke": custom_smoke,
    "memory_after_smoke": memory_after_smoke
}

OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print("=== DIRECTOR SAFE V1 HEALTH ===")
print(json.dumps(health, ensure_ascii=False, indent=2))
print()
print("=== DIRECTOR SAFE V1 WHOAMI ===")
print(json.dumps(whoami, ensure_ascii=False, indent=2))
print()
print("=== DIRECTOR SAFE V1 VERSION ===")
print(json.dumps(version, ensure_ascii=False, indent=2))
print()
print("=== DIRECTOR SAFE V1 STATUS ALL ===")
print(json.dumps(status_all, ensure_ascii=False, indent=2))
print()
print("=== DIRECTOR SAFE V1 REGISTER CUSTOM ===")
print(json.dumps(register_custom, ensure_ascii=False, indent=2))
print()
print("=== DIRECTOR SAFE V1 RECOMMEND CUSTOM ===")
print(json.dumps(recommend_custom, ensure_ascii=False, indent=2))
print()
print("=== DIRECTOR SAFE V1 CUSTOM SMOKE ===")
print(json.dumps(custom_smoke, ensure_ascii=False, indent=2))
print()
print("=== DIRECTOR SAFE V1 MEMORY AFTER SMOKE ===")
print(json.dumps(memory_after_smoke, ensure_ascii=False, indent=2))
print()
print("=== REPORT PATH ===")
print(str(OUT_PATH))