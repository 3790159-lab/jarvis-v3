"""Tests for the IG token auto-refresh job (scripts/ig_token_refresh.py).

Pure-helper coverage — no network, no scheduler. Focus on the fail-closed
guarantees: the .env token line is replaced surgically (nothing else touched),
a backup is made, and the age-gate decides when to act.
"""
from __future__ import annotations

import importlib.util
import os
import sys
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
