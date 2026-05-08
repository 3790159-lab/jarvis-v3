"""Phase 30: Error Reporter — global error capture, logging, Telegram notifications.

Usage:
    @error_reporter.capture
    def my_function():
        ...

Or manually:
    error_reporter.report(exc, context="describe what was happening")

Errors stored in state/errors.log (JSON Lines).
"""
from __future__ import annotations

import functools
import json
import logging
import os
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

ERRORS_PATH = Path("state") / "errors.log"
ERRORS_PATH.parent.mkdir(parents=True, exist_ok=True)

# Notify these chat_ids on critical errors
_NOTIFY_CHAT_IDS: List[str] = []
_SEND_FN: Optional[Callable[[str, str], None]] = None


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup(send_fn: Callable[[str, str], None], notify_chat_ids: List[str]) -> None:
    """Configure the reporter with a Telegram send function and target chat IDs."""
    global _SEND_FN, _NOTIFY_CHAT_IDS
    _SEND_FN = send_fn
    _NOTIFY_CHAT_IDS = list(notify_chat_ids)


# ---------------------------------------------------------------------------
# Core reporting
# ---------------------------------------------------------------------------

def report(
    exc: Exception,
    context: str = "",
    critical: bool = False,
    notify_user: bool = True,
    chat_id: Optional[str] = None,
) -> str:
    """Record an error and optionally notify the user.

    Returns error_id for later lookup.
    """
    error_id = uuid.uuid4().hex[:12]
    tb = traceback.format_exc()
    record = {
        "error_id": error_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "context": context or "",
        "exception_type": type(exc).__name__,
        "exception_message": str(exc)[:500],
        "traceback": tb[:3000],
        "critical": critical,
    }

    # Write to log
    try:
        with ERRORS_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as write_exc:
        logger.warning("Failed to write error log: %s", write_exc)

    logger.error("[%s] %s: %s | context=%s", error_id, type(exc).__name__, exc, context)

    # Telegram notifications
    if notify_user and _SEND_FN and chat_id:
        _safe_notify(chat_id, f"⚠️ Ошибка обработана. ID: {error_id[:8]}\n/errors trace {error_id[:8]}")

    if critical and _SEND_FN and _NOTIFY_CHAT_IDS:
        msg = (
            f"🚨 КРИТИЧЕСКАЯ ОШИБКА [{error_id[:8]}]\n"
            f"{type(exc).__name__}: {str(exc)[:200]}\n"
            f"Контекст: {context}\n\n"
            f"Детали: /errors trace {error_id[:8]}"
        )
        for cid in _NOTIFY_CHAT_IDS:
            _safe_notify(cid, msg)

    return error_id


def _safe_notify(chat_id: str, text: str) -> None:
    if _SEND_FN:
        try:
            _SEND_FN(chat_id, text)
        except Exception as e:
            logger.warning("Error reporter notify failed: %s", e)


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

def capture(func: Callable) -> Callable:
    """Decorator: catch and report any unhandled exception from the function."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            report(exc, context=f"{func.__module__}.{func.__qualname__}")
            raise
    return wrapper


def capture_silent(func: Callable) -> Callable:
    """Decorator: catch, report, and SWALLOW exceptions (return None)."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            report(exc, context=f"{func.__module__}.{func.__qualname__}")
            return None
    return wrapper


# ---------------------------------------------------------------------------
# Retry wrapper
# ---------------------------------------------------------------------------

def retry(max_attempts: int = 3, backoff: float = 2.0, exceptions=(Exception,)):
    """Decorator factory: retry on failure with exponential backoff."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            import time
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < max_attempts:
                        wait = backoff ** (attempt - 1)
                        logger.warning(
                            "[retry] %s attempt %d/%d failed: %s. Retrying in %.1fs",
                            func.__name__, attempt, max_attempts, exc, wait,
                        )
                        time.sleep(wait)
                    else:
                        logger.error(
                            "[retry] %s failed after %d attempts: %s",
                            func.__name__, max_attempts, exc,
                        )
            raise last_exc
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Log reading
# ---------------------------------------------------------------------------

def get_recent_errors(n: int = 10) -> List[Dict[str, Any]]:
    """Return the N most recent error records."""
    if not ERRORS_PATH.exists():
        return []
    try:
        lines = [l for l in ERRORS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
        records = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except Exception:
                pass
        return records[-n:]
    except Exception:
        return []


def get_error_by_id(error_id_prefix: str) -> Optional[Dict[str, Any]]:
    """Find an error record by prefix of error_id."""
    errors = get_recent_errors(n=500)
    return next((e for e in reversed(errors) if e["error_id"].startswith(error_id_prefix)), None)


def clear_errors() -> int:
    """Clear the error log. Returns number of cleared records."""
    if not ERRORS_PATH.exists():
        return 0
    try:
        lines = [l for l in ERRORS_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
        count = len(lines)
        ERRORS_PATH.write_text("", encoding="utf-8")
        return count
    except Exception:
        return 0


def format_recent_errors(n: int = 10) -> str:
    """Format recent errors for Telegram display."""
    errors = get_recent_errors(n)
    if not errors:
        return "✅ Нет ошибок в логе."

    lines = [f"🔴 Последние ошибки ({len(errors)}):"]
    for e in reversed(errors):
        ts = e.get("timestamp", "")[:16]
        eid = e.get("error_id", "?")[:8]
        exc_type = e.get("exception_type", "?")
        msg = e.get("exception_message", "")[:60]
        ctx = e.get("context", "")[:30]
        critical = "🚨" if e.get("critical") else "⚠️"
        lines.append(f"{critical} [{eid}] {ts} | {exc_type}: {msg}")
        if ctx:
            lines.append(f"   ctx: {ctx}")

    lines.append("\n/errors trace <id> — полный traceback")
    lines.append("/errors clear — очистить")
    return "\n".join(lines)
