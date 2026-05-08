"""Phase 24: n8n Deep Integration.

High-level helpers that wrap N8nClient for Telegram bot commands:
  discover_n8n_workflows()  — list available workflows from n8n cloud
  trigger_workflow()        — trigger a workflow by id or name
  get_workflow_status()     — get execution status
  toggle_workflow()         — enable / disable a workflow
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _client():
    """Return an N8nClient or raise RuntimeError with a friendly message."""
    try:
        from app.services.n8n_client import N8nClient, N8nClientError
        return N8nClient()
    except Exception as exc:
        raise RuntimeError(f"n8n не настроен: {exc}") from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def discover_n8n_workflows() -> List[Dict[str, Any]]:
    """Return list of workflows from n8n cloud.

    Each item: {"id", "name", "active", "trigger_url"}.
    Raises RuntimeError on connectivity/auth problems.
    """
    client = _client()
    raw = client.list_workflows()

    workflows = raw if isinstance(raw, list) else raw.get("data", [])
    result = []
    for wf in workflows:
        wf_id = str(wf.get("id", ""))
        name = wf.get("name", wf_id)
        active = bool(wf.get("active", False))

        # Build trigger URL from webhook node if present
        trigger_url = ""
        nodes = wf.get("nodes") or []
        for node in nodes:
            if "Webhook" in node.get("type", ""):
                params = node.get("parameters", {})
                path = params.get("path", "")
                if path:
                    base = os.getenv("N8N_BASE_URL", "").rstrip("/")
                    trigger_url = f"{base}/webhook/{path}"
                break

        result.append({
            "id": wf_id,
            "name": name,
            "active": active,
            "trigger_url": trigger_url,
        })
    return result


def trigger_workflow(
    workflow_id: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Trigger a workflow via its webhook.

    Resolves name → id if workflow_id looks like a name.
    Returns dict with at least {"execution_id"} on success.
    """
    client = _client()
    payload = payload or {}

    # Try name → id resolution
    if not workflow_id.isdigit():
        try:
            workflows = discover_n8n_workflows()
            match = next(
                (w for w in workflows if workflow_id.lower() in w["name"].lower()),
                None,
            )
            if match:
                workflow_id = match["id"]
        except Exception:
            pass

    result = client.trigger_webhook(payload)
    if isinstance(result, dict):
        return result
    return {"execution_id": str(result), "status": "triggered"}


def get_workflow_status(execution_id: str) -> Dict[str, Any]:
    """Return execution status from n8n.

    Returns {"execution_id", "status", "data"}.
    """
    client = _client()
    try:
        # Use internal _request to hit executions endpoint
        raw = client._request(
            "GET",
            f"{client.base_url}/api/v1/executions/{execution_id}",
            headers=client._api_headers(),
        )
    except Exception as exc:
        return {"execution_id": execution_id, "status": "error", "error": str(exc)}

    return {
        "execution_id": execution_id,
        "status": raw.get("status", "unknown"),
        "data": raw.get("data"),
        "finished": raw.get("finished", False),
    }


def toggle_workflow(workflow_id: str, active: bool) -> bool:
    """Enable or disable a workflow. Returns True on success."""
    client = _client()
    try:
        client.activate_workflow(workflow_id, active=active)
        return True
    except Exception as exc:
        logger.warning("toggle_workflow %s failed: %s", workflow_id, exc)
        return False


def workflow_list_text(
    workflows: List[Dict[str, Any]],
    page: int = 0,
    page_size: int = 20,
    filter_str: str = "",
) -> str:
    """Format workflow list as human-readable text for Telegram.

    Supports pagination (page_size per page) and optional name filter.
    Returns text + navigation hint when more pages exist.
    """
    if not workflows:
        return "📭 Нет доступных workflows."

    # Apply filter
    if filter_str:
        filtered = [w for w in workflows if filter_str.lower() in w["name"].lower()]
        if not filtered:
            return f"📭 Нет workflows содержащих «{filter_str}»."
    else:
        filtered = workflows

    total = len(filtered)
    start = page * page_size
    end = start + page_size
    page_items = filtered[start:end]

    header = f"📋 Workflows n8n ({total} всего):"
    if filter_str:
        header = f"📋 Workflows «{filter_str}» ({total} найдено):"

    lines = [header]
    for wf in page_items:
        icon = "✅" if wf.get("active") else "⏸"
        lines.append(f"{icon} [{wf['id']}] {wf['name']}")

    if end < total:
        remaining = total - end
        lines.append(f"\n+{remaining} ещё. Следующая страница: /n8n list {page + 1}")
    elif page > 0:
        lines.append(f"\n(страница {page + 1} из {(total + page_size - 1) // page_size})")

    lines.append("\nЗапустить: /n8n run <id или название>")
    if not filter_str:
        lines.append("Фильтр: /n8n list active  или  /n8n list <слово>")
    return "\n".join(lines)
