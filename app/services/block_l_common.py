# -*- coding: utf-8 -*-
"""Block L shared infrastructure: filesystem helpers, Claude API wrapper, Telegram sender."""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State directories
# ---------------------------------------------------------------------------

_BASE = Path(__file__).resolve().parent.parent.parent  # project root

STATE_DIRS = [
    _BASE / "state" / "figma_queue",
    _BASE / "state" / "bolt_queue",
    _BASE / "state" / "landing_briefs",
    _BASE / "state" / "smart_prompts_cache",
    _BASE / "docs" / "block_l",
]


def ensure_state_dirs() -> None:
    """Create all Block L state directories if they don't exist."""
    for d in STATE_DIRS:
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Timestamp / ID helpers
# ---------------------------------------------------------------------------


def get_timestamp_id() -> str:
    """Return YYYYMMDD_HHMMSS string suitable for filenames."""
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def get_content_hash(text: str) -> str:
    """Return 12-char hex hash of text for cache keys."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------------------
# JSON persistence helpers
# ---------------------------------------------------------------------------


def save_json_safe(path: Path | str, data: Dict) -> None:
    """Atomic JSON write: write to .tmp then rename."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def load_json_safe(path: Path | str, default: Any = None) -> Any:
    """Safe JSON read; returns default on any error."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Queue helpers
# ---------------------------------------------------------------------------


def list_queue(folder: Path | str, limit: int = 10) -> List[Dict]:
    """Return up to `limit` queue items from folder, newest first."""
    folder = Path(folder)
    if not folder.exists():
        return []
    items = []
    for f in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        data = load_json_safe(f)
        if data is not None:
            items.append(data)
        if len(items) >= limit:
            break
    return items


# ---------------------------------------------------------------------------
# Claude API wrapper
# ---------------------------------------------------------------------------

_API_LOG = _BASE / "state" / "block_l_api_calls.jsonl"


def claude_api_call(
    prompt: str,
    system: Optional[str] = None,
    model: str = "claude-sonnet-4-5",
    max_tokens: int = 2048,
    max_retries: int = 3,
) -> str:
    """
    Call Claude API with retry/backoff. Returns response text.
    Logs each call to state/block_l_api_calls.jsonl.
    Raises RuntimeError if all retries fail.
    """
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError("anthropic package not installed") from exc

    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    client = anthropic.Anthropic(api_key=api_key)

    messages = [{"role": "user", "content": prompt}]
    kwargs: Dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system

    last_err: Optional[Exception] = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.messages.create(**kwargs)
            text = response.content[0].text
            _log_api_call(model, len(prompt), len(text), None)
            return text
        except Exception as exc:
            last_err = exc
            _log_api_call(model, len(prompt), 0, str(exc))
            logger.warning("Claude API attempt %d/%d failed: %s", attempt, max_retries, exc)
            if attempt < max_retries:
                time.sleep(2 ** attempt)

    raise RuntimeError(f"Claude API failed after {max_retries} retries: {last_err}") from last_err


def _log_api_call(model: str, prompt_len: int, response_len: int, error: Optional[str]) -> None:
    try:
        _API_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.utcnow().isoformat(),
            "model": model,
            "prompt_len": prompt_len,
            "response_len": response_len,
            "error": error,
        }
        with _API_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------

_MAX_TG_LEN = 4096


def escape_html(text: str) -> str:
    """Escape special HTML chars for Telegram HTML parse mode."""
    return html.escape(text, quote=False)


def split_long_message(text: str, max_len: int = _MAX_TG_LEN) -> List[str]:
    """Split text into chunks of at most max_len characters."""
    if len(text) <= max_len:
        return [text]
    parts = []
    while text:
        parts.append(text[:max_len])
        text = text[max_len:]
    return parts


def telegram_safe_send(
    bot_or_fn,
    chat_id: int | str,
    text: str,
    parse_mode: str = "HTML",
    max_retries: int = 3,
) -> bool:
    """
    Send Telegram message with retry. Splits messages >4096 chars.
    `bot_or_fn` can be a python-telegram-bot Bot instance or any callable(chat_id, text, **kw).
    Returns True on success.
    """
    chunks = split_long_message(text)
    for chunk in chunks:
        for attempt in range(1, max_retries + 1):
            try:
                if callable(bot_or_fn) and not hasattr(bot_or_fn, "send_message"):
                    bot_or_fn(chat_id, chunk, parse_mode=parse_mode)
                else:
                    bot_or_fn.send_message(chat_id=chat_id, text=chunk, parse_mode=parse_mode)
                break
            except Exception as exc:
                logger.warning("Telegram send attempt %d/%d: %s", attempt, max_retries, exc)
                if attempt < max_retries:
                    time.sleep(1)
                else:
                    return False
    return True
