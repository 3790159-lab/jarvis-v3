from __future__ import annotations

import copy
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


PROJECT_ROOT = Path(r"C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram")
OUT_DIR = PROJECT_ROOT / "jarvis_stage3_artifacts" / "n8n_template_first"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BASE_URL = os.environ["N8N_BASE_URL"].rstrip("/")
WEBHOOK_BASE_URL = os.environ.get("N8N_WEBHOOK_BASE_URL", BASE_URL).rstrip("/")
API_KEY = os.environ["N8N_API_KEY"]
TIMEOUT = int(os.environ.get("N8N_TIMEOUT_SECONDS", "45"))
PREFERRED_TEMPLATE_NAME = os.environ.get("JARVIS_TEMPLATE_WORKFLOW_NAME", "jarvis-ui-webhook-template")

STAMP = time.strftime("%Y%m%d_%H%M%S")
RUN_ID = uuid.uuid4().hex[:8]
REPORT_PATH = OUT_DIR / f"n8n_smart_template_first_{STAMP}_{RUN_ID}.json"
RAW_TEMPLATE_PATH = OUT_DIR / f"n8n_smart_template_raw_{STAMP}_{RUN_ID}.json"


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


def list_all_workflows(limit: int = 100, max_pages: int = 20) -> List[Dict[str, Any]]:
    workflows: List[Dict[str, Any]] = []
    cursor: Optional[str] = None

    for _ in range(max_pages):
        params: Dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor

        resp = req("GET", "/api/v1/workflows", params=params, expected=(200,))
        body = resp["body"]

        if isinstance(body, dict) and isinstance(body.get("data"), list):
            workflows.extend(body["data"])
            cursor = body.get("nextCursor")
        elif isinstance(body, list):
            workflows.extend(body)
            cursor = None
        else:
            cursor = None

        if not cursor:
            break

    return workflows


def find_webhook_nodes(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [n for n in nodes if str(n.get("type")) == "n8n-nodes-base.webhook"]


def load_full_workflow(workflow_id: str) -> Dict[str, Any]:
    return req("GET", f"/api/v1/workflows/{workflow_id}", expected=(200,))["body"]


def template_score(summary: Dict[str, Any], full: Dict[str, Any], preferred_name: str) -> Tuple[int, str]:
    score = 0
    reason: List[str] = []

    name = str(summary.get("name") or "")
    active = bool(full.get("active"))
    webhook_nodes = find_webhook_nodes(full.get("nodes", []))

    if name == preferred_name:
        score += 1000
        reason.append("exact_name_match")
    elif preferred_name.lower() in name.lower():
        score += 400
        reason.append("name_contains_match")

    if active:
        score += 200
        reason.append("active")

    if webhook_nodes:
        score += 300
        reason.append(f"webhook_nodes:{len(webhook_nodes)}")

    if str(summary.get("updatedAt") or ""):
        score += 1

    return score, ",".join(reason)


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
    prune_settings: bool,
) -> Dict[str, Any]:
    clone_name = f"jarvis-template-clone-{label}-{STAMP}-{RUN_ID}"
    clone_path = f"jarvis-template-clone-{label}-{STAMP}-{RUN_ID}"

    nodes = copy.deepcopy(template_workflow["nodes"])
    connections = copy.deepcopy(template_workflow.get("connections", {}))
    settings = copy.deepcopy(template_workflow.get("settings", {}))

    webhook_nodes = find_webhook_nodes(nodes)
    if not webhook_nodes:
        raise RuntimeError("Selected template has no webhook node")

    for node in nodes:
        if str(node.get("type")) == "n8n-nodes-base.webhook":
            params = node.setdefault("parameters", {})
            params["path"] = clone_path

    if remove_node_ids:
        nodes = deep_delete_keys(nodes, {"id"})

    if remove_webhook_ids:
        nodes = deep_delete_keys(nodes, {"webhookId"})

    allowed_settings = {}
    if isinstance(settings, dict) and not prune_settings:
        allowed_settings = settings
    elif isinstance(settings, dict) and prune_settings:
        for k, v in settings.items():
            if k in {"executionOrder", "callerPolicy", "timezone", "saveManualExecutions", "saveExecutionProgress", "saveDataErrorExecution", "saveDataSuccessExecution"}:
                allowed_settings[k] = v

    body = {
        "name": clone_name,
        "nodes": nodes,
        "connections": connections,
        "settings": allowed_settings,
    }

    body = deep_delete_keys(body, {"meta", "pinData", "staticData", "versionId", "activeVersionId", "shared", "tags", "triggerCount", "updatedAt", "createdAt", "isArchived", "activeVersion", "versionCounter"})

    return {
        "label": label,
        "clone_name": clone_name,
        "clone_path": clone_path,
        "body": body,
    }


