# -*- coding: utf-8 -*-
"""Tests for :mod:`app.services.audit.cost_tracker`.

The state file is redirected to ``tmp_path`` via the ``JARVIS_COST_FILE`` env
var so no real ``state/cost_tracking.json`` is touched. Times are injected via
explicit ``timestamp`` / ``now`` arguments so the tests don't depend on the
wall clock.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.audit import cost_tracker


# Kyiv summer time used throughout the spec (EEST = UTC+3).
KYIV = timezone(timedelta(hours=3))


@pytest.fixture
def cost_env(tmp_path, monkeypatch):
    """Point the tracker at an isolated state file."""
    state_file = tmp_path / "cost_tracking.json"
    monkeypatch.setenv("JARVIS_COST_FILE", str(state_file))
    return state_file


def _read(state_file: Path) -> dict:
    return json.loads(state_file.read_text(encoding="utf-8"))


# ── record_cost ──────────────────────────────────────────────────────────────


def test_record_cost_creates_user_record(cost_env):
    """A single call for a new user creates the documented structure."""
    ts = datetime(2026, 5, 27, 15, 30, tzinfo=KYIV)
    cost_tracker.record_cost(237616472, "daniil", 4.20, timestamp=ts)

    state = _read(cost_env)
    user = state["users"]["237616472"]
    assert user["username"] == "daniil"
    assert user["daily"] == {"2026-05-27": 4.20}
    assert user["monthly"] == {"2026-05": 4.20}
    assert user["all_time"] == 4.20
    assert user["last_seen"] == ts.isoformat()


def test_record_cost_accumulates_daily(cost_env):
    """Two calls on the same day aggregate into one daily bucket."""
    ts = datetime(2026, 5, 27, 10, 0, tzinfo=KYIV)
    cost_tracker.record_cost(1, "u", 4.20, timestamp=ts)
    cost_tracker.record_cost(1, "u", 1.80, timestamp=ts)

    user = _read(cost_env)["users"]["1"]
    assert user["daily"]["2026-05-27"] == pytest.approx(6.00)


def test_record_cost_accumulates_monthly(cost_env):
    """Two calls in the same month aggregate into one monthly bucket."""
    cost_tracker.record_cost(1, "u", 4.00, timestamp=datetime(2026, 5, 1, tzinfo=KYIV))
    cost_tracker.record_cost(1, "u", 2.05, timestamp=datetime(2026, 5, 28, tzinfo=KYIV))

    user = _read(cost_env)["users"]["1"]
    assert user["monthly"]["2026-05"] == pytest.approx(6.05)


def test_record_cost_accumulates_all_time(cost_env):
    """all_time accumulates across different days."""
    cost_tracker.record_cost(1, "u", 4.00, timestamp=datetime(2026, 5, 1, tzinfo=KYIV))
    cost_tracker.record_cost(1, "u", 1.00, timestamp=datetime(2026, 6, 1, tzinfo=KYIV))

    user = _read(cost_env)["users"]["1"]
    assert user["all_time"] == pytest.approx(5.00)


def test_record_cost_separates_days(cost_env):
    """Calls on different dates land in separate daily entries."""
    cost_tracker.record_cost(1, "u", 4.20, timestamp=datetime(2026, 5, 27, tzinfo=KYIV))
    cost_tracker.record_cost(1, "u", 1.85, timestamp=datetime(2026, 5, 28, tzinfo=KYIV))

    user = _read(cost_env)["users"]["1"]
    assert user["daily"] == {"2026-05-27": 4.20, "2026-05-28": 1.85}


def test_record_cost_atomic_write(cost_env, monkeypatch):
    """State is written to a .tmp sibling then atomically renamed into place."""
    captured: list[tuple[str, str]] = []
    real_replace = __import__("os").replace

    def _spy_replace(src, dst):
        captured.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(cost_tracker.os, "replace", _spy_replace)
    cost_tracker.record_cost(1, "u", 1.00, timestamp=datetime(2026, 5, 27, tzinfo=KYIV))

    assert captured, "expected an os.replace (atomic rename) call"
    src, dst = captured[-1]
    assert src.endswith(".tmp")
    assert dst == str(cost_env)
    # No leftover temp file after a clean write.
    assert not Path(src).exists()


# ── get_user_stats ───────────────────────────────────────────────────────────


def test_get_user_stats_returns_zeros_for_unknown_user(cost_env):
    """An unknown user gets an all-zero snapshot, not a KeyError."""
    stats = cost_tracker.get_user_stats(999, now=datetime(2026, 5, 28, tzinfo=KYIV))
    assert stats["today"] == 0.0
    assert stats["month"] == 0.0
    assert stats["all_time"] == 0.0
    assert stats["first_seen"] is None
    assert stats["last_seen"] is None


def test_get_user_stats_returns_formatted_data(cost_env):
    """A known user's today/month/all-time and seen dates are reported."""
    cost_tracker.record_cost(
        1, "daniil", 4.20, timestamp=datetime(2026, 5, 27, 9, 0, tzinfo=KYIV)
    )
    cost_tracker.record_cost(
        1, "daniil", 1.85, timestamp=datetime(2026, 5, 28, 15, 30, tzinfo=KYIV)
    )

    stats = cost_tracker.get_user_stats(1, now=datetime(2026, 5, 28, 16, 0, tzinfo=KYIV))
    assert stats["today"] == pytest.approx(1.85)
    assert stats["month"] == pytest.approx(6.05)
    assert stats["all_time"] == pytest.approx(6.05)
    assert stats["first_seen"] == "2026-05-27"
    assert stats["last_seen"] == "2026-05-28 15:30"


