from __future__ import annotations

import json
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
OUT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "n8n_truth_probe"
OUT_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")
OUT_PATH = OUT_DIR / f"n8n_final_auth_fix_verify_{STAMP}.json"

BASE = "http://127.0.0.1:8035"

def req(method, url, **kwargs):
    r = requests.request(method, url, timeout=120, allow_redirects=False, **kwargs)
    try:
        data = r.json()
    except Exception:
        data = r.text
    return {
        "status_code": r.status_code,
        "ok": 200 <= r.status_code < 300,
        "headers": {k: v for k, v in r.headers.items() if k.lower() in {"content-type", "location", "server", "x-powered-by"}},
        "body": data,
    }

suffix = time.strftime("%Y%m%d%H%M%S")
payload_body = {
    "name": f"jarvis-final-{suffix}",
    "webhook_path": f"jarvis-final-{suffix}",
    "response_text": "Jarvis final auth fixed webhook",
}

health = req("GET", f"{BASE}/health")
config = req("GET", f"{BASE}/config")
create_only = req(
    "POST",
    f"{BASE}/create-only",
    json=payload_body,
    headers={"Content-Type": "application/json"},
)

report = {
    "health": health,
    "config": config,
    "create_only": create_only,
}

OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print("=== FINAL SIDECAR HEALTH ===")
print(json.dumps(health, ensure_ascii=False, indent=2))
print()
print("=== FINAL SIDECAR CONFIG ===")
print(json.dumps(config, ensure_ascii=False, indent=2))
print()
print("=== FINAL SIDECAR CREATE ONLY ===")
print(json.dumps(create_only, ensure_ascii=False, indent=2))
print()
print("=== REPORT PATH ===")
print(str(OUT_PATH))