def activate_workflow(workflow_id: str) -> List[Dict[str, Any]]:
    attempts: List[Dict[str, Any]] = []
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


def probe_webhook(path: str, retries: int = 5, sleep_seconds: float = 3.0) -> List[Dict[str, Any]]:
    results = []
    url = f"{WEBHOOK_BASE_URL}/webhook/{path}"

    for i in range(retries):
        time.sleep(sleep_seconds if i > 0 else 5.0)
        resp = requests.post(
            url,
            json={
                "source": "jarvis_smart_template_first",
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
    "preferred_template_name": PREFERRED_TEMPLATE_NAME,
}

report["auth_check"] = req("GET", "/api/v1/workflows?limit=1", expected=(200,))

all_workflows = list_all_workflows(limit=100, max_pages=20)
report["workflow_count_seen"] = len(all_workflows)

webhook_candidates: List[Dict[str, Any]] = []

for summary in all_workflows:
    wid = str(summary.get("id") or "")
    if not wid:
        continue
    try:
        full = load_full_workflow(wid)
        webhook_nodes = find_webhook_nodes(full.get("nodes", []))
        if not webhook_nodes:
            continue

        score, reason = template_score(summary, full, PREFERRED_TEMPLATE_NAME)
        webhook_candidates.append({
            "id": wid,
            "name": summary.get("name"),
            "active": bool(full.get("active")),
            "updatedAt": summary.get("updatedAt"),
            "createdAt": summary.get("createdAt"),
            "webhook_nodes_count": len(webhook_nodes),
            "score": score,
            "score_reason": reason,
            "full": full,
        })
    except Exception as exc:
        continue

webhook_candidates.sort(
    key=lambda x: (
        x["score"],
        str(x.get("updatedAt") or ""),
        str(x.get("createdAt") or "")
    ),
    reverse=True,
)

report["discovered_webhook_workflows"] = [
    {
        "id": c["id"],
        "name": c["name"],
        "active": c["active"],
        "updatedAt": c["updatedAt"],
        "createdAt": c["createdAt"],
        "webhook_nodes_count": c["webhook_nodes_count"],
        "score": c["score"],
        "score_reason": c["score_reason"],
    }
    for c in webhook_candidates
]

if not webhook_candidates:
    report["winner"] = None
    report["next_action"] = "No UI-created workflow with a Webhook node exists in this workspace. Create one in n8n UI, activate it, then rerun this script."
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== DISCOVERED WEBHOOK WORKFLOWS ===")
    print(json.dumps([], ensure_ascii=False, indent=2))
    print()
    print("=== NEXT ACTION ===")
    print(json.dumps({"next_action": report["next_action"]}, ensure_ascii=False, indent=2))
    print()
    print("=== REPORT PATH ===")
    print(str(REPORT_PATH))
    raise SystemExit(4)

selected = webhook_candidates[0]
template_body = selected["full"]
RAW_TEMPLATE_PATH.write_text(json.dumps(template_body, ensure_ascii=False, indent=2), encoding="utf-8")

report["selected_template"] = {
    "id": selected["id"],
    "name": selected["name"],
    "active": selected["active"],
    "score": selected["score"],
    "score_reason": selected["score_reason"],
}

candidate_specs = [
    {"label": "A_keep_ids_keep_webhookId_keep_settings", "remove_node_ids": False, "remove_webhook_ids": False, "prune_settings": False},
    {"label": "B_drop_node_ids_keep_webhookId_keep_settings", "remove_node_ids": True, "remove_webhook_ids": False, "prune_settings": False},
    {"label": "C_drop_node_ids_drop_webhookId_keep_settings", "remove_node_ids": True, "remove_webhook_ids": True, "prune_settings": False},
    {"label": "D_drop_node_ids_drop_webhookId_prune_settings", "remove_node_ids": True, "remove_webhook_ids": True, "prune_settings": True},
]

results = []
winner = None

for spec in candidate_specs:
    candidate = patch_template_for_clone(
        template_body,
        label=spec["label"],
        remove_node_ids=spec["remove_node_ids"],
        remove_webhook_ids=spec["remove_webhook_ids"],
        prune_settings=spec["prune_settings"],
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

print("=== DISCOVERED WEBHOOK WORKFLOWS ===")
print(json.dumps(report["discovered_webhook_workflows"], ensure_ascii=False, indent=2))
print()
print("=== SELECTED TEMPLATE ===")
print(json.dumps(report["selected_template"], ensure_ascii=False, indent=2))
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