# ── get_admin_overview ───────────────────────────────────────────────────────


def test_get_admin_overview_aggregates_all_users(cost_env):
    """The admin overview sums today/month/all-time across every user."""
    now = datetime(2026, 5, 28, 12, 0, tzinfo=KYIV)
    cost_tracker.record_cost(1, "daniil", 4.20, timestamp=now)
    cost_tracker.record_cost(2, "vasya", 1.80, timestamp=now)
    # Yesterday — counts toward month + all-time but not today.
    cost_tracker.record_cost(
        2, "vasya", 2.00, timestamp=datetime(2026, 5, 27, 12, 0, tzinfo=KYIV)
    )

    overview = cost_tracker.get_admin_overview(now=now)
    assert overview["active_users"] == 2
    assert overview["today_total"] == pytest.approx(6.00)
    assert overview["month_total"] == pytest.approx(8.00)
    assert overview["all_time_total"] == pytest.approx(8.00)
    ids = {u["user_id"] for u in overview["users"]}
    assert ids == {"1", "2"}


def test_get_admin_overview_with_no_users(cost_env):
    """With no recorded usage the overview is all zeros, no users."""
    overview = cost_tracker.get_admin_overview(now=datetime(2026, 5, 28, tzinfo=KYIV))
    assert overview["users"] == []
    assert overview["active_users"] == 0
    assert overview["today_total"] == 0.0
    assert overview["month_total"] == 0.0
    assert overview["all_time_total"] == 0.0


# ── message formatting ───────────────────────────────────────────────────────


def test_format_my_stats_message_with_data(cost_env):
    """The /my_stats message renders the Russian table for a real user."""
    cost_tracker.record_cost(
        1, "daniil", 4.20, timestamp=datetime(2026, 5, 27, 9, 0, tzinfo=KYIV)
    )
    cost_tracker.record_cost(
        1, "daniil", 1.85, timestamp=datetime(2026, 5, 28, 15, 30, tzinfo=KYIV)
    )

    msg = cost_tracker.format_my_stats_message(
        1, "daniil", now=datetime(2026, 5, 28, 16, 0, tzinfo=KYIV)
    )
    assert "📊 Твоя статистика" in msg
    assert "$1.85" in msg          # today
    assert "$6.05" in msg          # month / all-time
    assert "2026-05-27" in msg     # first use
    assert "2026-05-28 15:30" in msg  # last use


def test_format_my_stats_message_empty(cost_env):
    """A user with no usage gets the helpful prompt, not a zero table."""
    msg = cost_tracker.format_my_stats_message(
        999, "ghost", now=datetime(2026, 5, 28, tzinfo=KYIV)
    )
    assert "пока пусто" in msg
    assert "/swapbatch_source" in msg


def test_format_admin_costs_message(cost_env):
    """The /admin_costs message renders a per-user table plus totals."""
    now = datetime(2026, 5, 28, 12, 0, tzinfo=KYIV)
    cost_tracker.record_cost(237616472, "daniil", 4.20, timestamp=now)
    cost_tracker.record_cost(123456789, "vasya", 1.80, timestamp=now)

    msg = cost_tracker.format_admin_costs_message(now=now)
    assert "📊 Статистика по пользователям" in msg
    assert "daniil" in msg
    assert "237616472" in msg
    assert "vasya" in msg
    assert "123456789" in msg
    assert "👥 Активных users:   2" in msg
    assert "$6.00" in msg  # today total


def test_format_admin_costs_message_no_users(cost_env):
    """With no users the admin table still renders with zero totals."""
    msg = cost_tracker.format_admin_costs_message(
        now=datetime(2026, 5, 28, tzinfo=KYIV)
    )
    assert "👥 Активных users:   0" in msg


# ── concurrency ──────────────────────────────────────────────────────────────


def test_concurrent_writes_dont_corrupt_state(cost_env):
    """50 concurrent record_cost calls keep the file valid and the sum exact."""
    ts = datetime(2026, 5, 27, 10, 0, tzinfo=KYIV)

    def _worker():
        for _ in range(10):
            cost_tracker.record_cost(1, "u", 0.10, timestamp=ts)

    threads = [threading.Thread(target=_worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    state = _read(cost_env)  # must still be valid JSON
    assert state["users"]["1"]["all_time"] == pytest.approx(5.00)
