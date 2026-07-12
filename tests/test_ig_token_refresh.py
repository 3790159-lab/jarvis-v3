"""Tests for the IG token auto-refresh job (scripts/ig_token_refresh.py).

Pure-helper coverage — no network, no scheduler. Focus on the fail-closed
guarantees: the .env token line is replaced surgically (nothing else touched),
a backup is made, and the age-gate decides when to act.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ig_token_refresh.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("ig_token_refresh", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ig_token_refresh"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_update_env_token_replaces_only_that_line(tmp_path):
    mod = _load_module()
    env = tmp_path / ".env"
    env.write_text(
        "FOO=bar\n"
        "IG_ACCESS_TOKEN=OLDTOKEN\n"
        "IG_USER_ID=17841412939799614\n"
        "# comment\n",
        encoding="utf-8",
    )
    mod.update_env_token(str(env), "NEWTOKEN")
    out = env.read_text(encoding="utf-8")
    assert "IG_ACCESS_TOKEN=NEWTOKEN\n" in out
    assert "OLDTOKEN" not in out
    # everything else preserved byte-for-byte
    assert "FOO=bar\n" in out
    assert "IG_USER_ID=17841412939799614\n" in out
    assert "# comment\n" in out


def test_update_env_token_makes_backup(tmp_path):
    mod = _load_module()
    env = tmp_path / ".env"
    env.write_text("IG_ACCESS_TOKEN=OLDTOKEN\n", encoding="utf-8")
    mod.update_env_token(str(env), "NEWTOKEN")
    backups = list(tmp_path.glob(".env.bak_igrefresh*"))
    assert backups, "a backup of the original .env must exist"
    assert "OLDTOKEN" in backups[0].read_text(encoding="utf-8")


def test_update_env_token_missing_key_raises_fail_closed(tmp_path):
    """No IG_ACCESS_TOKEN line -> refuse to write (config error), don't append."""
    mod = _load_module()
    env = tmp_path / ".env"
    env.write_text("FOO=bar\n", encoding="utf-8")
    try:
        mod.update_env_token(str(env), "NEWTOKEN")
        assert False, "should raise"
    except Exception as e:
        assert "IG_ACCESS_TOKEN" in str(e)
    # unchanged
    assert env.read_text(encoding="utf-8") == "FOO=bar\n"


def test_update_env_token_empty_new_raises(tmp_path):
    mod = _load_module()
    env = tmp_path / ".env"
    env.write_text("IG_ACCESS_TOKEN=OLDTOKEN\n", encoding="utf-8")
    try:
        mod.update_env_token(str(env), "")
        assert False, "should raise on empty token (fail-closed)"
    except Exception:
        pass
    assert "OLDTOKEN" in env.read_text(encoding="utf-8")


# ── send_telegram: shared test-isolation guard ───────────────────────────────
#
# This job is standalone (module docstring: "does NOT depend on the live
# bot"), so it has its own send_telegram() rather than going through
# app.services.notifications. Before this fix it skipped the shared
# app.core.notify_isolation guard entirely -- a test that forgets to
# monkeypatch send_telegram (as every test_main_* below does) would reach
# the real admin chat if TELEGRAM_BOT_TOKEN/CHAT_ID leaked in from .env.


def test_send_telegram_blocked_under_test_isolation_no_network(monkeypatch, caplog):
    mod = _load_module()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "faketoken123")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", "42")
    monkeypatch.delenv("JARVIS_ALLOW_TELEGRAM_SEND", raising=False)

    def boom(*a, **k):
        raise AssertionError("network send attempted under test isolation")

    monkeypatch.setattr(mod.urllib.request, "urlopen", boom)
    with caplog.at_level("INFO"):
        ok = mod.send_telegram("phantom alarm")

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
    ok = mod.send_telegram("real alarm")

    assert ok is True
    assert "faketoken123" in captured["url"]


def test_should_refresh_age_gate():
    mod = _load_module()
    # no prior refresh recorded -> establish baseline (refresh)
    assert mod.should_refresh(None, min_age_days=30) is True
    # too fresh -> skip
    assert mod.should_refresh(5.0, min_age_days=30) is False
    # old enough -> refresh
    assert mod.should_refresh(31.0, min_age_days=30) is True
    assert mod.should_refresh(30.0, min_age_days=30) is True


def test_read_age_days_missing_file_returns_none(tmp_path):
    mod = _load_module()
    assert mod.read_age_days(str(tmp_path / "nope.txt")) is None


def test_read_age_days_from_timestamp(tmp_path, monkeypatch):
    mod = _load_module()
    stamp = tmp_path / "ts"
    # 10 days ago
    now = 1_000_000_000.0
    stamp.write_text(str(now - 10 * 86400), encoding="utf-8")
    monkeypatch.setattr(mod.time, "time", lambda: now)
    age = mod.read_age_days(str(stamp))
    assert abs(age - 10.0) < 0.01


def test_age_days_from_timestamp_none_returns_none():
    mod = _load_module()
    assert mod.age_days_from_timestamp(None) is None


