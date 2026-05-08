from __future__ import annotations

import json
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
OUT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "n8n_truth_probe"
OUT_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")
OUT_PATH = OUT_DIR / f"n8n_stage_b_wrapper_verify_{STAMP}.json"

BACKEND = "http://127.0.0.1:8015"

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
    "name": f"jarvis-v2-{suffix}",
    "webhook_path": f"jarvis-v2-{suffix}",
    "response_text": "Jarvis stage B v2 wrapper webhook",
}

health = req("GET", f"{BACKEND}/api/n8n/materializer-v2/health")
debug_state = req("GET", f"{BACKEND}/api/n8n/materializer-v2/debug-state")
dry_run = req(
    "POST",
    f"{BACKEND}/api/n8n/materializer-v2/dry-run-payload",
    json=payload_body,
    headers={"Content-Type": "application/json"},
)
create_only = req(
    "POST",
    f"{BACKEND}/api/n8n/materializer-v2/create-only",
    json=payload_body,
    headers={"Content-Type": "application/json"},
)

report = {
    "health": health,
    "debug_state": debug_state,
    "dry_run": dry_run,
    "create_only": create_only,
}

OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print("=== V2 HEALTH ===")
print(json.dumps(health, ensure_ascii=False, indent=2))
print()
print("=== V2 DEBUG STATE ===")
print(json.dumps(debug_state, ensure_ascii=False, indent=2))
print()
print("=== V2 DRY RUN PAYLOAD ===")
print(json.dumps(dry_run, ensure_ascii=False, indent=2))
print()
print("=== V2 CREATE ONLY ===")
print(json.dumps(create_only, ensure_ascii=False, indent=2))
print()
print("=== REPORT PATH ===")
print(str(OUT_PATH))