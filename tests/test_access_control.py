# tests/test_access_control.py
# -*- coding: utf-8 -*-
from datetime import datetime, timezone, timedelta

import pytest

from app.services.auth import access_control as ac
from app.services.auth import users_store as us

KYIV = timezone(timedelta(hours=3))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_USERS_FILE", str(tmp_path / "users.json"))
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    return tmp_path


def test_admin_unlimited(env):
    allowed, reason = ac.check_limit(111, estimated_usd=999.0)
    assert allowed is True and reason == ""


def test_unknown_user_blocked(env):
    allowed, _ = ac.check_limit(999, estimated_usd=0.1)
    assert allowed is False


def test_friend_under_limit_allowed(env):
    us.add_friend(555, "p", added_by="111", limit_usd=5.0)
    allowed, _ = ac.check_limit(555, estimated_usd=1.0)
    assert allowed is True


def test_friend_over_limit_blocked_before_spend(env):
    from app.services.audit import cost_tracker as ct
    us.add_friend(555, "p", added_by="111", limit_usd=5.0)
    ct.record_cost(555, "p", 4.50)  # spent today
    allowed, reason = ac.check_limit(555, estimated_usd=1.0)  # 4.5 + 1.0 > 5.0
    assert allowed is False
    assert "лимит" in reason.lower()


def test_reset_override_forgives_today(env):
    from app.services.audit import cost_tracker as ct
    us.add_friend(555, "p", added_by="111", limit_usd=5.0)
    ct.record_cost(555, "p", 4.50)
    us.record_reset(555, spent_today=4.50)  # forgive
    allowed, _ = ac.check_limit(555, estimated_usd=1.0)  # effective 0 + 1 <= 5
    assert allowed is True
