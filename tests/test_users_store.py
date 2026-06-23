# tests/test_users_store.py
# -*- coding: utf-8 -*-
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from app.services.auth import users_store as us

KYIV = timezone(timedelta(hours=3))


@pytest.fixture
def store_env(tmp_path, monkeypatch):
    """Isolate users.json and the admin env for each test."""
    f = tmp_path / "users.json"
    monkeypatch.setenv("JARVIS_USERS_FILE", str(f))
    monkeypatch.delenv("JARVIS_ADMIN_USER_ID", raising=False)
    return f


def test_unknown_user_has_no_role(store_env):
    assert us.get_role(999) is None
    assert us.is_member(999) is False


def test_env_admin_is_admin_without_file(store_env, monkeypatch):
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    assert us.get_role(111) == "admin"
    assert us.is_member(111) is True
    # bootstrap-admin needs NO users.json entry
    assert not store_env.exists() or "111" not in store_env.read_text(encoding="utf-8")


def test_add_friend_persists_with_default_limit(store_env):
    us.add_friend(555, "petya", added_by="111")
    assert us.get_role(555) == "friend"
    assert us.get_limit(555) == us.DEFAULT_FRIEND_LIMIT_USD == 5.0
    data = json.loads(store_env.read_text(encoding="utf-8"))
    assert data["users"]["555"]["status"] == "active"
    assert data["users"]["555"]["added_by"] == "111"


def test_blocked_friend_has_no_role(store_env):
    us.add_friend(555, "petya", added_by="111")
    us.set_status(555, "blocked")
    assert us.get_role(555) is None
    assert us.is_member(555) is False


def test_set_limit_updates(store_env):
    us.add_friend(555, "petya", added_by="111")
    us.set_limit(555, 12.5)
    assert us.get_limit(555) == 12.5


def test_admin_limit_is_none_unlimited(store_env, monkeypatch):
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    assert us.get_limit(111) is None


def test_pending_dedup(store_env):
    assert us.add_pending(777, "stranger") is True   # new
    assert us.add_pending(777, "stranger") is False  # dup, no re-notify
    data = json.loads(store_env.read_text(encoding="utf-8"))
    assert data["pending"]["777"]["request_count"] == 2


def test_pop_pending_returns_and_removes(store_env):
    us.add_pending(777, "stranger")
    rec = us.pop_pending(777)
    assert rec is not None and rec["username"] == "stranger"
    assert us.pop_pending(777) is None


def test_has_members_and_list_users(store_env):
    assert us.has_members() is False
    us.add_friend(555, "petya", added_by="111")
    assert us.has_members() is True
    rows = us.list_users()
    assert any(r["user_id"] == "555" and r["role"] == "friend" for r in rows)


def test_reset_today_credit_only_applies_same_day(store_env):
    us.add_friend(555, "petya", added_by="111")
    today = datetime(2026, 6, 23, 12, 0, tzinfo=KYIV)
    us.record_reset(555, spent_today=3.40, when=today)
    # same day → credit forgives 3.40
    assert us.effective_spent(555, spent_today=4.0, when=today) == pytest.approx(0.60)
    # next day → credit expired
    nextday = datetime(2026, 6, 24, 12, 0, tzinfo=KYIV)
    assert us.effective_spent(555, spent_today=4.0, when=nextday) == pytest.approx(4.0)


def test_atomic_write_uses_replace(store_env, monkeypatch):
    called = {"n": 0}
    real = us.os.replace
    def spy(a, b):
        called["n"] += 1
        return real(a, b)
    monkeypatch.setattr(us.os, "replace", spy)
    us.add_friend(555, "petya", added_by="111")
    assert called["n"] >= 1


def test_whitelist_allows_active_member(store_env):
    from app.services.auth import whitelist
    us.add_friend(555, "petya", added_by="111")
    assert whitelist.is_allowed(555) is True
    us.set_status(555, "blocked")
    assert whitelist.is_allowed(555) is False


def test_whitelist_open_mode_only_when_nothing_configured(store_env, monkeypatch):
    from app.services.auth import whitelist
    monkeypatch.delenv("JARVIS_ALLOWED_USER_IDS", raising=False)
    # nothing configured + empty users.json → open mode preserved
    assert whitelist.is_allowed(12345) is True
    # once a member exists, open mode is OFF (strangers rejected)
    us.add_friend(555, "petya", added_by="111")
    assert whitelist.is_allowed(12345) is False
