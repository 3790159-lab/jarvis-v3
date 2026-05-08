from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from app.services.identity_core import get_system_prompt


ROOT = Path.cwd()
ART_DIR = ROOT / "jarvis_stage3_artifacts" / "internet_agent"
ART_DIR.mkdir(parents=True, exist_ok=True)


def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int = 45) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}


def _save_report(prefix: str, data: Dict[str, Any]) -> str:
    path = ART_DIR / f"{prefix}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def tavily_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    key = os.getenv("TAVILY_API_KEY", "").strip()
    if not key:
        return {"ok": False, "provider": "tavily", "error": "TAVILY_API_KEY is empty"}

    payload = {
        "api_key": key,
        "query": query,
        "search_depth": "basic",
        "max_results": max_results,
        "include_answer": True,
    }

    try:
        data = _post_json(
            "https://api.tavily.com/search",
            payload,
            {"Content-Type": "application/json"},
            timeout=45,
        )
        return {"ok": True, "provider": "tavily", "data": data}
    except Exception as e:
        return {"ok": False, "provider": "tavily", "error": str(e)}


def perplexity_research(query: str) -> Dict[str, Any]:
    key = os.getenv("PERPLEXITY_API_KEY", "").strip()
    if not key:
        return {"ok": False, "provider": "perplexity", "error": "PERPLEXITY_API_KEY is empty"}

    payload = {
        "model": os.getenv("PERPLEXITY_MODEL", "sonar-pro"),
        "messages": [
            {
                "role": "system",
                "content": get_system_prompt("researcher", lang="en")
            },
            {
                "role": "user",
                "content": query
            }
        ],
    }

    try:
        data = _post_json(
            "https://api.perplexity.ai/chat/completions",
            payload,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            },
            timeout=60,
        )
        return {"ok": True, "provider": "perplexity", "data": data}
    except Exception as e:
        return {"ok": False, "provider": "perplexity", "error": str(e)}


def should_research(prompt: str, quality_target: str = "") -> bool:
    text = (prompt or "").lower()
    if quality_target in {"ultra", "research", "best"}:
        return True

    triggers = [
        "найди",
        "поищи",
        "актуальн",
        "лучший сервис",
        "какой сервис",
        "pipeline",
        "пайплайн",
        "api",
        "model",
        "модель",
        "provider",
        "alternative",
        "альтернатива",
    ]

    return any(t in text for t in triggers)


def enhance_generation_prompt(prompt: str, research: Dict[str, Any] | None = None) -> str:
    base = (prompt or "").strip()

    additions = [
        "high-end editorial photography",
        "professional lighting",
        "clean composition",
        "realistic skin texture",
        "natural anatomy",
        "premium visual quality",
    ]

    return base + ", " + ", ".join(additions)


def research_and_decide(prompt: str, quality_target: str = "high") -> Dict[str, Any]:
    query = (
        "Find current best AI image generation services, APIs, prompt strategies, "
        "workflow examples, and production pipeline advice for: "
        + prompt
    )

    tavily = tavily_search(query)
    perplexity = perplexity_research(query)

    providers_ok = [x["provider"] for x in [tavily, perplexity] if x.get("ok")]

    decision = {
        "research_used": bool(providers_ok),
        "providers_ok": providers_ok,
        "recommended_provider": "current_service",
        "should_try_alternative_provider": False,
        "reason": "Default current pipeline remains active.",
    }

    if providers_ok:
        decision["should_try_alternative_provider"] = True
        decision["reason"] = "Internet research available; Jarvis may compare current provider against alternatives."

    enhanced_prompt = enhance_generation_prompt(prompt, {"tavily": tavily, "perplexity": perplexity})

    report = {
        "ok": True,
        "prompt": prompt,
        "quality_target": quality_target,
        "enhanced_prompt": enhanced_prompt,
        "decision": decision,
        "research": {
            "tavily": tavily,
            "perplexity": perplexity,
        },
    }

    report["report_path"] = _save_report("internet_research", report)
    return report