def test_age_days_from_timestamp_computes_days(monkeypatch):
    mod = _load_module()
    now = 1_000_000_000.0
    monkeypatch.setattr(mod.time, "time", lambda: now)
    age = mod.age_days_from_timestamp(now - 15 * 86400)
    assert abs(age - 15.0) < 0.01


def test_age_days_from_timestamp_bad_value_returns_none():
    mod = _load_module()
    assert mod.age_days_from_timestamp("not-a-number") is None


# ── multi-account main() (state/ig_accounts.json) ──────────────────────────


def test_main_no_accounts_json_falls_back_to_legacy(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "ig_accounts.json"))
    monkeypatch.delenv("IG_ACCESS_TOKEN", raising=False)  # nothing to migrate
    calls = {"legacy": 0}
    monkeypatch.setattr(mod, "_refresh_legacy", lambda force: calls.__setitem__("legacy", 1) or 0)

    rc = mod.main([])

    assert calls["legacy"] == 1
    assert rc == 0


def test_main_two_accounts_force_refreshes_both(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "ig_accounts.json"))
    from app.services import ig_accounts as iga
    iga.save_account("jtest_lab_", ig_user_id="111", username="jtest_lab_",
                     access_token="OLD_JTEST", token_refreshed_at=1000.0)
    iga.save_account("vera_ai_ua", ig_user_id="222", username="vera.ai.ua",
                     access_token="OLD_VERA", token_refreshed_at=1000.0)

    monkeypatch.setattr("app.services.instagram_api.refresh_long_lived_token",
                        lambda tok, base=None: f"NEW_{tok}")
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: sent.append(text) or True)

    rc = mod.main(["--force"])

    assert rc == 0
    accounts = iga.list_accounts()
    assert accounts["jtest_lab_"]["access_token"] == "NEW_OLD_JTEST"
    assert accounts["vera_ai_ua"]["access_token"] == "NEW_OLD_VERA"
    assert any("jtest_lab_" in t for t in sent)
    assert any("vera_ai_ua" in t for t in sent)


def test_main_per_account_age_gate_only_refreshes_stale(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "ig_accounts.json"))
    from app.services import ig_accounts as iga
    now = time.time()
    iga.save_account("jtest_lab_", access_token="TOK_A", token_refreshed_at=now - 5 * 86400)
    iga.save_account("vera_ai_ua", access_token="TOK_B", token_refreshed_at=now - 40 * 86400)

    calls = []
    monkeypatch.setattr("app.services.instagram_api.refresh_long_lived_token",
                        lambda tok, base=None: calls.append(tok) or f"NEW_{tok}")
    monkeypatch.setattr(mod, "send_telegram", lambda text: True)

    rc = mod.main([])                          # no --force -> age-gated

    assert calls == ["TOK_B"]                   # only the stale account refreshed
    accounts = iga.list_accounts()
    assert accounts["jtest_lab_"]["access_token"] == "TOK_A"     # untouched, fresh
    assert accounts["vera_ai_ua"]["access_token"] == "NEW_TOK_B"
    assert rc == 0


def test_main_account_refresh_failure_alarms_by_name_others_unaffected(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "ig_accounts.json"))
    from app.services import ig_accounts as iga
    iga.save_account("jtest_lab_", access_token="TOK_A", token_refreshed_at=1000.0)
    iga.save_account("vera_ai_ua", access_token="TOK_B", token_refreshed_at=1000.0)

    def _refresh(tok, base=None):
        if tok == "TOK_A":
            raise RuntimeError("Graph API rejected token")
        return f"NEW_{tok}"

    monkeypatch.setattr("app.services.instagram_api.refresh_long_lived_token", _refresh)
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: sent.append(text) or True)

    rc = mod.main(["--force"])

    assert rc == 1                              # honest non-zero: one account failed
    accounts = iga.list_accounts()
    assert accounts["jtest_lab_"]["access_token"] == "TOK_A"      # fail-closed, kept
    assert accounts["vera_ai_ua"]["access_token"] == "NEW_TOK_B"  # other account unaffected
    assert any("jtest_lab_" in t and "ПРОВАЛ" in t for t in sent)
    assert any("vera_ai_ua" in t and "✅" in t for t in sent)


def test_main_account_missing_token_alarms_and_skips(tmp_path, monkeypatch):
    mod = _load_module()
    monkeypatch.setenv("IG_ACCOUNTS_FILE", str(tmp_path / "ig_accounts.json"))
    from app.services import ig_accounts as iga
    iga.save_account("jtest_lab_", access_token="", token_refreshed_at=1000.0)

    calls = {"refresh": 0}
    monkeypatch.setattr("app.services.instagram_api.refresh_long_lived_token",
                        lambda tok, base=None: calls.__setitem__("refresh", 1) or "NEW")
    sent = []
    monkeypatch.setattr(mod, "send_telegram", lambda text: sent.append(text) or True)

    rc = mod.main(["--force"])

    assert calls["refresh"] == 0
    assert rc == 2
    assert any("jtest_lab_" in t for t in sent)
