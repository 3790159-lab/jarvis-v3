from __future__ import annotations

import json
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
OUT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "temp"
OUT_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")
OUT_PATH = OUT_DIR / f"jarvis_local_n8n_gateway_verify_{STAMP}.json"

BASE = "http://127.0.0.1:8016"

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
ping = req("GET", f"{BASE}/api/jarvis/n8n/ping")
routes = req("GET", f"{BASE}/api/jarvis/n8n/_routes")
bridge_health = req("GET", f"{BASE}/api/jarvis/n8n/health")
bridge_config = req("GET", f"{BASE}/api/jarvis/n8n/config")
public_api_check = req("GET", f"{BASE}/api/jarvis/n8n/public-api-check")
smoke = req(
    "POST",
    f"{BASE}/api/jarvis/n8n/smoke/local-webhook",
    {
        "name": f"jarvis-gateway-smoke-{STAMP}",
        "webhook_path": f"jarvis-gateway-smoke-{STAMP}",
    },
)

report = {
    "health": health,
    "ping": ping,
    "routes": routes,
    "bridge_health": bridge_health,
    "bridge_config": bridge_config,
    "public_api_check": public_api_check,
    "smoke": smoke,
}

OUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print("=== GATEWAY HEALTH ===")
print(json.dumps(health, ensure_ascii=False, indent=2))
print()
print("=== GATEWAY PING ===")
print(json.dumps(ping, ensure_ascii=False, indent=2))
print()
print("=== GATEWAY ROUTES ===")
print(json.dumps(routes, ensure_ascii=False, indent=2))
print()
print("=== GATEWAY -> N8N BRIDGE HEALTH ===")
print(json.dumps(bridge_health, ensure_ascii=False, indent=2))
print()
print("=== GATEWAY -> N8N BRIDGE CONFIG ===")
print(json.dumps(bridge_config, ensure_ascii=False, indent=2))
print()
print("=== GATEWAY -> N8N PUBLIC API CHECK ===")
print(json.dumps(public_api_check, ensure_ascii=False, indent=2))
print()
print("=== GATEWAY -> N8N SMOKE ===")
print(json.dumps(smoke, ensure_ascii=False, indent=2))
print()
print("=== REPORT PATH ===")
print(str(OUT_PATH))