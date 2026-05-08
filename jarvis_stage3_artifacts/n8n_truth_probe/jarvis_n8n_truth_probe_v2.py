from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests


PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
OUT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "n8n_truth_probe"
OUT_DIR.mkdir(parents=True, exist_ok=True)
STAMP = time.strftime("%Y%m%d_%H%M%S")
REPORT_PATH = OUT_DIR / f"n8n_truth_probe_v2_{STAMP}.json"


def load_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        return data

    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        data[key] = value
    return data


def env_value(name: str, env_file: dict[str, str], default: Optional[str] = None) -> Optional[str]:
    return os.getenv(name) or env_file.get(name) or default


def summarize_headers(headers: requests.structures.CaseInsensitiveDict) -> dict[str, str]:
    keep = {}
    for key in [
        "content-type",
        "location",
        "server",
        "x-powered-by",
        "cf-ray",
        "set-cookie",
        "www-authenticate",
    ]:
        if key in headers:
            keep[key] = headers[key]
    return keep


def parse_json_loose(text: str) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def request(
    method: str,
    url: str,
    *,
    headers: Optional[dict[str, str]] = None,
    json_body: Optional[dict[str, Any]] = None,
    timeout: int = 60,
    allow_redirects: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "method": method,
        "url": url,
        "status_code": None,
        "headers": {},
        "body_text": None,
        "body_json": None,
        "error": None,
        "final_url": None,
    }
    try:
        resp = requests.request(
            method=method,
            url=url,
            headers=headers,
            json=json_body,
            timeout=timeout,
            allow_redirects=allow_redirects,
        )
        result["ok"] = resp.ok
        result["status_code"] = resp.status_code
        result["headers"] = summarize_headers(resp.headers)
        result["body_text"] = resp.text[:8000] if resp.text else ""
        result["body_json"] = parse_json_loose(resp.text)
        result["final_url"] = str(resp.url)
        return result
    except requests.RequestException as exc:
        result["error"] = f"{exc.__class__.__name__}: {exc}"
        if getattr(exc, "response", None) is not None:
            resp = exc.response
            result["status_code"] = resp.status_code
            result["headers"] = summarize_headers(resp.headers)
            result["body_text"] = resp.text[:8000] if resp.text else ""
            result["body_json"] = parse_json_loose(resp.text)
            result["final_url"] = str(resp.url)
        return result


def get_workflow_id(obj: Any) -> Optional[str]:
    if not isinstance(obj, dict):
        return None

    for key in ("id", "workflowId"):
        if obj.get(key):
            return str(obj[key])

    data = obj.get("data")
    if isinstance(data, dict):
        for key in ("id", "workflowId"):
            if data.get(key):
                return str(data[key])

    items = obj.get("items")
    if isinstance(items, list) and items:
        first = items[0]
        if isinstance(first, dict):
            for key in ("id", "workflowId"):
                if first.get(key):
                    return str(first[key])

    return None


env_file = load_env_file(PROJECT_ROOT / ".env")
n8n_base_url = (env_value("N8N_BASE_URL", env_file, "https://daniliyc.app.n8n.cloud") or "").rstrip("/")
api_key = env_value("N8N_API_KEY", env_file)
backend_base_url = env_value("JARVIS_BACKEND_BASE_URL", env_file, "http://127.0.0.1:8015")

if not api_key:
    print("N8N_API_KEY is empty in both shell env and .env", file=sys.stderr)
    sys.exit(2)

api_headers = {
    "Accept": "application/json",
    "X-N8N-API-KEY": api_key,
}

report: dict[str, Any] = {
    "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "n8n_base_url": n8n_base_url,
    "backend_base_url": backend_base_url,
}

# 1) Compare with backend materializer health
backend_health = request(
    "GET",
    f"{backend_base_url}/api/n8n/materializer/health",
    headers={"Accept": "application/json"},
    timeout=20,
)
report["backend_materializer_health"] = backend_health

# 2) Public API probes
public_list = request(
    "GET",
    f"{n8n_base_url}/api/v1/workflows?limit=1",
    headers=api_headers,
    timeout=60,
)
report["public_list"] = public_list

public_get_one = request(
    "GET",
    f"{n8n_base_url}/api/v1/workflows",
    headers=api_headers,
    timeout=60,
)
report["public_list_no_query"] = public_get_one

rest_probe = request(
    "GET",
    f"{n8n_base_url}/rest/workflows",
    headers=api_headers,
    timeout=60,
)
report["rest_probe"] = rest_probe

# 3) Candidate create payloads
suffix = f"{int(time.time())}"
workflow_name = f"jarvis-diag-{suffix}"
webhook_path = f"jarvis-diag-{suffix}"

