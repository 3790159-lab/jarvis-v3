# -*- coding: utf-8 -*-
"""Unified Telegram notification helper.

Provides both a sync entry point (for legacy / non-async callers like the
system watchdog) and an async one (for callers running in an event loop,
such as the RunPod guardian).

Read the bot token and admin chat id from ``TELEGRAM_BOT_TOKEN`` /
``TELEGRAM_ALLOWED_CHAT_ID`` by default. If either is missing the
notifier is considered "not configured": calls log a warning and return
``False`` instead of raising — alerting is best-effort by design.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from functools import lru_cache

import httpx

from app.core.notify_isolation import telegram_send_blocked

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org"
_DEFAULT_TIMEOUT = 10.0


def _mask_token(token: str) -> str:
    if not token:
        return "***"
    if len(token) <= 12:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


class TelegramNotifier:
    """Synchronous + async Telegram alerter."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        self._bot_token = (
            bot_token if bot_token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")
        ).strip()
        self._chat_id = (
            chat_id if chat_id is not None else os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "")
        ).strip()

    # -- diagnostics ----------------------------------------------------------

    def is_configured(self) -> bool:
        return bool(self._bot_token and self._chat_id)

    @property
    def _send_url(self) -> str:
        return f"{_TELEGRAM_API}/bot{self._bot_token}/sendMessage"

    def _format(self, text: str, prefix: str) -> str:
        return f"{prefix}\n{text}" if prefix else text

    # -- sync -----------------------------------------------------------------

    def send(self, text: str, *, prefix: str = "🔔 Jarvis") -> bool:
        if telegram_send_blocked():
            logger.debug("Telegram alert suppressed under test isolation")
            return False
        if not self.is_configured():
            logger.warning(
                "Telegram alert skipped: notifier not configured "
                "(bot_token=%s, chat_id=%s)",
                _mask_token(self._bot_token),
                "set" if self._chat_id else "missing",
            )
            return False

        body = json.dumps(
            {"chat_id": self._chat_id, "text": self._format(text, prefix)}
        ).encode("utf-8")
        request = urllib.request.Request(
            self._send_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=_DEFAULT_TIMEOUT) as response:
                ok = 200 <= response.status < 300
        except (urllib.error.URLError, OSError) as exc:
            logger.error(
                "Telegram alert failed (token=%s): %s",
                _mask_token(self._bot_token),
                exc,
            )
            return False
        if not ok:
            logger.error(
                "Telegram alert returned non-2xx (token=%s)",
                _mask_token(self._bot_token),
            )
        return ok

    # -- async ----------------------------------------------------------------

    async def send_async(self, text: str, *, prefix: str = "🔔 Jarvis") -> bool:
        if telegram_send_blocked():
            logger.debug("Telegram alert suppressed under test isolation")
            return False
        if not self.is_configured():
            logger.warning(
                "Telegram alert skipped: notifier not configured "
                "(bot_token=%s, chat_id=%s)",
                _mask_token(self._bot_token),
                "set" if self._chat_id else "missing",
            )
            return False

        payload = {"chat_id": self._chat_id, "text": self._format(text, prefix)}
        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
                response = await client.post(self._send_url, json=payload)
        except httpx.HTTPError as exc:
            logger.error(
                "Telegram async alert failed (token=%s): %s",
                _mask_token(self._bot_token),
                exc,
            )
            return False

        if response.status_code >= 400:
            logger.error(
                "Telegram async alert returned HTTP %s (token=%s)",
                response.status_code,
                _mask_token(self._bot_token),
            )
            return False
        return True


@lru_cache(maxsize=1)
def get_default_notifier() -> TelegramNotifier:
    """Return a process-wide cached :class:`TelegramNotifier`."""
    return TelegramNotifier()


def send_alert(text: str) -> bool:
    """Convenience wrapper around :meth:`TelegramNotifier.send`."""
    return get_default_notifier().send(text)
