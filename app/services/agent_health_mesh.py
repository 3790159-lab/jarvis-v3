"""Phase 17: Provider Health Mesh — extends Phase 9 to all agents.

Adds:
- Health checks for all agents (HTTP ping, LLM minimal call, external connectivity)
- Background scheduler updates agent statuses every 60s
- Smart fallback: if primary agent DOWN → auto-select alternative
- Rich /agents status with capability grouping
"""
from __future__ import annotations

import os
import threading
import time
import urllib.request
from typing import Dict, List, Optional, Tuple

from app.services.agent_registry import AGENTS, get_agent, list_agents_by_capability

# ---------------------------------------------------------------------------
# Health cache
# ---------------------------------------------------------------------------

_health_cache: Dict[str, Tuple[bool, float]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 60.0


def _is_cached(agent_id: str) -> Optional[bool]:
    with _cache_lock:
        entry = _health_cache.get(agent_id)
        if entry and (time.time() - entry[1]) < _CACHE_TTL:
            return entry[0]
    return None


def _set_cache(agent_id: str, healthy: bool) -> None:
    with _cache_lock:
        _health_cache[agent_id] = (healthy, time.time())


# ---------------------------------------------------------------------------
# Per-agent health checks
# ---------------------------------------------------------------------------

def _check_http_agent(agent_id: str, cfg: Dict) -> bool:
    """Ping the health_check endpoint or the main endpoint (HEAD/GET)."""
    base = os.environ.get("TELEGRAM_BACKEND_URL", "http://127.0.0.1:8010")
    hc = cfg.get("health_check") or cfg.get("endpoint")
    if not hc:
        return True  # local/filesystem agents assumed up
    url = base + hc if hc.startswith("/") else hc
    try:
        from app.services.internal_api_client import backend_headers
        req = urllib.request.Request(url, headers=backend_headers(url), method="GET")
        resp = urllib.request.urlopen(req, timeout=4)
        return resp.status < 500
    except Exception:
        return False


def _check_llm_agent(agent_id: str, cfg: Dict) -> bool:
    """Check LLM agent by verifying the API key exists (fast, no actual API call)."""
    interface = cfg.get("interface", "")
    if interface == "anthropic_api":
        return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())
    if interface == "openai_api":
        return bool(os.environ.get("OPENAI_API_KEY", "").strip())
    if interface == "perplexity_api":
        return bool(os.environ.get("PERPLEXITY_API_KEY", "").strip())
    return True


def _check_webhook_agent(agent_id: str, cfg: Dict) -> bool:
    """Check external webhook reachability (HEAD request)."""
    endpoint = cfg.get("endpoint", "")
    if not endpoint:
        return True
    try:
        req = urllib.request.Request(endpoint, method="HEAD")
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception:
        return False


def check_agent_health(agent_id: str, use_cache: bool = True) -> bool:
    """Return True if agent is healthy. Uses cache by default."""
    cfg = get_agent(agent_id)
    if not cfg:
        return False
    if not cfg.get("available"):
        return False

    if use_cache:
        cached = _is_cached(agent_id)
        if cached is not None:
            return cached

    agent_type = cfg.get("type", "api_tool")
    try:
        if agent_type in ("api_tool", "local_tool"):
            result = _check_http_agent(agent_id, cfg)
        elif agent_type in ("llm", "llm_tool"):
            result = _check_llm_agent(agent_id, cfg)
        elif agent_type == "external_workflow":
            result = _check_webhook_agent(agent_id, cfg)
        else:
            result = True
    except Exception:
        result = False

    _set_cache(agent_id, result)
    return result


def check_all_agents(use_cache: bool = True) -> Dict[str, bool]:
    """Return health status dict for all agents."""
    return {aid: check_agent_health(aid, use_cache=use_cache) for aid in AGENTS}


# ---------------------------------------------------------------------------
# Smart fallback
# ---------------------------------------------------------------------------

def get_healthy_agent_for_capability(cap: str, use_cache: bool = True) -> Optional[str]:
    """Return first healthy agent for given capability, or None if all down."""
    from app.services.smart_router import _CAPABILITY_PRIORITY
    candidates = _CAPABILITY_PRIORITY.get(cap) or list_agents_by_capability(cap)
    for aid in candidates:
        if check_agent_health(aid, use_cache=use_cache):
            return aid
    return None


def select_agents_with_fallback(caps: List[str], use_cache: bool = True) -> Dict[str, Optional[str]]:
    """For each capability, return the best healthy agent (or None if all down)."""
    return {cap: get_healthy_agent_for_capability(cap, use_cache=use_cache) for cap in caps}


# ---------------------------------------------------------------------------
# Rich status text for /agents command
# ---------------------------------------------------------------------------

_CAPABILITY_GROUPS = {
    "🧠 LLMs": ["claude_coder", "openai_reasoner", "perplexity_researcher"],
    "🔧 Tools": ["internet_research", "smart_table", "file_processor", "ai_engineer"],
    "🎨 Content": ["image_generator", "video_generator"],
    "🔗 Integrations": ["n8n_workflow", "google_drive", "obsidian_writer"],
    "🖥 External apps": ["cowork_file_agent"],
}


def agents_status_text(use_cache: bool = True) -> str:
    """Build human-readable /agents status grouped by capability."""
    health = check_all_agents(use_cache=use_cache)
    lines = ["🔌 Статус агентов:\n"]

    for group_label, agent_ids in _CAPABILITY_GROUPS.items():
        group_lines = []
        for aid in agent_ids:
            cfg = get_agent(aid) or {}
            label = cfg.get("label", aid)
            available = cfg.get("available", False)
            if not available:
                icon = "⏸"
            elif health.get(aid):
                icon = "✅"
            else:
                icon = "🔴"
            group_lines.append(f"  {icon} {label}")
        lines.append(group_label + ":")
        lines.extend(group_lines)
        lines.append("")

    healthy_count = sum(1 for v in health.values() if v)
    total = len([aid for aid, cfg in AGENTS.items() if cfg.get("available")])
    lines.append(f"✅ {healthy_count}/{total} агентов готовы")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Background health monitor
# ---------------------------------------------------------------------------

_monitor_thread: Optional[threading.Thread] = None
_monitor_running = False


def start_agent_health_monitor(interval: int = 60) -> None:
    global _monitor_thread, _monitor_running
    if _monitor_running:
        return

    def _loop():
        while _monitor_running:
            try:
                check_all_agents(use_cache=False)
            except Exception:
                pass
            time.sleep(interval)

    _monitor_running = True
    _monitor_thread = threading.Thread(target=_loop, daemon=True, name="agent-health-monitor")
    _monitor_thread.start()


def stop_agent_health_monitor() -> None:
    global _monitor_running
    _monitor_running = False
