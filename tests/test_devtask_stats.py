# -*- coding: utf-8 -*-
"""Tests for :mod:`app.services.devtask.stats` — pure aggregation + formatting.

No I/O, no CC, no money: inputs are plain dicts/lists constructed in-test,
mirroring what ``DevTaskQueue.list_all()`` / ``DevTaskQueue.log_entries()``
would hand the caller (see tests/test_devtask_queue.py for the real shapes).
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.services.devtask.stats import build_summary, format_stats_message
from app.services.devtask.queue import (
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_AWAITING_REVIEW,
    STATUS_MERGED,
    STATUS_ROLLED_BACK,
    STATUS_FAILED,
)

NOW = datetime(2026, 7, 13, 12, 0, 0)


def _card(tid, status, created_at, cost=None, error=None, desc="t"):
    return {
        "id": tid, "desc": desc, "status": status,
        "created_at": created_at, "cost": cost, "error": error,
    }


def _log(tid, action, ts):
    return {"id": tid, "action": action, "ts": ts}


# ── counts ───────────────────────────────────────────────────────────────────


def test_counts_merged_rolled_back_no_report():
    cards = [
        _card("1", STATUS_MERGED, "2026-07-05T00:00:00"),
        _card("2", STATUS_ROLLED_BACK, "2026-07-06T00:00:00"),
        _card("3", STATUS_FAILED, "2026-07-07T00:00:00", error="no_report"),
        _card("4", STATUS_FAILED, "2026-07-08T00:00:00", error="cc_error: boom"),
        _card("5", STATUS_QUEUED, "2026-07-09T00:00:00"),
    ]
    summary = build_summary(cards, [], days=30, now=NOW)
    assert summary["total"] == 5
    assert summary["merged"] == 1
    assert summary["rolled_back"] == 1
    assert summary["no_report"] == 1
    assert summary["other_failed"] == 1
    assert summary["in_flight"] == 1


def test_out_of_window_cards_excluded():
    cards = [
        _card("old", STATUS_MERGED, "2026-05-01T00:00:00"),
        _card("new", STATUS_MERGED, "2026-07-10T00:00:00"),
    ]
    summary = build_summary(cards, [], days=30, now=NOW)
    assert summary["total"] == 1
    assert summary["merged"] == 1


def test_unparseable_created_at_excluded():
    cards = [_card("bad", STATUS_MERGED, "not-a-date")]
    summary = build_summary(cards, [], days=30, now=NOW)
    assert summary["total"] == 0


def test_missing_created_at_excluded():
    card = {"id": "x", "desc": "t", "status": STATUS_MERGED, "cost": None, "error": None}
    summary = build_summary([card], [], days=30, now=NOW)
    assert summary["total"] == 0


def test_days_clamped_to_at_least_one():
    cards = [_card("1", STATUS_MERGED, "2026-07-13T00:00:00")]
    summary = build_summary(cards, [], days=0, now=NOW)
    assert summary["days"] == 1
    assert summary["total"] == 1


# ── cost aggregation ─────────────────────────────────────────────────────────


def test_total_and_average_cost_ignores_none():
    cards = [
        _card("1", STATUS_MERGED, "2026-07-10T00:00:00", cost=1.5),
        _card("2", STATUS_MERGED, "2026-07-11T00:00:00", cost=2.5),
        _card("3", STATUS_FAILED, "2026-07-11T00:00:00", cost=None, error="no_report"),
    ]
    summary = build_summary(cards, [], days=30, now=NOW)
    assert summary["total_cost_usd"] == pytest.approx(4.0)
    assert summary["avg_cost_usd"] == pytest.approx(2.0)


def test_no_costed_tasks_gives_zero_averages():
    cards = [_card("1", STATUS_QUEUED, "2026-07-10T00:00:00")]
    summary = build_summary(cards, [], days=30, now=NOW)
    assert summary["total_cost_usd"] == 0.0
    assert summary["avg_cost_usd"] == 0.0


# ── duration aggregation (from log entries, terminal tasks only) ────────────


def test_average_duration_from_log_entries():
    cards = [
        _card("1", STATUS_MERGED, "2026-07-10T00:00:00"),
        _card("2", STATUS_MERGED, "2026-07-11T00:00:00"),
    ]
    log_entries = [
        _log("1", "added", "2026-07-10T00:00:00"),
        _log("1", "merged", "2026-07-10T00:10:00"),   # 600s
        _log("2", "added", "2026-07-11T00:00:00"),
        _log("2", "merged", "2026-07-11T00:20:00"),   # 1200s
    ]
    summary = build_summary(cards, log_entries, days=30, now=NOW)
    assert summary["avg_duration_s"] == pytest.approx(900.0)


def test_duration_excludes_non_terminal_tasks():
    cards = [_card("1", STATUS_RUNNING, "2026-07-10T00:00:00")]
    log_entries = [
        _log("1", "added", "2026-07-10T00:00:00"),
        _log("1", "running", "2026-07-10T00:05:00"),
    ]
    summary = build_summary(cards, log_entries, days=30, now=NOW)
    assert summary["avg_duration_s"] == 0.0


def test_duration_missing_log_entries_gives_zero():
    cards = [_card("1", STATUS_MERGED, "2026-07-10T00:00:00")]
    summary = build_summary(cards, [], days=30, now=NOW)
    assert summary["avg_duration_s"] == 0.0


# ── top-3 most expensive ─────────────────────────────────────────────────────


def test_top_costly_sorted_desc_limited_to_three():
    cards = [
        _card("1", STATUS_MERGED, "2026-07-10T00:00:00", cost=0.5, desc="cheap"),
        _card("2", STATUS_MERGED, "2026-07-10T00:00:00", cost=5.0, desc="priciest"),
        _card("3", STATUS_FAILED, "2026-07-10T00:00:00", cost=2.0, desc="mid", error="no_report"),
        _card("4", STATUS_MERGED, "2026-07-10T00:00:00", cost=3.0, desc="second"),
    ]
    summary = build_summary(cards, [], days=30, now=NOW)
    assert [t["desc"] for t in summary["top_costly"]] == ["priciest", "second", "mid"]
    assert summary["top_costly"][0]["cost_usd"] == pytest.approx(5.0)


def test_top_costly_empty_when_no_costs():
    cards = [_card("1", STATUS_QUEUED, "2026-07-10T00:00:00")]
    summary = build_summary(cards, [], days=30, now=NOW)
    assert summary["top_costly"] == []


# ── format_stats_message ─────────────────────────────────────────────────────


def test_format_message_contains_key_numbers():
    cards = [
        _card("1", STATUS_MERGED, "2026-07-10T00:00:00", cost=1.0, desc="fix bug"),
        _card("2", STATUS_ROLLED_BACK, "2026-07-11T00:00:00"),
        _card("3", STATUS_FAILED, "2026-07-11T00:00:00", error="no_report"),
    ]
    summary = build_summary(cards, [], days=30, now=NOW)
    text = format_stats_message(summary)
    assert "30" in text
    assert "Всего задач: 3" in text
    assert "Смерджено: 1" in text
    assert "Откат: 1" in text
    assert "No report: 1" in text
    assert "$1.00" in text
    assert "fix bug" in text


def test_format_message_no_cost_data_says_so():
    cards = [_card("1", STATUS_QUEUED, "2026-07-10T00:00:00")]
    summary = build_summary(cards, [], days=30, now=NOW)
    text = format_stats_message(summary)
    assert "нет данных" in text
