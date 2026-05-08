from __future__ import annotations

import json
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
OUT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "temp"
OUT_DIR.mkdir(parents=True, exist_ok=True)

STAMP = time.strftime("%Y%m%d_%H%M%S")
OUT_PATH = OUT_DIR / f"jarvis_local_n8n_director_pro_core_verify_{STAMP}.json"
BASE = "http://127.0.0.1:8024"


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
abilities = req("GET", f"{BASE}/api/jarvis/n8n/abilities")
status_all = req("GET", f"{BASE}/api/jarvis/n8n/status/all?limit=20")

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

recommend_custom = req("GET", f"{BASE}/api/jarvis/n8n/template-recommendations?purpose=custom")

render_custom = req(
    "POST",
    f"{BASE}/api/jarvis/n8n/templates/render",
    {
        "template_id": "custom_hook_v2",
        "name": f"custom-hook-v2-{STAMP}",
        "webhook_path": f"custom-hook-v2-{STAMP}",
        "activate": False,
    },
)

custom_smoke = req(
    "POST",
    f"{BASE}/api/jarvis/n8n/templates/smoke",
    {
        "template_id": "custom_hook_v2",
        "name": f"custom-hook-v2-{STAMP}",
        "webhook_path": f"custom-hook-v2-{STAMP}",
        "payload": {
            "source": "jarvis_local_n8n_director_pro_core_verify",
            "message": "hello from custom director pro core smoke",
            "timestamp": STAMP,
        },
    },
)

memory_after = req("GET", f"{BASE}/api/jarvis/n8n/memory")

report = {
    "health": health,
    "whoami": whoami,
    "version": version,
    "abilities": abilities,
    "status_all": status_all,
    "register_custom": register_custom,
    "recommend_custom": recommend_custom,
    "render_custom": render_custom,
    "custom_smoke": custom_smoke,
    "memory_after": memory_after,
}

OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print("=== DIRECTOR PRO CORE HEALTH ===")
print(json.dumps(health, ensure_ascii=False, indent=2))
print()

print("=== DIRECTOR PRO CORE WHOAMI ===")
print(json.dumps(whoami, ensure_ascii=False, indent=2))
print()

print("=== DIRECTOR PRO CORE VERSION ===")
print(json.dumps(version, ensure_ascii=False, indent=2))
print()

print("=== DIRECTOR PRO CORE ABILITIES ===")
print(json.dumps(abilities, ensure_ascii=False, indent=2))
print()

print("=== DIRECTOR PRO CORE STATUS ALL ===")
print(json.dumps(status_all, ensure_ascii=False, indent=2))
print()

print("=== DIRECTOR PRO CORE REGISTER CUSTOM ===")
print(json.dumps(register_custom, ensure_ascii=False, indent=2))
print()

print("=== DIRECTOR PRO CORE RECOMMEND CUSTOM ===")
print(json.dumps(recommend_custom, ensure_ascii=False, indent=2))
print()

print("=== DIRECTOR PRO CORE RENDER CUSTOM ===")
print(json.dumps(render_custom, ensure_ascii=False, indent=2))
print()

print("=== DIRECTOR PRO CORE CUSTOM SMOKE ===")
print(json.dumps(custom_smoke, ensure_ascii=False, indent=2))
print()

print("=== DIRECTOR PRO CORE MEMORY AFTER ===")
print(json.dumps(memory_after, ensure_ascii=False, indent=2))
print()

print("=== REPORT PATH ===")
print(str(OUT_PATH))