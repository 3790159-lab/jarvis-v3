"""Trend Analyzer — Block H5.5.

Uses Perplexity to search for food trends, hashtags, and event ideas.
Saves insights to Obsidian and formats for morning brief.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent.parent
_INSIGHTS_DIR = _ROOT / "state" / "trend_insights"
_INSIGHTS_DIR.mkdir(parents=True, exist_ok=True)


def _call_perplexity(query: str) -> str:
    """Query Perplexity for trend research."""
    try:
        import sys
        root = str(_ROOT)
        if root not in sys.path:
            sys.path.insert(0, root)
        from app.services.smart_router import call_perplexity
        return call_perplexity(query)
    except Exception as exc:
        logger.warning("[trend_analyzer] perplexity error: %s", exc)
        return ""


def _parse_trends_from_text(text: str) -> Dict[str, List[str]]:
    """Extract structured trends from Perplexity text response."""
    food_trends: List[str] = []
    hashtags: List[str] = []
    ideas: List[str] = []

    lines = text.splitlines()
    section = "food"
    for line in lines:
        line_lower = line.lower().strip()
        if not line_lower:
            continue
        if "hashtag" in line_lower or "#" in line:
            section = "hashtag"
        elif "idea" in line_lower or "идея" in line_lower or "специальн" in line_lower:
            section = "ideas"
        elif any(w in line_lower for w in ["тренд", "trend", "popular", "популяр"]):
            section = "food"

        # Extract items that look like list entries
        if line.strip().startswith(("•", "-", "*", "1", "2", "3", "4", "5")):
            content = line.strip().lstrip("•-*0123456789. ").strip()
            if content:
                if section == "hashtag" or "#" in content:
                    hashtags.append(content)
                elif section == "ideas":
                    ideas.append(content)
                else:
                    food_trends.append(content)

    return {
        "food_trends": food_trends[:10],
        "hashtags": hashtags[:15],
        "ideas": ideas[:5],
    }


def analyze_industry_trends() -> Dict[str, Any]:
    """Fetch and analyze restaurant/food industry trends."""
    query = (
        "trending food posts Instagram 2026 restaurant "
        "popular hashtags for restaurant food photography "
        "special event ideas for restaurants this week"
    )

    raw_response = _call_perplexity(query)
    parsed = _parse_trends_from_text(raw_response)

    # Ensure minimum structure even if parsing fails
    if not parsed["food_trends"]:
        parsed["food_trends"] = ["seasonal ingredients", "comfort food", "fusion cuisine"]
    if not parsed["hashtags"]:
        parsed["hashtags"] = ["#foodphotography", "#instafood", "#restaurant", "#foodie"]
    if not parsed["ideas"]:
        parsed["ideas"] = ["Тематический вечер", "Мастер-класс по кулинарии"]

    insights = {
        **parsed,
        "found_at": datetime.now().isoformat(),
        "date": date.today().isoformat(),
        "raw_length": len(raw_response),
    }

    # Save insights
    insights_path = _INSIGHTS_DIR / f"{insights['date']}.json"
    insights_path.write_text(json.dumps(insights, ensure_ascii=False, indent=2), encoding="utf-8")

    return insights


def save_insights_to_obsidian(insights: Dict[str, Any]) -> str:
    """Save insights as Markdown to Obsidian via backend."""
    today = insights.get("date", date.today().isoformat())
    content = _format_insights_as_markdown(insights)

    try:
        import urllib.request
        backend = os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
        payload = json.dumps({
            "content": content,
            "title": f"Trends — {today}",
            "path": f"insights/{today}.md",
        }).encode("utf-8")
        req = urllib.request.Request(
            backend + "/api/jarvis/tools/obsidian/save",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
        return result.get("path") or f"insights/{today}.md"
    except Exception as exc:
        logger.warning("[trend_analyzer] obsidian error: %s", exc)
        local_path = _INSIGHTS_DIR / f"{today}.md"
        local_path.write_text(content, encoding="utf-8")
        return str(local_path)


def _format_insights_as_markdown(insights: Dict[str, Any]) -> str:
    today = insights.get("date", "?")
    lines = [
        f"# Trend Insights — {today}",
        "",
        f"**Analyzed:** {insights.get('found_at', '?')[:19]}",
        "",
        "## Food Trends",
        "",
    ]
    for t in insights.get("food_trends", []):
        lines.append(f"- {t}")
    lines.extend(["", "## Top Hashtags", ""])
    for h in insights.get("hashtags", []):
        lines.append(f"- {h}")
    lines.extend(["", "## Event Ideas", ""])
    for i in insights.get("ideas", []):
        lines.append(f"- {i}")
    return "\n".join(lines)


def format_for_morning_brief(insights: Dict[str, Any]) -> str:
    """Format insights as a short Telegram message section."""
    food = insights.get("food_trends", [])[:3]
    tags = insights.get("hashtags", [])[:5]
    ideas = insights.get("ideas", [])[:2]

    lines = ["📈 *Тренды сегодня:*"]
    if food:
        lines.append("🍽 " + " | ".join(food))
    if tags:
        lines.append("🏷 " + " ".join(tags))
    if ideas:
        for idea in ideas:
            lines.append(f"💡 {idea}")
    return "\n".join(lines)


def get_latest_insights() -> Optional[Dict[str, Any]]:
    """Load most recent insights file."""
    files = sorted(_INSIGHTS_DIR.glob("*.json"), reverse=True)
    for f in files:
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None
