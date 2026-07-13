# -*- coding: utf-8 -*-
"""Tests for the standalone morning digest job (scripts/morning_digest.py).

Every network/IO seam is mocked — no real Telegram, Graph API, backend HTTP,
or Anthropic call is ever made under pytest. Each ``gather_*`` fail-closed
branch is exercised independently, then ``main()`` end-to-end with everything
mocked to prove the digest still assembles and "sends" when sources fail.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "morning_digest.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("morning_digest_script", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["morning_digest_script"] = mod
    spec.loader.exec_module(mod)
    return mod


# ── gather_ig_accounts ───────────────────────────────────────────────────────


def test_gather_ig_accounts_happy_path(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "ig_accounts.json"))
    monkeypatch.setenv("DAILY_METRICS_FILE", str(tmp_path / "daily_metrics.json"))
    from app.services import ig_accounts as iga
    iga.save_account("jtest_lab_", ig_user_id="111", username="jtest_lab_",
                      access_token="TOK", token_refreshed_at=time.time())

    from app.services import instagram_api
    monkeypatch.setattr(
        instagram_api.InstagramAPI, "get_profile",
        lambda self, fields="": {"username": "jtest_lab_", "followers_count": 100, "media_count": 20},
    )

    out = mod.gather_ig_accounts()
    assert len(out) == 1
    assert out[0]["followers"] == 100
    assert out[0]["media_count"] == 20
    assert out[0]["followers_delta"] is None  # first run, no baseline yet


def test_gather_ig_accounts_second_run_has_delta(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "ig_accounts.json"))
    monkeypatch.setenv("DAILY_METRICS_FILE", str(tmp_path / "daily_metrics.json"))
    from app.services import ig_accounts as iga
    iga.save_account("jtest_lab_", access_token="TOK", token_refreshed_at=time.time())

    from app.services import instagram_api
    profiles = iter([
        {"username": "jtest_lab_", "followers_count": 100, "media_count": 20},
        {"username": "jtest_lab_", "followers_count": 105, "media_count": 21},
    ])
    monkeypatch.setattr(
        instagram_api.InstagramAPI, "get_profile",
        lambda self, fields="": next(profiles),
    )

    mod.gather_ig_accounts()
    out = mod.gather_ig_accounts()
    assert out[0]["followers_delta"] == 5
    assert out[0]["media_delta"] == 1


def test_gather_ig_accounts_fetch_failure_is_honest_not_crash(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "ig_accounts.json"))
    monkeypatch.setenv("DAILY_METRICS_FILE", str(tmp_path / "daily_metrics.json"))
    from app.services import ig_accounts as iga
    iga.save_account("vera_ai_ua", access_token="TOK", token_refreshed_at=time.time())

    from app.services import instagram_api
    def _boom(self, fields=""):
        raise instagram_api.InstagramAPIError("rate limited")
    monkeypatch.setattr(instagram_api.InstagramAPI, "get_profile", _boom)

    out = mod.gather_ig_accounts()
    assert out[0]["account_key"] == "vera_ai_ua"
    assert "error" in out[0]


def test_gather_ig_accounts_no_accounts_returns_empty(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "ig_accounts.json"))
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)
    assert mod.gather_ig_accounts() == []


# ── gather_token_ages ─────────────────────────────────────────────────────────


def test_gather_token_ages_computes_days_left(monkeypatch):
    mod = _load_module()
    now = 1_000_000_000.0
    monkeypatch.setattr(mod.time, "time", lambda: now)
    raw = {"jtest_lab_": {"token_refreshed_at": now - 10 * 86400}}
    out = mod.gather_token_ages(raw)
    assert out[0]["account_key"] == "jtest_lab_"
    assert abs(out[0]["days_left"] - 50.0) < 0.01  # 60 - 10


def test_gather_token_ages_missing_timestamp_is_na():
    mod = _load_module()
    out = mod.gather_token_ages({"a": {}})
    assert out[0]["days_left"] is None
    assert "error" in out[0]


def test_gather_token_ages_bad_timestamp_is_na():
    mod = _load_module()
    out = mod.gather_token_ages({"a": {"token_refreshed_at": "not-a-number"}})
    assert out[0]["days_left"] is None


# ── gather_bot_health ─────────────────────────────────────────────────────────


def test_gather_bot_health_alive(tmp_path, monkeypatch):
    mod = _load_module()
    hb = tmp_path / "bot_heartbeat.txt"
    now = 1_000_000_000.0
    hb.write_text(str(int(now - 10)), encoding="utf-8")
    monkeypatch.setattr(mod, "HEARTBEAT_FILE", hb)
    monkeypatch.setattr(mod.time, "time", lambda: now)

    alive, age = mod.gather_bot_health()
    assert alive is True
    assert abs(age - 10) < 1.0


def test_gather_bot_health_stale(tmp_path, monkeypatch):
    mod = _load_module()
    hb = tmp_path / "bot_heartbeat.txt"
    now = 1_000_000_000.0
    hb.write_text(str(int(now - 10_000)), encoding="utf-8")
    monkeypatch.setattr(mod, "HEARTBEAT_FILE", hb)
    monkeypatch.setattr(mod.time, "time", lambda: now)

    alive, age = mod.gather_bot_health()
    assert alive is False


def test_gather_bot_health_missing_file_is_na(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setattr(mod, "HEARTBEAT_FILE", tmp_path / "nope.txt")
    alive, age = mod.gather_bot_health()
    assert alive is None
    assert age is None


# ── gather_backend_ok ─────────────────────────────────────────────────────────


def test_gather_backend_ok_true(monkeypatch):
    mod = _load_module()
    from app.services import system_watchdog as wd
    monkeypatch.setattr(wd, "check_backend", lambda: {"ok": True, "detail": "200"})
    assert mod.gather_backend_ok() is True


def test_gather_backend_ok_false(monkeypatch):
    mod = _load_module()
    from app.services import system_watchdog as wd
    monkeypatch.setattr(wd, "check_backend", lambda: {"ok": False, "detail": "timeout"})
    assert mod.gather_backend_ok() is False


def test_gather_backend_ok_exception_is_na(monkeypatch):
    mod = _load_module()
    from app.services import system_watchdog as wd
    def _boom():
        raise RuntimeError("boom")
    monkeypatch.setattr(wd, "check_backend", _boom)
    assert mod.gather_backend_ok() is None


# ── gather_costs_yesterday ────────────────────────────────────────────────────


def test_gather_costs_yesterday_returns_breakdown(monkeypatch):
    mod = _load_module()
    from app.services.block_m_common import cost_tracker as ct

    class _FakeTracker:
        def __init__(self, *a, **k):
            pass

        async def get_costs_by_day(self, target_date):
            return {"kling_video": 0.30}

    monkeypatch.setattr(ct, "CostTracker", _FakeTracker)
    out = mod.gather_costs_yesterday()
    assert out == {"kling_video": 0.30}


def test_gather_costs_yesterday_failure_is_na(monkeypatch):
    mod = _load_module()
    from app.services.block_m_common import cost_tracker as ct

    class _BoomTracker:
        def __init__(self, *a, **k):
            raise RuntimeError("disk error")

    monkeypatch.setattr(ct, "CostTracker", _BoomTracker)
    assert mod.gather_costs_yesterday() is None


# ── gather_balance_ok ─────────────────────────────────────────────────────────


def test_gather_balance_ok_true(monkeypatch):
    mod = _load_module()
    from app.services.devtask import preflight
    monkeypatch.setattr(preflight, "preflight_credit_check", lambda: {"ok": True, "reason": None})
    assert mod.gather_balance_ok() is True


def test_gather_balance_ok_depleted(monkeypatch):
    mod = _load_module()
    from app.services.devtask import preflight
    monkeypatch.setattr(
        preflight, "preflight_credit_check",
        lambda: {"ok": False, "reason": "credit balance too low"},
    )
    assert mod.gather_balance_ok() is False


def test_gather_balance_ok_exception_is_na(monkeypatch):
    mod = _load_module()
    from app.services.devtask import preflight
    def _boom():
        raise RuntimeError("network down")
    monkeypatch.setattr(preflight, "preflight_credit_check", _boom)
    assert mod.gather_balance_ok() is None


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


def test_main_end_to_end_all_sources_ok(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "ig_accounts.json"))
    monkeypatch.setenv("DAILY_METRICS_FILE", str(tmp_path / "daily_metrics.json"))
    monkeypatch.setenv("JARVIS_ALLOW_TELEGRAM_SEND", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "faketoken")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "42")

    from app.services import ig_accounts as iga
    iga.save_account("jtest_lab_", access_token="TOK", token_refreshed_at=time.time())

    from app.services import instagram_api
    monkeypatch.setattr(
        instagram_api.InstagramAPI, "get_profile",
        lambda self, fields="": {"username": "jtest_lab_", "followers_count": 100, "media_count": 20},
    )

    from app.services import system_watchdog as wd
    monkeypatch.setattr(wd, "check_backend", lambda: {"ok": True, "detail": "200"})

    from app.services.block_m_common import cost_tracker as ct
    class _FakeTracker:
        def __init__(self, *a, **k):
            pass
        async def get_costs_by_day(self, target_date):
            return {"kling_video": 0.30}
    monkeypatch.setattr(ct, "CostTracker", _FakeTracker)

    from app.services.devtask import preflight
    monkeypatch.setattr(preflight, "preflight_credit_check", lambda: {"ok": True, "reason": None})

    captured = {}

    def fake_send(text):
        captured["text"] = text
        return True

    monkeypatch.setattr(mod, "send_telegram", fake_send)

    rc = mod.main([])

    assert rc == 0
    assert "jtest_lab_" in captured["text"]
    assert "kling_video" in captured["text"]
    assert "✅" in captured["text"]


def test_main_all_sources_down_still_sends_one_message(tmp_path, monkeypatch):
    """Fail-closed contract end-to-end: every gatherer fails, digest still 'sends'."""
    mod = _load_module()
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "ig_accounts.json"))
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)

    from app.services import system_watchdog as wd
    def _boom_backend():
        raise RuntimeError("down")
    monkeypatch.setattr(wd, "check_backend", _boom_backend)

    from app.services.block_m_common import cost_tracker as ct
    class _BoomTracker:
        def __init__(self, *a, **k):
            raise RuntimeError("disk error")
    monkeypatch.setattr(ct, "CostTracker", _BoomTracker)

    from app.services.devtask import preflight
    def _boom_balance():
        raise RuntimeError("network down")
    monkeypatch.setattr(preflight, "preflight_credit_check", _boom_balance)

    monkeypatch.setattr(mod, "HEARTBEAT_FILE", tmp_path / "nope.txt")

    captured = {}

    def fake_send(text):
        captured["text"] = text
        return True

    monkeypatch.setattr(mod, "send_telegram", fake_send)

    rc = mod.main([])

    assert rc == 0
    assert "text" in captured
    assert "N/A" in captured["text"]
