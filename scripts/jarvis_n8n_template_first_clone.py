from __future__ import annotations

import copy
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
OUT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "n8n_template_first"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = os.environ["N8N_BASE_URL"].rstrip("/")
WEBHOOK_BASE_URL = os.environ.get("N8N_WEBHOOK_BASE_URL", BASE_URL).rstrip("/")
API_KEY = os.environ["N8N_API_KEY"]
TIMEOUT = int(os.environ.get("N8N_TIMEOUT_SECONDS", "45"))
TEMPLATE_NAME = os.environ.get("JARVIS_TEMPLATE_WORKFLOW_NAME", "jarvis-ui-webhook-template")

STAMP = time.strftime("%Y%m%d_%H%M%S")
RUN_ID = uuid.uuid4().hex[:8]
REPORT_PATH = OUT_DIR / f"n8n_template_first_{STAMP}_{RUN_ID}.json"
RAW_TEMPLATE_PATH = OUT_DIR / f"n8n_template_raw_{STAMP}_{RUN_ID}.json"


def req(
    method: str,
    path_or_url: str,
    *,
    json_body: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
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
        params=params,
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
        "headers": {
            k: v
            for k, v in resp.headers.items()
            if k.lower() in {"content-type", "location", "server", "x-powered-by"}
        },
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


def list_all_workflows(limit: int = 250) -> List[Dict[str, Any]]:
    first = req("GET", f"/api/v1/workflows?limit={limit}", expected=(200,))
    body = first["body"]
    if isinstance(body, dict) and isinstance(body.get("data"), list):
        return body["data"]
    if isinstance(body, list):
        return body
    return []


def find_template_by_name(workflows: List[Dict[str, Any]], template_name: str) -> Optional[Dict[str, Any]]:
    exact = [w for w in workflows if str(w.get("name", "")).strip() == template_name]
    if not exact:
        return None

    exact.sort(key=lambda x: str(x.get("updatedAt") or x.get("createdAt") or ""), reverse=True)
    return exact[0]


def find_webhook_nodes(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [n for n in nodes if str(n.get("type")) == "n8n-nodes-base.webhook"]


def deep_delete_keys(obj: Any, keys_to_remove: set[str]) -> Any:
    if isinstance(obj, dict):
        return {
            k: deep_delete_keys(v, keys_to_remove)
            for k, v in obj.items()
            if k not in keys_to_remove
        }
    if isinstance(obj, list):
        return [deep_delete_keys(v, keys_to_remove) for v in obj]
    return obj


def patch_template_for_clone(
    template_workflow: Dict[str, Any],
    *,
    label: str,
    remove_node_ids: bool,
    remove_webhook_ids: bool,
    remove_top_meta: bool,
) -> Dict[str, Any]:
    clone_name = f"jarvis-template-clone-{label}-{STAMP}-{RUN_ID}"
    clone_path = f"jarvis-template-clone-{label}-{STAMP}-{RUN_ID}"

    nodes = copy.deepcopy(template_workflow["nodes"])
    connections = copy.deepcopy(template_workflow.get("connections", {}))
    settings = copy.deepcopy(template_workflow.get("settings", {}))

    webhook_nodes = find_webhook_nodes(nodes)
    if not webhook_nodes:
        raise RuntimeError("Template has no webhook node")

    # Patch all webhook nodes with a new unique path
    for node in nodes:
        if str(node.get("type")) == "n8n-nodes-base.webhook":
            params = node.setdefault("parameters", {})
            params["path"] = clone_path

    if remove_node_ids:
        nodes = deep_delete_keys(nodes, {"id"})

    if remove_webhook_ids:
        nodes = deep_delete_keys(nodes, {"webhookId"})

    body = {
        "name": clone_name,
        "nodes": nodes,
        "connections": connections,
        "settings": settings if isinstance(settings, dict) else {},
    }

    if remove_top_meta:
        body = deep_delete_keys(body, {"meta", "pinData", "staticData", "versionId", "activeVersionId"})

    return {
        "label": label,
        "clone_name": clone_name,
        "clone_path": clone_path,
        "body": body,
    }


def activate_workflow(workflow_id: str) -> List[Dict[str, Any]]:
    attempts: List[Dict[str, Any]] = []

    # Only keep methods that are actually useful in your workspace history
    for method, path, body, expected in [
        ("POST", f"/api/v1/workflows/{workflow_id}/activate", None, (200, 201, 204)),
    ]:
        item: Dict[str, Any] = {"method": method, "path": path}
        try:
            item["result"] = req(method, path, json_body=body, expected=expected)
            item["ok"] = True
        except Exception as exc:
            item["ok"] = False
            item["error"] = str(exc)
        attempts.append(item)
    return attempts


def read_back(workflow_id: str) -> Dict[str, Any]:
    return req("GET", f"/api/v1/workflows/{workflow_id}", expected=(200,))


def probe_webhook(path: str, retries: int = 4, sleep_seconds: float = 3.0) -> List[Dict[str, Any]]:
    results = []
    url = f"{WEBHOOK_BASE_URL}/webhook/{path}"

    for i in range(retries):
        time.sleep(sleep_seconds if i > 0 else 4.0)
        resp = requests.post(
            url,
            json={
                "source": "jarvis_template_first",
                "ts": time.time(),
                "message": f"probe_attempt_{i+1}",
            },
            timeout=TIMEOUT,
            allow_redirects=False,
        )
        try:
            body = resp.json()
        except Exception:
            body = resp.text

        item = {
            "attempt": i + 1,
            "url": url,
            "status_code": resp.status_code,
            "ok": 200 <= resp.status_code < 300,
            "headers": {
                k: v
                for k, v in resp.headers.items()
                if k.lower() in {"content-type", "location", "server", "x-powered-by"}
            },
            "body": body,
        }
        results.append(item)

        if item["ok"]:
            break

    return results


report: Dict[str, Any] = {
    "base_url": BASE_URL,
    "webhook_base_url": WEBHOOK_BASE_URL,
    "template_name": TEMPLATE_NAME,
}

# 1) Auth works?
auth_check = req("GET", "/api/v1/workflows?limit=1", expected=(200,))
report["auth_check"] = auth_check

# 2) Find template
all_workflows = list_all_workflows(limit=250)
report["workflow_count_seen"] = len(all_workflows)

template_summary = find_template_by_name(all_workflows, TEMPLATE_NAME)
if not template_summary:
    raise RuntimeError(f'Template workflow "{TEMPLATE_NAME}" not found. Create it in UI first and activate it.')

template_id = str(template_summary["id"])
report["template_summary"] = template_summary

# 3) Pull raw template
template_full = req("GET", f"/api/v1/workflows/{template_id}", expected=(200,))
report["template_full"] = template_full

template_body = template_full["body"]
RAW_TEMPLATE_PATH.write_text(json.dumps(template_body, ensure_ascii=False, indent=2), encoding="utf-8")

if not isinstance(template_body, dict):
    raise RuntimeError("Template GET body is not a dict")

if not isinstance(template_body.get("nodes"), list):
    raise RuntimeError("Template has no nodes list")

template_webhook_nodes = find_webhook_nodes(template_body["nodes"])
report["template_webhook_nodes_count"] = len(template_webhook_nodes)
if not template_webhook_nodes:
    raise RuntimeError("Template workflow has no webhook node")

report["template_active"] = bool(template_body.get("active"))

# 4) Build candidates from the REAL template
candidate_specs = [
    {"label": "A_keep_ids_keep_webhookId", "remove_node_ids": False, "remove_webhook_ids": False, "remove_top_meta": True},
    {"label": "B_drop_node_ids_keep_webhookId", "remove_node_ids": True, "remove_webhook_ids": False, "remove_top_meta": True},
    {"label": "C_drop_node_ids_drop_webhookId", "remove_node_ids": True, "remove_webhook_ids": True, "remove_top_meta": True},
]

results = []
winner = None

for spec in candidate_specs:
    candidate = patch_template_for_clone(
        template_body,
        label=spec["label"],
        remove_node_ids=spec["remove_node_ids"],
        remove_webhook_ids=spec["remove_webhook_ids"],
        remove_top_meta=spec["remove_top_meta"],
    )

    row: Dict[str, Any] = {
        "label": candidate["label"],
        "clone_name": candidate["clone_name"],
        "clone_path": candidate["clone_path"],
        "used_body": candidate["body"],
    }

    try:
        created = req("POST", "/api/v1/workflows", json_body=candidate["body"], expected=(200, 201))
        row["create_result"] = created

        workflow_id = extract_workflow_id(created["body"])
        row["workflow_id"] = workflow_id

        if not workflow_id:
            row["error"] = "workflow_id not found after create"
            results.append(row)
            continue

        row["activation_attempts"] = activate_workflow(workflow_id)
        row["read_back"] = read_back(workflow_id)
        row["probe_attempts"] = probe_webhook(candidate["clone_path"])

        if any(bool(p.get("ok")) for p in row["probe_attempts"]):
            winner = row
            results.append(row)
            break

    except Exception as exc:
        row["error"] = str(exc)

    results.append(row)

report["candidate_results"] = results
report["winner"] = winner
report["raw_template_path"] = str(RAW_TEMPLATE_PATH)
REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

print("=== TEMPLATE SUMMARY ===")
print(json.dumps(report["template_summary"], ensure_ascii=False, indent=2))
print()
print("=== TEMPLATE ACTIVE ===")
print(json.dumps({"template_active": report["template_active"], "template_webhook_nodes_count": report["template_webhook_nodes_count"]}, ensure_ascii=False, indent=2))
print()
print("=== CANDIDATE RESULTS ===")
print(json.dumps(results, ensure_ascii=False, indent=2))
print()
print("=== WINNER ===")
print(json.dumps(winner if winner else {"winner": None}, ensure_ascii=False, indent=2))
print()
print("=== REPORT PATH ===")
print(str(REPORT_PATH))
print("=== RAW TEMPLATE PATH ===")
print(str(RAW_TEMPLATE_PATH))