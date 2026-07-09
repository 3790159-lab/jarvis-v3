# -*- coding: utf-8 -*-
"""Tests for the unified Telegram notifier."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services import notifications
from app.services.notifications import (
    TelegramNotifier,
    get_default_notifier,
    send_alert,
)


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    # These tests deliberately exercise the (mocked) transport, so opt in past
    # the global test-isolation guard that otherwise suppresses sends.
    monkeypatch.setenv("JARVIS_ALLOW_TELEGRAM_SEND", "1")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_ID", raising=False)
    get_default_notifier.cache_clear()
    yield
    get_default_notifier.cache_clear()


# ── test-isolation guard (defensive flag) ────────────────────────────────────


def test_send_suppressed_under_pytest_without_optin(monkeypatch):
    """With the opt-in removed, a fully-configured notifier must NOT hit the
    network during a pytest run — this is the belt that stops phantoms."""
    monkeypatch.delenv("JARVIS_ALLOW_TELEGRAM_SEND", raising=False)
    n = TelegramNotifier(bot_token="testtoken123456", chat_id="42")

    def fake_urlopen(req, timeout):  # noqa: ARG001
        raise AssertionError("network send attempted under pytest")

    with patch.object(notifications.urllib.request, "urlopen", fake_urlopen):
        ok = n.send("phantom")

    assert ok is False


@pytest.mark.anyio
async def test_send_async_suppressed_under_pytest_without_optin(monkeypatch):
    monkeypatch.delenv("JARVIS_ALLOW_TELEGRAM_SEND", raising=False)
    n = TelegramNotifier(bot_token="abc1234567890", chat_id="555")

    def boom(*a, **k):
        raise AssertionError("network send attempted under pytest")

    with patch.object(notifications.httpx, "AsyncClient", boom):
        ok = await n.send_async("phantom")

    assert ok is False


# ── configuration / no-op behaviour ──────────────────────────────────────────


def test_notifier_not_configured(caplog):
    n = TelegramNotifier()
    assert n.is_configured() is False
    with caplog.at_level("WARNING"):
        assert n.send("hello") is False
    assert any("not configured" in r.getMessage() for r in caplog.records)


# ── sync send ───────────────────────────────────────────────────────────────


def test_notifier_send_calls_telegram_api():
    n = TelegramNotifier(bot_token="testtoken123456", chat_id="42")

    captured: dict = {}

    class _FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout):  # noqa: ARG001
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["headers"] = dict(req.header_items())
        captured["body"] = req.data
        return _FakeResponse()

    with patch.object(notifications.urllib.request, "urlopen", fake_urlopen):
        ok = n.send("budget exceeded", prefix="🛡️ TEST")

    assert ok is True
    assert captured["url"] == (
        "https://api.telegram.org/bottesttoken123456/sendMessage"
    )
    assert captured["method"] == "POST"
    body = json.loads(captured["body"].decode("utf-8"))
    assert body["chat_id"] == "42"
    assert body["text"] == "🛡️ TEST\nbudget exceeded"
    assert captured["headers"]["Content-type"] == "application/json"


def test_notifier_send_returns_false_on_url_error(caplog):
    n = TelegramNotifier(bot_token="testtoken123456", chat_id="42")

    def fake_urlopen(req, timeout):  # noqa: ARG001
        raise OSError("network down")

    with patch.object(notifications.urllib.request, "urlopen", fake_urlopen):
        with caplog.at_level("ERROR"):
            ok = n.send("hi")

    assert ok is False
    assert any("Telegram alert failed" in r.getMessage() for r in caplog.records)


# ── async send ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_send_async_uses_httpx():
    n = TelegramNotifier(bot_token="abc1234567890", chat_id="555")

    fake_response = MagicMock(spec=httpx.Response)
    fake_response.status_code = 200

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.post = AsyncMock(return_value=fake_response)

    with patch.object(notifications.httpx, "AsyncClient", return_value=fake_client):
        ok = await n.send_async("budget!", prefix="🛡️ G")

    assert ok is True
    fake_client.post.assert_awaited_once()
    call = fake_client.post.await_args
    url = call.args[0]
    payload = call.kwargs["json"]
    assert url == "https://api.telegram.org/botabc1234567890/sendMessage"
    assert payload == {"chat_id": "555", "text": "🛡️ G\nbudget!"}


@pytest.mark.anyio
async def test_send_async_returns_false_on_http_error():
    n = TelegramNotifier(bot_token="abc1234567890", chat_id="555")

    fake_response = MagicMock(spec=httpx.Response)
    fake_response.status_code = 500

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.post = AsyncMock(return_value=fake_response)

    with patch.object(notifications.httpx, "AsyncClient", return_value=fake_client):
        ok = await n.send_async("hi")

    assert ok is False


# ── singleton + shortcut ─────────────────────────────────────────────────────


def test_get_default_notifier_singleton():
    a = get_default_notifier()
    b = get_default_notifier()
    assert a is b


def test_send_alert_uses_default_notifier(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tokTOKtoken1234")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "999")
    get_default_notifier.cache_clear()

    captured: dict = {}

    class _FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def fake_urlopen(req, timeout):  # noqa: ARG001
        captured["body"] = req.data
        return _FakeResponse()

    with patch.object(notifications.urllib.request, "urlopen", fake_urlopen):
        ok = send_alert("hi from shortcut")

    assert ok is True
    body = json.loads(captured["body"].decode("utf-8"))
    assert body["chat_id"] == "999"
    assert body["text"].endswith("hi from shortcut")
