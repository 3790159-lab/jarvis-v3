# tests/test_audit_activity.py
# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

from app.services.audit import audit_logger as al


@pytest.fixture
def audit_env(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_AUDIT_DIR", str(tmp_path / "audit"))
    al._reset_seen_users_cache()
    return tmp_path


def test_read_user_activity_filters_and_limits(audit_env):
    al.audit_event(555, "petya", "555", "command", {"command": "/swapbatch_go"})
    al.audit_event(555, "petya", "555", "animate_started", {"mode": "yes"})
    al.audit_event(999, "other", "999", "command", {"command": "/start"})
    rows = al.read_user_activity(555, limit=10)
    # only user 555 events come back (user 999 excluded)
    assert all(r["user_id"] == 555 for r in rows)
    # the first real event for a brand-new user auto-prepends a synthetic
    # user_first_seen event (same user_id), so user 555 has 3 events total:
    # user_first_seen + command + animate_started.
    assert len(rows) == 3
    events = [r["event"] for r in rows]
    assert "command" in events
    assert "animate_started" in events
    assert "user_first_seen" in events
    # newest first: animate_started was written last
    assert rows[0]["event"] == "animate_started"


def test_read_user_activity_respects_limit(audit_env):
    for i in range(5):
        al.audit_event(555, "p", "555", "command", {"command": f"/c{i}"})
    rows = al.read_user_activity(555, limit=3)
    assert len(rows) == 3
