"""Phase 15: Agent Registry — single source of truth for all agents Jarvis can invoke.

AGENTS dict describes every agent: type, interface, capabilities, cost/speed tiers, health endpoint.
CAPABILITY_REGISTRY (legacy) is generated from AGENTS for backwards compatibility.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------

AGENTS: Dict[str, Dict[str, Any]] = {
    "internet_research": {
        "type": "api_tool",
        "interface": "http",
        "endpoint": "/api/jarvis/tools/internet/research",
        "health_check": "/api/jarvis/tools/internet/health",
        "capabilities": ["web_search", "fact_checking", "news", "research"],
        "input_format": {"query": "str"},
        "output_format": {"answer": "str", "sources": "list"},
        "cost_tier": "medium",
        "cost_per_use": 0.01,
        "speed_tier": "fast",
        "speed_sec": (5, 15),
        "available": True,
        "label": "интернет-исследование",
        "note": "Perplexity sonar-pro → ответ + цитаты",
    },
    "smart_table": {
        "type": "api_tool",
        "interface": "http",
        "endpoint": "/api/jarvis/telegram-tools/internet-table",
        "health_check": None,
        "capabilities": ["data_aggregation", "comparison_tables", "structured_extraction", "excel"],
        "input_format": {"query": "str"},
        "output_format": {"file_path": "str", "drive_url": "str"},
        "cost_tier": "medium",
        "cost_per_use": 0.01,
        "speed_tier": "medium",
        "speed_sec": (15, 40),
        "available": True,
        "label": "таблицы Excel/CSV",
        "note": "Tavily + LLM → тема-зависимые колонки → XLSX в Telegram",
    },
    "claude_coder": {
        "type": "llm",
        "interface": "anthropic_api",
        "model": "claude-sonnet-4-6",
        "health_check": None,
        "capabilities": ["code_generation", "code_review", "architecture_design", "refactoring", "engineering"],
        "input_format": {"prompt": "str", "system": "str"},
        "output_format": {"text": "str"},
        "cost_tier": "high",
        "cost_per_use": 0.02,
        "speed_tier": "medium",
        "speed_sec": (5, 30),
        "available": True,
        "label": "Claude Coder",
        "note": "claude-sonnet-4-6 — код, архитектура, ревью",
    },
    "openai_reasoner": {
        "type": "llm",
        "interface": "openai_api",
        "model": "gpt-4o-mini",
        "health_check": None,
        "capabilities": ["reasoning", "classification", "summarization", "chat"],
        "input_format": {"prompt": "str"},
        "output_format": {"text": "str"},
        "cost_tier": "low",
        "cost_per_use": 0.005,
        "speed_tier": "fast",
        "speed_sec": (2, 10),
        "available": True,
        "label": "OpenAI GPT-4o-mini",
        "note": "быстрый reasoning, классификация, суммаризация",
    },
    "perplexity_researcher": {
        "type": "llm_tool",
        "interface": "perplexity_api",
        "model": "sonar-pro",
        "health_check": None,
        "capabilities": ["web_research_with_sources", "current_events", "fact_checking"],
        "input_format": {"query": "str"},
        "output_format": {"answer": "str", "citations": "list"},
        "cost_tier": "medium",
        "cost_per_use": 0.01,
        "speed_tier": "fast",
        "speed_sec": (5, 15),
        "available": True,
        "label": "Perplexity sonar-pro",
        "note": "веб-исследование с источниками и цитатами",
    },
    "file_processor": {
        "type": "api_tool",
        "interface": "http",
        "endpoint": "/api/jarvis/files/parse",
        "health_check": None,
        "capabilities": ["pdf_parsing", "docx_parsing", "xlsx_parsing", "ocr", "csv_parsing", "file_analysis"],
        "input_format": {"file_path": "str"},
        "output_format": {"text": "str", "tables": "list", "metadata": "dict"},
        "cost_tier": "low",
        "cost_per_use": 0.001,
        "speed_tier": "fast",
        "speed_sec": (1, 10),
        "available": True,
        "label": "файловый процессор",
        "note": "PDF/DOCX/XLSX/CSV → текст, таблицы, метаданные",
    },
    "ai_engineer": {
        "type": "api_tool",
        "interface": "http",
        "endpoint": "/api/jarvis/ai-engineer/review",
        "health_check": None,
        "capabilities": ["architecture_review", "risk_analysis", "integration_planning", "ai_planning"],
        "input_format": {"query": "str"},
        "output_format": {"plan": "str", "risks": "list"},
        "cost_tier": "high",
        "cost_per_use": 0.05,
        "speed_tier": "slow",
        "speed_sec": (30, 90),
        "available": True,
        "label": "AI-инженер",
        "note": "архитектура, риски, безопасный план внедрения (30-60s)",
    },
    "image_generator": {
        "type": "api_tool",
        "interface": "http",
        "endpoint": "/api/jarvis/v5/content-factory/submit",
        "health_check": None,
        "capabilities": ["image_generation", "content_creation"],
        "input_format": {"prompt": "str", "count": "int"},
        "output_format": {"job_id": "str"},
        "cost_tier": "medium",
        "cost_per_use": 0.05,
        "speed_tier": "slow",
        "speed_sec": (30, 120),
        "available": True,
        "label": "генератор изображений",
        "note": "InfluencerStudio → async job → up to 4 images",
    },
    "video_generator": {
        "type": "api_tool",
        "interface": "http",
        "endpoint": "/api/jarvis/v5/content-factory/submit",
        "health_check": None,
        "capabilities": ["video_generation", "content_creation"],
        "input_format": {"prompt": "str", "video_enabled": "bool"},
        "output_format": {"job_id": "str"},
        "cost_tier": "high",
        "cost_per_use": 0.10,
        "speed_tier": "very_slow",
        "speed_sec": (60, 300),
        "available": True,
        "label": "генератор видео",
        "note": "Kling-3 через InfluencerStudio → async job",
    },
    "n8n_workflow": {
        "type": "external_workflow",
        "interface": "webhook",
        "endpoint": "https://daniliyc.app.n8n.cloud/webhook/jarvis",
        "health_check": None,
        "capabilities": ["scheduled_automation", "service_integration", "workflow"],
        "input_format": {"workflow_name": "str", "params": "dict"},
        "output_format": {"result": "any"},
        "cost_tier": "low",
        "cost_per_use": 0.001,
        "speed_tier": "medium",
        "speed_sec": (5, 60),
        "available": True,
        "label": "n8n Cloud автоматизация",
        "note": "webhook → n8n workflow → результат",
    },
    "obsidian_writer": {
        "type": "local_tool",
        "interface": "filesystem",
        "capabilities": ["note_creation", "knowledge_base", "markdown"],
        "input_format": {"title": "str", "content": "str"},
        "output_format": {"path": "str"},
        "cost_tier": "free",
        "cost_per_use": 0.0,
        "speed_tier": "instant",
        "speed_sec": (0, 1),
        "available": True,
        "label": "Obsidian заметки",
        "note": "экспорт в Obsidian Vault (Markdown)",
    },
    "google_drive": {
        "type": "api_tool",
        "interface": "http",
        "capabilities": ["file_upload", "link_sharing", "cloud_storage"],
        "input_format": {"file_path": "str", "folder": "str"},
        "output_format": {"drive_url": "str"},
        "cost_tier": "free",
        "cost_per_use": 0.0,
        "speed_tier": "medium",
        "speed_sec": (3, 15),
        "available": True,
        "label": "Google Drive",
        "note": "загрузка файлов, получение ссылок",
    },
    "cowork_file_agent": {
        "type": "external_app",
        "interface": "filesystem_bridge",
        "watch_folder": "state/cowork_inbox",
        "result_folder": "state/cowork_outbox",
        "capabilities": [
            "file_organization", "expense_reports", "doc_creation",
            "complex_file_workflows", "desktop_automation",
        ],
        "input_format": {"instruction": "str", "context": "dict"},
        "output_format": {"result": "str", "files": "list"},
        "cost_tier": "free",
        "cost_per_use": 0.0,
        "speed_tier": "slow",
        "speed_sec": (30, 300),
        "available": True,  # enabled in Block D1
        "label": "Cowork file agent",
        "note": "Claude Desktop filesystem bridge (watcher required)",
    },
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def get_agent(agent_id: str) -> Optional[Dict[str, Any]]:
    """Return agent config or None."""
    return AGENTS.get(agent_id)


def list_agents_by_capability(cap: str) -> List[str]:
    """Return agent IDs that have the given capability."""
    cap_lower = cap.lower()
    return [
        aid for aid, cfg in AGENTS.items()
        if cfg.get("available") and any(cap_lower in c for c in cfg.get("capabilities", []))
    ]


def list_available_agents() -> List[str]:
    return [aid for aid, cfg in AGENTS.items() if cfg.get("available")]


def estimate_cost(plan: List[str]) -> str:
    """Estimate combined cost tier for a plan (list of agent IDs)."""
    tiers = {"free": 0, "low": 1, "medium": 2, "high": 3}
    max_tier = 0
    for agent_id in plan:
        cfg = AGENTS.get(agent_id, {})
        max_tier = max(max_tier, tiers.get(cfg.get("cost_tier", "low"), 1))
    reverse = {0: "free", 1: "low", 2: "medium", 3: "high"}
    return reverse.get(max_tier, "medium")


def estimate_plan_cost_usd(plan: List[str]) -> float:
    """Return estimated total cost in USD for executing a plan (list of agent IDs)."""
    total = 0.0
    for agent_id in plan:
        cfg = AGENTS.get(agent_id, {})
        total += cfg.get("cost_per_use", 0.0)
    return round(total, 4)


def check_all_agents_health() -> Dict[str, bool]:
    """Return health status for all agents. HTTP agents get a quick ping; others assumed up."""
    import urllib.request
    base_url = os.environ.get("TELEGRAM_BACKEND_URL", "http://127.0.0.1:8010")
    results: Dict[str, bool] = {}
    for agent_id, cfg in AGENTS.items():
        if not cfg.get("available"):
            results[agent_id] = False
            continue
        hc = cfg.get("health_check")
        if hc:
            try:
                url = base_url + hc
                req = urllib.request.urlopen(url, timeout=3)
                results[agent_id] = req.status < 400
            except Exception:
                results[agent_id] = False
        else:
            # No health check — assume available
            results[agent_id] = True
    return results


# ---------------------------------------------------------------------------
# Legacy CAPABILITY_REGISTRY view (backwards compat — Phase 6 consumers)
# ---------------------------------------------------------------------------

def _capabilities_from_agents():
    """Build legacy CAPABILITIES list from AGENTS."""
    status_map = {"free": "AVAILABLE", "low": "AVAILABLE", "medium": "AVAILABLE",
                  "high": "AVAILABLE"}
    caps = []
    for aid, cfg in AGENTS.items():
        if not cfg.get("available"):
            continue
        caps.append({
            "id": aid,
            "category": cfg.get("type", "tool"),
            "status": "AVAILABLE",
            "label": cfg.get("label", aid),
            "note": cfg.get("note", ""),
        })
    return caps


CAPABILITY_REGISTRY = _capabilities_from_agents()
