from __future__ import annotations
import json
import requests

TARGETS = {
    "supervisor_health": ("GET", "http://127.0.0.1:8015/health", None),
    "supervisor_healthz": ("GET", "http://127.0.0.1:8015/healthz", None),
    "supervisor_router_status": ("GET", "http://127.0.0.1:8015/_jarvis/router/status", None),
    "director_health": ("GET", "http://127.0.0.1:8024/health", None),
    "director_policy": ("GET", "http://127.0.0.1:8024/api/jarvis/n8n/lifecycle/policy", None),
    "director_managed_summary": ("GET", "http://127.0.0.1:8024/api/jarvis/n8n/lifecycle/managed-summary", None),
    "director_audit_snapshot": ("GET", "http://127.0.0.1:8024/api/jarvis/n8n/lifecycle/audit-snapshot", None),
    "telegram_module_health": ("GET", "http://127.0.0.1:8110/health", None),
    "telegram_module_config": ("GET", "http://127.0.0.1:8110/config", None),
    "telegram_module_preview": ("POST", "http://127.0.0.1:8110/respond-preview", {"text": "Hello from fixed telegram module"}),
}
def req(method: str, url: str, payload=None):
    try:
        r = requests.request(method, url, json=payload, timeout=120)
        try:
            body = r.json()
        except Exception:
            body = r.text
        return {"status_code": r.status_code, "ok": 200 <= r.status_code < 300, "body": body}
    except Exception as exc:
        return {"status_code": 0, "ok": False, "body": str(exc)}
results = {name: req(method, url, payload) for name, (method, url, payload) in TARGETS.items()}
print("=== CONTINUE VERIFY ===")
print(json.dumps(results, ensure_ascii=False, indent=2))