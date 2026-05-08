from __future__ import annotations

import json
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
OUT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "temp"
OUT_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")
OUT_PATH = OUT_DIR / f"jarvis_local_n8n_identity_v3_verify_{STAMP}.json"

BASE = "http://127.0.0.1:8018"

def req(method, url, json_body=None):
    r = requests.request(method, url, json=json_body, timeout=90, allow_redirects=False)
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
smoke = req(
    "POST",
    f"{BASE}/api/jarvis/n8n/smoke/local-webhook",
    {
        "name": f"jarvis-identity-v3-smoke-{STAMP}",
        "webhook_path": f"jarvis-identity-v3-smoke-{STAMP}",
    },
)

report = {
    "health": health,
    "whoami": whoami,
    "version": version,
    "status_all": status_all,
    "summary": summary,
    "smoke": smoke,
}

OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print("=== IDENTITY V3 HEALTH ===")
print(json.dumps(health, ensure_ascii=False, indent=2))
print()
print("=== IDENTITY V3 WHOAMI ===")
print(json.dumps(whoami, ensure_ascii=False, indent=2))
print()
print("=== IDENTITY V3 VERSION ===")
print(json.dumps(version, ensure_ascii=False, indent=2))
print()
print("=== IDENTITY V3 STATUS ALL ===")
print(json.dumps(status_all, ensure_ascii=False, indent=2))
print()
print("=== IDENTITY V3 WORKFLOWS SUMMARY ===")
print(json.dumps(summary, ensure_ascii=False, indent=2))
print()
print("=== IDENTITY V3 SMOKE ===")
print(json.dumps(smoke, ensure_ascii=False, indent=2))
print()
print("=== REPORT PATH ===")
print(str(OUT_PATH))