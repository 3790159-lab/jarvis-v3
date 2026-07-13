# -*- coding: utf-8 -*-
"""Tests for the standalone evening error-digest job (scripts/error_digest.py).

Every network/IO seam is mocked — no real Telegram send, no real log files
under pytest. Mirrors tests/test_morning_digest_script.py's ``_load_module``
idiom and isolation-guard checks. Key behavioural difference from
morning_digest asserted here: an EMPTY digest sends NOTHING (spec: "тишина
= хорошо"), instead of morning_digest's always-one-message contract.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "error_digest.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("error_digest_script", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["error_digest_script"] = mod
    spec.loader.exec_module(mod)
    return mod


def _log_line(ts, level, logger_name, msg):
    return f"{ts} | {level:<8}| {logger_name:<30}| {msg}"


# ── send_telegram: shared test-isolation guard ───────────────────────────────


def test_send_telegram_blocked_under_test_isolation_no_network(monkeypatch, caplog):
    mod = _load_module()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "faketoken123")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "42")
    monkeypatch.delenv("JARVIS_ALLOW_TELEGRAM_SEND", raising=False)

    def boom(*a, **k):
        raise AssertionError("network send attempted under test isolation")

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    with caplog.at_level("INFO"):
        ok = mod.send_telegram("digest text")

    assert ok is False
    assert any("suppressed under test isolation" in r.getMessage() for r in caplog.records)


def test_send_telegram_sends_when_isolation_opted_out(monkeypatch):
    mod = _load_module()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "faketoken123")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "42")
    monkeypatch.setenv("JARVIS_ALLOW_TELEGRAM_SEND", "1")

    captured = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(req, timeout):  # noqa: ARG001
        captured["url"] = req.full_url
        return _FakeResponse()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    ok = mod.send_telegram("digest text")

    assert ok is True
    assert "faketoken123" in captured["url"]


# ── main(): full orchestration, everything mocked ────────────────────────────


def test_main_sends_nothing_when_no_fresh_errors(tmp_path, monkeypatch):
    """Core spec contract: empty digest -> zero Telegram calls."""
    mod = _load_module()
    monkeypatch.setattr(mod, "BOT_LOG_PATH", tmp_path / "jarvis_bot.log")
    monkeypatch.setattr(mod, "BACKEND_LOG_PATH", tmp_path / "jarvis.log")

    def boom(text):
        raise AssertionError("send_telegram must not be called for an empty digest")

    monkeypatch.setattr(mod, "send_telegram", boom)

    rc = mod.main([])
    assert rc == 0


def test_main_sends_digest_when_errors_found(tmp_path, monkeypatch):
    mod = _load_module()
    bot_log = tmp_path / "jarvis_bot.log"
    bot_log.write_text(
        _log_line("2026-07-13 20:00:00", "ERROR", "app.services.foo", "boom") + "\n"
        + _log_line("2026-07-13 20:05:00", "INFO", "app.services.foo", "irrelevant") + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "BOT_LOG_PATH", bot_log)
    monkeypatch.setattr(mod, "BACKEND_LOG_PATH", tmp_path / "jarvis.log")
    monkeypatch.setattr(mod, "datetime", _FixedDatetime)

    captured = {}

    def fake_send(text):
        captured["text"] = text
        return True

    monkeypatch.setattr(mod, "send_telegram", fake_send)

    rc = mod.main([])

    assert rc == 0
    assert "text" in captured
    assert "boom" in captured["text"]
    assert "irrelevant" not in captured["text"]


def test_main_dedupes_repeats_with_counter(tmp_path, monkeypatch):
    mod = _load_module()
    bot_log = tmp_path / "jarvis_bot.log"
    bot_log.write_text(
        (_log_line("2026-07-13 20:00:00", "ERROR", "app.services.foo", "boom") + "\n") * 3,
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "BOT_LOG_PATH", bot_log)
    monkeypatch.setattr(mod, "BACKEND_LOG_PATH", tmp_path / "jarvis.log")
    monkeypatch.setattr(mod, "datetime", _FixedDatetime)

    captured = {}
    monkeypatch.setattr(mod, "send_telegram", lambda text: captured.setdefault("text", text) or True)

    mod.main([])

    assert "(x3)" in captured["text"]


def test_main_respects_noise_pattern_env_filter(tmp_path, monkeypatch):
    mod = _load_module()
    bot_log = tmp_path / "jarvis_bot.log"
    bot_log.write_text(
        _log_line("2026-07-13 20:00:00", "ERROR", "x", "known flaky retry") + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "BOT_LOG_PATH", bot_log)
    monkeypatch.setattr(mod, "BACKEND_LOG_PATH", tmp_path / "jarvis.log")
    monkeypatch.setattr(mod, "datetime", _FixedDatetime)
    monkeypatch.setenv("ERROR_DIGEST_NOISE_PATTERNS", "known flaky")

    def boom(text):
        raise AssertionError("send_telegram must not be called once noise is filtered out")

    monkeypatch.setattr(mod, "send_telegram", boom)

    rc = mod.main([])
    assert rc == 0
    monkeypatch.delenv("ERROR_DIGEST_NOISE_PATTERNS", raising=False)


class _FixedDatetime:
    """Freezes ``datetime.now()`` inside the script to right after the fixture
    log lines so the 24h window always includes them, regardless of wall-clock."""

    @classmethod
    def now(cls):
        import datetime as _dt
        return _dt.datetime(2026, 7, 13, 21, 0, 0)
