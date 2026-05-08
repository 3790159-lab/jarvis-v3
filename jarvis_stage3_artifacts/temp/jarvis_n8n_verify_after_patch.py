from __future__ import annotations

import json
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
OUT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "n8n_truth_probe"
OUT_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")
OUT_PATH = OUT_DIR / f"n8n_verify_after_patch_{STAMP}.json"

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

health = req("GET", f"{BACKEND}/api/n8n/materializer/health")

suffix = time.strftime("%Y%m%d%H%M%S")
body = {
    "name": f"jarvis-stable-{suffix}",
    "webhook_path": f"jarvis-stable-{suffix}",
    "response_text": "Jarvis stable webhook",
    "probe_payload": {
        "source": "jarvis_verify_after_patch",
        "ts": time.time(),
        "message": "hello from verify script",
    },
}

publish = req(
    "POST",
    f"{BACKEND}/api/n8n/materializer/publish-and-probe",
    json=body,
    headers={"Content-Type": "application/json"},
)

report = {
    "health": health,
    "publish_and_probe": publish,
}

OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print("=== MATERIALIZER HEALTH ===")
print(json.dumps(health, ensure_ascii=False, indent=2))
print()
print("=== PUBLISH AND PROBE ===")
print(json.dumps(publish, ensure_ascii=False, indent=2))
print()
print("=== REPORT PATH ===")
print(str(OUT_PATH))