candidates = [
    {
        "label": "A1_v1_connections_empty",
        "body": {
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
        },
    },
    {
        "label": "A2_v2_connections_empty",
        "body": {
            "name": workflow_name,
            "nodes": [
                {
                    "name": "Jarvis Webhook",
                    "type": "n8n-nodes-base.webhook",
                    "typeVersion": 2,
                    "position": [600, 300],
                    "parameters": {
                        "httpMethod": "POST",
                        "path": webhook_path,
                        "options": {},
                    },
                }
            ],
            "connections": {},
        },
    },
    {
        "label": "B1_v1_with_settings_empty",
        "body": {
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
        },
    },
    {
        "label": "C1_v2_bare_params",
        "body": {
            "name": workflow_name,
            "nodes": [
                {
                    "name": "Jarvis Webhook",
                    "type": "n8n-nodes-base.webhook",
                    "typeVersion": 2,
                    "position": [600, 300],
                    "parameters": {
                        "httpMethod": "POST",
                        "path": webhook_path,
                    },
                }
            ],
            "connections": {},
        },
    },
]

candidate_results: list[dict[str, Any]] = []
success: Optional[dict[str, Any]] = None

for item in candidates:
    resp = request(
        "POST",
        f"{n8n_base_url}/api/v1/workflows",
        headers=api_headers,
        json_body=item["body"],
        timeout=120,
    )
    row = {
        "label": item["label"],
        "request_body": item["body"],
        "response": resp,
        "workflow_id": get_workflow_id(resp.get("body_json")),
    }
    candidate_results.append(row)
    if resp["ok"] and row["workflow_id"]:
        success = row
        break

report["create_candidates"] = candidate_results

# 4) Activate + probe if create worked
activation_attempts: list[dict[str, Any]] = []
webhook_probe: Optional[dict[str, Any]] = None

if success:
    wid = success["workflow_id"]

    for spec in [
        ("POST", f"{n8n_base_url}/api/v1/workflows/{wid}/activate", None),
        ("POST", f"{n8n_base_url}/api/v1/workflows/{wid}/publish", None),
        ("PATCH", f"{n8n_base_url}/api/v1/workflows/{wid}", {"active": True}),
    ]:
        method, url, body = spec
        activation_attempts.append(
            request(method, url, headers=api_headers, json_body=body, timeout=120)
        )

    webhook_probe = request(
        "POST",
        f"{n8n_base_url}/webhook/{webhook_path}",
        headers={"Accept": "application/json"},
        json_body={"source": "jarvis_truth_probe_v2", "ts": time.time()},
        timeout=120,
    )

report["activation_attempts"] = activation_attempts
report["webhook_probe"] = webhook_probe

# 5) Summary
public_body = (public_list.get("body_text") or "").lower()
rest_body = (rest_probe.get("body_text") or "").lower()

cause = "unknown"
next_plan = "inspect report body_text entries"

if report["backend_materializer_health"].get("ok") and not public_list.get("ok"):
    cause = "backend_can_reach_n8n_but_local_direct_probe_cannot"
    next_plan = "use Python/requests-based diagnostics only, compare API key and base URL, then continue with template-first approach if create remains blocked"
elif public_list.get("ok") and not success:
    cause = "public_api_reachable_but_create_schema_blocked"
    next_plan = "stop creating workflows from scratch and move to base-template duplication"
elif not public_list.get("ok") and "free trial" in public_body:
    cause = "public_api_unavailable_on_current_plan"
    next_plan = "upgrade n8n plan or use manual UI/template flow"
elif not public_list.get("ok") and public_list.get("status_code") in (401, 403):
    cause = "auth_or_permission_problem"
    next_plan = "rotate/check X-N8N-API-KEY and verify plan/permissions"
elif success:
    cause = "public_create_success"
    next_plan = "patch Jarvis materializer to use the successful candidate only"

summary = {
    "n8n_base_url": n8n_base_url,
    "backend_materializer_ok": report["backend_materializer_health"].get("ok"),
    "public_list_ok": public_list.get("ok"),
    "public_list_status": public_list.get("status_code"),
    "rest_probe_ok": rest_probe.get("ok"),
    "rest_probe_status": rest_probe.get("status_code"),
    "create_succeeded": success is not None,
    "successful_candidate": success["label"] if success else None,
    "workflow_id": success["workflow_id"] if success else None,
    "webhook_probe_ok": webhook_probe.get("ok") if webhook_probe else None,
    "probable_cause": cause,
    "next_plan": next_plan,
    "report_path": str(REPORT_PATH),
}

report["summary"] = summary

REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print("\n=== SUMMARY ===")
print(json.dumps(summary, ensure_ascii=False, indent=2))

print("\n=== REPORT PATH ===")
print(str(REPORT_PATH))