from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import urlparse


RISK_ORDER = {
    "low": 0,
    "guarded": 1,
    "high_risk": 2,
    "forbidden": 3,
}


def _merge_level(current: str, candidate: str) -> str:
    return candidate if RISK_ORDER.get(candidate, 0) > RISK_ORDER.get(current, 0) else current


def _host_is_local(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").strip().lower()
        return host in {"127.0.0.1", "localhost"}
    except Exception:
        return False


def evaluate_risk(
    adapter_name: str | None = None,
    payload: Dict[str, Any] | None = None,
    required_capabilities: List[str] | None = None,
    requested_tools: List[str] | None = None,
) -> Dict[str, Any]:
    payload = dict(payload or {})
    required_capabilities = list(required_capabilities or [])
    requested_tools = list(requested_tools or [])

    risk_level = "low"
    reasons: List[str] = []

    adapter = (adapter_name or "").strip().lower()
    if adapter in {"openai_compatible_http", "claude_code_bridge", "external_cli_agent"}:
        risk_level = _merge_level(risk_level, "guarded")
        reasons.append(f"Adapter '{adapter}' is external or less constrained and requires governance.")

    if adapter in {"claude_code_bridge"}:
        risk_level = _merge_level(risk_level, "high_risk")
        reasons.append("Claude-style CLI bridge can execute broad external agent behavior.")

    command = str(payload.get("command") or "")
    if command.strip():
        risk_level = _merge_level(risk_level, "high_risk")
        reasons.append("Payload contains a shell/CLI command.")

    url = str(payload.get("url") or "")
    if url.strip() and not _host_is_local(url):
        risk_level = _merge_level(risk_level, "guarded")
        reasons.append("Payload targets a non-local URL.")

    lowered_caps = [str(x).strip().lower() for x in required_capabilities]
    if "shell" in lowered_caps:
        risk_level = _merge_level(risk_level, "high_risk")
        reasons.append("Task requires shell capability.")
    if "external_ai" in lowered_caps or "remote_ai" in lowered_caps:
        risk_level = _merge_level(risk_level, "guarded")
        reasons.append("Task requires external AI capability.")

    lowered_tools = [str(x).strip().lower() for x in requested_tools]
    if "shell" in lowered_tools:
        risk_level = _merge_level(risk_level, "high_risk")
        reasons.append("Requested tools include shell.")
    if "http" in lowered_tools:
        risk_level = _merge_level(risk_level, "guarded")
        reasons.append("Requested tools include HTTP.")

    requires_approval = risk_level in {"guarded", "high_risk"}
    is_forbidden = risk_level == "forbidden"

    return {
        "risk_level": risk_level,
        "requires_approval": requires_approval,
        "is_forbidden": is_forbidden,
        "reason": "; ".join(reasons) if reasons else "No significant risk markers detected.",
    }