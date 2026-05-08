from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import requests

PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
OUT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "n8n_direct_runtime"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = os.environ["N8N_BASE_URL"].rstrip("/")
WEBHOOK_BASE_URL = os.environ.get("N8N_WEBHOOK_BASE_URL", BASE_URL).rstrip("/")
API_KEY = os.environ["N8N_API_KEY"]
TIMEOUT = int(os.environ.get("N8N_TIMEOUT_SECONDS", "45"))

STAMP = time.strftime("%Y%m%d_%H%M%S")
RUN_ID = uuid.uuid4().hex[:8]
WORKFLOW_NAME = f"jarvis-direct-{STAMP}-{RUN_ID}"
WEBHOOK_PATH = f"jarvis-direct-{STAMP}-{RUN_ID}"
REPORT_PATH = OUT_DIR / f"n8n_direct_create_{STAMP}_{RUN_ID}.json"


def request(
    method: str,
    path_or_url: str,
    *,
    json_body: Optional[Dict[str, Any]] = None,
    absolute: bool = False,
    expected: tuple[int, ...] = (200, 201),
) -> Dict[str, Any]:
    url = path_or_url if absolute else f"{BASE_URL}{path_or_url}"
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-N8N-API-KEY": API_KEY,
    }

    resp = requests.request(
        method=method.upper(),
        url=url,
        headers=headers,
        json=json_body,
        timeout=TIMEOUT,
        allow_redirects=False,
    )

    try:
        body = resp.json()
    except Exception:
        body = resp.text

    result = {
        "url": url,
        "status_code": resp.status_code,
        "ok": resp.status_code in expected,
        "headers": {k: v for k, v in resp.headers.items() if k.lower() in {"content-type", "location", "server", "x-powered-by"}},
        "body": body,
    }

    if resp.status_code not in expected:
        raise RuntimeError(json.dumps(result, ensure_ascii=False))

    return result


def extract_workflow_id(payload: Any) -> Optional[str]:
    if isinstance(payload, dict):
        for key in ("id", "workflowId"):
            if payload.get(key) is not None:
                return str(payload[key])

        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("id", "workflowId"):
                if data.get(key) is not None:
                    return str(data[key])

        items = payload.get("items")
        if isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, dict):
                for key in ("id", "workflowId"):
                    if first.get(key) is not None:
                        return str(first[key])
    return None


def build_known_good_payload(workflow_name: str, webhook_path: str) -> Dict[str, Any]:
    # Known-good candidate: B1_v1_with_settings_empty
    return {
        "name": workflow_name,
        "nodes": [
            {
                "name": "Jarvis Webhook",
                "type": "n8n-nodes-base.webhook",
                "typeVersion": 1,
                "position": [600, 300],
                "parameters": {
                    "httpMethod": "POST",
                    "path": webhook_path,
                    "options": {},
                },
            }
        ],
        "connections": {},
        "settings": {},
    }


report: Dict[str, Any] = {
    "base_url": BASE_URL,
    "webhook_base_url": WEBHOOK_BASE_URL,
    "workflow_name": WORKFLOW_NAME,
    "webhook_path": WEBHOOK_PATH,
    "payload_mode": "B1_v1_with_settings_empty",
}

# 1) Direct auth/list check
auth_check = request("GET", "/api/v1/workflows?limit=1", expected=(200,))
report["auth_check"] = auth_check

# 2) Create workflow
payload = build_known_good_payload(WORKFLOW_NAME, WEBHOOK_PATH)
report["used_payload"] = payload

create_result = request("POST", "/api/v1/workflows", json_body=payload, expected=(200, 201))
report["create_result"] = create_result

workflow_id = extract_workflow_id(create_result["body"])
report["workflow_id"] = workflow_id

if not workflow_id:
    raise RuntimeError("Workflow ID not found in create response")

# 3) Activate attempts
activation_attempts = []
for method, path, body, expected in [
    ("POST", f"/api/v1/workflows/{workflow_id}/activate", None, (200, 201, 204)),
    ("PATCH", f"/api/v1/workflows/{workflow_id}", {"active": True}, (200,)),
]:
    attempt = {
        "method": method,
        "path": path,
    }
    try:
        attempt["result"] = request(method, path, json_body=body, expected=expected)
        attempt["ok"] = True
    except Exception as exc:
        attempt["ok"] = False
        attempt["error"] = str(exc)
    activation_attempts.append(attempt)

report["activation_attempts"] = activation_attempts

# 4) Read workflow back
try:
    read_back = request("GET", f"/api/v1/workflows/{workflow_id}", expected=(200,))
    report["read_back"] = read_back
except Exception as exc:
    report["read_back_error"] = str(exc)

# 5) Probe production webhook
probe_url = f"{WEBHOOK_BASE_URL}/webhook/{WEBHOOK_PATH}"
report["probe_url"] = probe_url

time.sleep(4)

try:
    probe_resp = requests.post(
        probe_url,
        json={
            "source": "jarvis_direct_runtime",
            "ts": time.time(),
            "message": "hello from direct create runtime",
        },
        timeout=TIMEOUT,
        allow_redirects=False,
    )
    try:
        probe_body = probe_resp.json()
    except Exception:
        probe_body = probe_resp.text

    report["probe_result"] = {
        "status_code": probe_resp.status_code,
        "ok": 200 <= probe_resp.status_code < 300,
        "headers": {k: v for k, v in probe_resp.headers.items() if k.lower() in {"content-type", "location", "server", "x-powered-by"}},
        "body": probe_body,
    }
except Exception as exc:
    report["probe_error"] = f"{exc.__class__.__name__}: {exc}"

REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print("=== DIRECT AUTH CHECK ===")
print(json.dumps(report["auth_check"], ensure_ascii=False, indent=2))
print()
print("=== DIRECT CREATE RESULT ===")
print(json.dumps(report["create_result"], ensure_ascii=False, indent=2))
print()
print("=== DIRECT ACTIVATION ATTEMPTS ===")
print(json.dumps(report["activation_attempts"], ensure_ascii=False, indent=2))
print()
print("=== DIRECT READ BACK ===")
print(json.dumps(report.get("read_back", {"error": report.get("read_back_error")}), ensure_ascii=False, indent=2))
print()
print("=== DIRECT PROBE RESULT ===")
print(json.dumps(report.get("probe_result", {"error": report.get("probe_error")}), ensure_ascii=False, indent=2))
print()
print("=== REPORT PATH ===")
print(str(REPORT_PATH))