from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services.identity_core import get_system_prompt


ROOT = Path.cwd()
ART_DIR = ROOT / "jarvis_stage3_artifacts" / "internet_tools"
ART_DIR.mkdir(parents=True, exist_ok=True)


def _utc() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _save_artifact(prefix: str, data: Dict[str, Any]) -> str:
    path = ART_DIR / f"{prefix}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int = 60) -> Dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        return {
            "ok": False,
            "http_error": e.code,
            "error": detail,
            "url": url,
        }
    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
            "url": url,
        }


def internet_health() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "jarvis_internet_tools",
        "tools": [
            "internet.search",
            "internet.research",
            "internet.find_pipelines",
            "internet.compare_services",
            "internet.engineer_brief",
        ],
        "tavily_configured": bool(os.getenv("TAVILY_API_KEY", "").strip()),
        "perplexity_configured": bool(os.getenv("PERPLEXITY_API_KEY", "").strip()),
        "perplexity_model": os.getenv("PERPLEXITY_MODEL", "sonar-pro"),
        "artifact_dir": str(ART_DIR),
    }


def internet_search(query: str, max_results: int = 5, search_depth: str = "basic") -> Dict[str, Any]:
    key = os.getenv("TAVILY_API_KEY", "").strip()

    if not key:
        result = {
            "ok": False,
            "tool": "internet.search",
            "provider": "tavily",
            "error": "TAVILY_API_KEY is empty",
            "query": query,
            "created_at": _utc(),
        }
        result["artifact_path"] = _save_artifact("search_missing_key", result)
        return result

    payload = {
        "api_key": key,
        "query": query,
        "search_depth": search_depth,
        "max_results": max_results,
        "include_answer": True,
        "include_raw_content": False,
    }

    data = _post_json(
        "https://api.tavily.com/search",
        payload,
        {"Content-Type": "application/json"},
        timeout=60,
    )

    result = {
        "ok": bool(data) and not data.get("http_error") and not data.get("error"),
        "tool": "internet.search",
        "provider": "tavily",
        "query": query,
        "created_at": _utc(),
        "data": data,
    }
    result["artifact_path"] = _save_artifact("search", result)
    return result


def internet_research(query: str, system_prompt: Optional[str] = None) -> Dict[str, Any]:
    key = os.getenv("PERPLEXITY_API_KEY", "").strip()

    if not key:
        result = {
            "ok": False,
            "tool": "internet.research",
            "provider": "perplexity",
            "error": "PERPLEXITY_API_KEY is empty",
            "query": query,
            "created_at": _utc(),
        }
        result["artifact_path"] = _save_artifact("research_missing_key", result)
        return result

    if not system_prompt:
        system_prompt = get_system_prompt("researcher")

    payload = {
        "model": os.getenv("PERPLEXITY_MODEL", "sonar-pro"),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ],
    }

    data = _post_json(
        "https://api.perplexity.ai/chat/completions",
        payload,
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        timeout=90,
    )

    text = ""
    try:
        text = data["choices"][0]["message"]["content"]
    except Exception:
        text = ""

    result = {
        "ok": bool(text),
        "tool": "internet.research",
        "provider": "perplexity",
        "query": query,
        "created_at": _utc(),
        "answer": text,
        "raw": data,
    }
    result["artifact_path"] = _save_artifact("research", result)
    return result


def find_pipelines(topic: str, max_results: int = 6) -> Dict[str, Any]:
    search_query = (
        "production workflow pipeline examples APIs integrations best practices "
        f"{topic}"
    )

    search = internet_search(search_query, max_results=max_results, search_depth="basic")

    research_query = (
        "Find practical production-ready pipeline examples, APIs, tools, architecture options, "
        "tradeoffs, and integration steps for: "
        f"{topic}"
    )

    research = internet_research(research_query)

    result = {
        "ok": bool(search.get("ok") or research.get("ok")),
        "tool": "internet.find_pipelines",
        "topic": topic,
        "created_at": _utc(),
        "search": search,
        "research": research,
        "recommendation": _build_pipeline_recommendation(topic, search, research),
    }
    result["artifact_path"] = _save_artifact("find_pipelines", result)
    return result


def compare_services(goal: str, services: Optional[List[str]] = None) -> Dict[str, Any]:
    services = services or []

    services_text = ", ".join(services) if services else "relevant current market options"

    query = (
        f"Compare {services_text} for this goal: {goal}. "
        "Give scores from 1-10 for quality, reliability, API integration, cost, automation, and scalability. "
        "Return a clear recommendation and fallback plan."
    )

    research = internet_research(query)

    result = {
        "ok": research.get("ok", False),
        "tool": "internet.compare_services",
        "goal": goal,
        "services": services,
        "created_at": _utc(),
        "research": research,
    }
    result["artifact_path"] = _save_artifact("compare_services", result)
    return result


def engineer_brief(task: str) -> Dict[str, Any]:
    query = (
        "Act as an AI systems engineer. Research current tools and propose an implementation plan. "
        "Include architecture, APIs, failure modes, security concerns, testing, and rollout steps. "
        f"Task: {task}"
    )

    search = internet_search(task, max_results=5, search_depth="basic")
    research = internet_research(query)

    result = {
        "ok": bool(search.get("ok") or research.get("ok")),
        "tool": "internet.engineer_brief",
        "task": task,
        "created_at": _utc(),
        "search": search,
        "research": research,
        "next_actions": [
            "Review recommended APIs/tools",
            "Choose provider",
            "Create small integration adapter",
            "Run smoke test",
            "Add fallback and monitoring",
        ],
    }
    result["artifact_path"] = _save_artifact("engineer_brief", result)
    return result


def _build_pipeline_recommendation(topic: str, search: Dict[str, Any], research: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "default_plan": [
            "Keep n8n as orchestrator/trigger layer",
            "Keep Jarvis as reasoning/tool-control layer",
            "Use async jobs for long-running tasks",
            "Store artifacts and reports",
            "Add provider fallback and quality evaluation",
        ],
        "topic": topic,
        "search_ok": search.get("ok", False),
        "research_ok": research.get("ok", False),
    }


def run_internet_tool(tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    if tool_name == "internet.search":
        return internet_search(
            query=args.get("query", ""),
            max_results=int(args.get("max_results", 5)),
            search_depth=args.get("search_depth", "basic"),
        )

    if tool_name == "internet.research":
        return internet_research(
            query=args.get("query", ""),
            system_prompt=args.get("system_prompt"),
        )

    if tool_name == "internet.find_pipelines":
        return find_pipelines(
            topic=args.get("topic") or args.get("query") or "",
            max_results=int(args.get("max_results", 6)),
        )

    if tool_name == "internet.compare_services":
        return compare_services(
            goal=args.get("goal") or args.get("query") or "",
            services=args.get("services") or [],
        )

    if tool_name == "internet.engineer_brief":
        return engineer_brief(
            task=args.get("task") or args.get("query") or "",
        )

    return {
        "ok": False,
        "error": f"Unknown internet tool: {tool_name}",
        "available_tools": internet_health()["tools"],
    }