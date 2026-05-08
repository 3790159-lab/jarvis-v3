from __future__ import annotations
import json
import requests

BASE = "http://127.0.0.1:8028"

def req(method: str, url: str, json_body=None):
    r = requests.request(method, url, json=json_body, timeout=120)
    try:
        body = r.json()
    except Exception:
        body = r.text
    return {"status_code": r.status_code, "ok": 200 <= r.status_code < 300, "body": body}

health = req("GET", f"{BASE}/health")
config = req("GET", f"{BASE}/api/self-evolution/config")
status = req("GET", f"{BASE}/api/self-evolution/status")
queue = req("GET", f"{BASE}/api/self-evolution/proposals")
once = req("POST", f"{BASE}/api/self-evolution/run/once", {"focus": "reliability"})

print("=== HOTFIX HEALTH ===")
print(json.dumps(health, ensure_ascii=False, indent=2))
print()
print("=== HOTFIX CONFIG ===")
print(json.dumps(config, ensure_ascii=False, indent=2))
print()
print("=== HOTFIX STATUS ===")
print(json.dumps(status, ensure_ascii=False, indent=2))
print()
print("=== HOTFIX QUEUE ===")
print(json.dumps(queue, ensure_ascii=False, indent=2))
print()
print("=== HOTFIX RUN ONCE ===")
print(json.dumps(once, ensure_ascii=False, indent=2))