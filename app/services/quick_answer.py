"""Phase 23: Smart Question Routing — quick factual answers via Claude Haiku.

Simple factual questions ("Сколько планет?", "Столица Франции?") are answered
directly using Claude Haiku (fast + cheap) instead of routing through Perplexity
research, which returns verbose multi-paragraph responses.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Markers that indicate a research/analytical query, not a simple factual question
_RESEARCH_MARKERS = [
    "сравни", "сравнение", "найди топ", "анализ", "аналитика",
    "compare", "research", "исследуй", "изучи",
    "плюсы и минусы", "pros and cons",
    "расскажи про", "обзор", "отзывы",
    "найди", "поищи", "из интернета",
]

# Phrases that must route to identity, not simple_question
_IDENTITY_BLOCKERS = [
    "кто ты", "что ты", "ты кто", "ты что", "ты такой",
    "who are you", "what are you", "jarvis", "джарвис",
]

# Patterns that indicate a simple factual question
_SIMPLE_TRIGGERS = [
    "сколько", "какой", "какая", "какие", "когда",
    "где ", "кто ", "что такое", "кто такой",
    "столица", "год ", "дата ", "вес ", "размер",
    "what is", "who is", "where is", "when was",
    "how many", "how much",
    "в каком", "в какой", "за сколько",
]


def is_simple_question(query: str) -> bool:
    """Return True if query is a simple factual question suitable for direct Claude answer."""
    q = query.lower().strip()

    if len(q) > 120:
        return False

    # Identity questions must not be hijacked by simple_question routing
    if any(m in q for m in _IDENTITY_BLOCKERS):
        return False

    if any(m in q for m in _RESEARCH_MARKERS):
        return False

    return any(t in q for t in _SIMPLE_TRIGGERS)


def quick_answer(query: str) -> Optional[str]:
    """Get a 1-3 sentence factual answer via Claude Haiku.

    Returns None on API failure so the caller can fall back to research.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set; falling back to research")
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        system_prompt = (
            "You are Jarvis V3 — Daniil Lapin's personal AI assistant.\n\n"
            "You CAN:\n"
            "- Generate images (InfluencerStudio, 9:16, batch up to 4)\n"
            "- Generate videos (Kling-3 via InfluencerStudio)\n"
            "- Create Excel/CSV tables from research\n"
            "- Parse PDF/DOCX/XLSX files\n"
            "- Run web research (Perplexity)\n"
            "- Trigger n8n workflows\n"
            "- Save notes to Obsidian\n"
            "- Upload to Google Drive\n\n"
            "ANTI-HALLUCINATION RULES (CRITICAL):\n"
            "1. NEVER invent metrics — no fake CPU%, RAM, connections unless you measured them.\n"
            "   If no real data: say 'не могу измерить без инструментов'.\n"
            "2. NEVER mention services that don't exist in this setup.\n"
            "   This system does NOT have: PostgreSQL, Docker, Redis (unless explicitly configured).\n"
            "3. NEVER pretend you executed an action if you did not.\n"
            "   Say 'команда подготовлена, но требует реального выполнения' — NOT 'Выполнено'.\n"
            "4. NEVER generate fake status reports. If no data: say 'нет данных'.\n\n"
            "Answer in 1-3 sentences. Be factual and concise. "
            "Respond in the same language as the question. "
            "If asked about your capabilities — answer truthfully based on the list above."
        )
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": query}],
        )
        text = response.content[0].text.strip()
        return text
    except Exception as exc:
        logger.warning("quick_answer failed: %s", exc)
        return None
