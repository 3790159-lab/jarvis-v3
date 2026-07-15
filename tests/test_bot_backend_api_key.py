"""Enforce prerequisite: the bot must send X-API-Key to the backend, not Telegram.

Under enforce, every bot->backend call (/research, /ig_gen image/generate,
brain/plan, ...) is gated. The bot's http_json helper is shared with Telegram
API calls, so the key must be attached ONLY for BACKEND URLs — never leaked to
api.telegram.org.
"""
from __future__ import annotations

import importlib.util
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KEY = "test-internal-key-bot"


def _load_bot(monkeypatch):
    monkeypatch.setenv("JARVIS_INTERNAL_API_KEY", KEY)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test:token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "1")
    mod_name = f"_bot_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _capture_headers(monkeypatch):
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def _fake_urlopen(req, timeout=None):
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["url"] = req.full_url
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    return captured


def test_backend_call_gets_api_key(monkeypatch):
    mod = _load_bot(monkeypatch)
    cap = _capture_headers(monkeypatch)
    mod.backend_get("/api/jarvis/tools/internet/research")
    assert cap["headers"].get("x-api-key") == KEY


def test_telegram_call_does_not_get_api_key(monkeypatch):
    """The internal key must never be sent to api.telegram.org."""
    mod = _load_bot(monkeypatch)
    cap = _capture_headers(monkeypatch)
    mod.tg_call("getMe", {})
    assert "api.telegram.org" in cap["url"]
    assert "x-api-key" not in cap["headers"]
