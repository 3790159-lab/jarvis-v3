"""Minimal Claude helper for simple one-shot text generation."""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def call_claude(prompt: str, max_tokens: int = 512) -> Optional[str]:
    """Call Claude API for a simple text completion. Returns text or None on error."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — skipping Claude call")
        return None
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text if message.content else None
    except Exception as exc:
        logger.error("Claude call failed: %s", exc)
        return None
