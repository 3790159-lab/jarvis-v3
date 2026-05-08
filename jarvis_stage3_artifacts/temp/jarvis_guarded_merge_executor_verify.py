from __future__ import annotations
import json
import requests

BASE = "http://127.0.0.1:8029"

def req(method: str, url: str, json_body=None):
    r = requests.request(method, url, json=json_body, timeout=120)
    try:
        body = r.json()
    except Exception:
        body = r.text
    return {"status_code": r.status_code, "ok": 200 <= r.status_code < 300, "body": body}

health = req("GET", f"{BASE}/health")
status = req("GET", f"{BASE}/api/guarded-merge/status")
discover = req("GET", f"{BASE}/api/guarded-merge/discover-approved")
package_all = req("POST", f"{BASE}/api/guarded-merge/package-all-approved")
register_blueprints = req("POST", f"{BASE}/api/guarded-merge/blueprints/register-defaults")
blueprints = req("GET", f"{BASE}/api/guarded-merge/blueprints")

print("=== GUARDED MERGE HEALTH ===")
print(json.dumps(health, ensure_ascii=False, indent=2))
print()
print("=== GUARDED MERGE STATUS ===")
print(json.dumps(status, ensure_ascii=False, indent=2))
print()
print("=== GUARDED MERGE DISCOVER APPROVED ===")
print(json.dumps(discover, ensure_ascii=False, indent=2))
print()
print("=== GUARDED MERGE PACKAGE ALL ===")
print(json.dumps(package_all, ensure_ascii=False, indent=2))
print()
print("=== GUARDED MERGE BLUEPRINTS REGISTERED ===")
print(json.dumps(register_blueprints, ensure_ascii=False, indent=2))
print()
print("=== GUARDED MERGE BLUEPRINTS ===")
print(json.dumps(blueprints, ensure_ascii=False, indent=2))