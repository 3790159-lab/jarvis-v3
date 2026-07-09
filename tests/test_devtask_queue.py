# -*- coding: utf-8 -*-
"""Dev-task queue — pure state on tmp, $0, no network, no CC."""
from datetime import datetime

import app.services.devtask.queue as q


def test_add_creates_queued(tmp_path):
    dq = q.DevTaskQueue(base_dir=tmp_path)
    tid = dq.add("fix the thing")
    item = dq.get(tid)
    assert item["status"] == q.STATUS_QUEUED and item["desc"] == "fix the thing"
    assert (tmp_path / f"{tid}.json").exists()


def test_add_generates_unique_ids_under_rapid_calls(tmp_path):
    # Windows datetime.utcnow() has ~15ms resolution -> strftime("%f") collides
    # for rapid successive adds. IDs MUST stay unique regardless of clock tick.
    dq = q.DevTaskQueue(base_dir=tmp_path)
    ids = [dq.add(f"t{i}") for i in range(25)]
    assert len(set(ids)) == 25


def test_status_transitions_and_active(tmp_path):
    dq = q.DevTaskQueue(base_dir=tmp_path)
    tid = dq.add("t")
    assert dq.active() is None                      # queued is NOT active
    dq.set_status(tid, q.STATUS_RUNNING)
    assert dq.active()["id"] == tid                 # running IS active
    dq.set_status(tid, q.STATUS_AWAITING_REVIEW)
    assert dq.active()["id"] == tid                 # awaiting_review IS active
    dq.set_status(tid, q.STATUS_MERGED)
    assert dq.active() is None                      # terminal is NOT active


def test_single_flight_active_points_at_first_running(tmp_path):
    dq = q.DevTaskQueue(base_dir=tmp_path)
    t1 = dq.add("a")
    dq.set_status(t1, q.STATUS_RUNNING)
    t2 = dq.add("b")                                # second may be enqueued
    assert dq.get(t2)["status"] == q.STATUS_QUEUED
    assert dq.active()["id"] == t1                  # but only one is active


def test_set_status_extra_fields_and_log(tmp_path):
    dq = q.DevTaskQueue(base_dir=tmp_path)
    tid = dq.add("t")
    dq.set_status(tid, q.STATUS_FAILED, error="timeout")
    assert dq.get(tid)["error"] == "timeout"
    assert (tmp_path / "log.jsonl").exists()


def test_claim_queued_wins(tmp_path):
    dq = q.DevTaskQueue(base_dir=tmp_path)
    tid = dq.add("t")
    assert dq.claim(tid) is True                     # queued -> caller wins
    assert dq.get(tid)["status"] == q.STATUS_RUNNING  # ...and it is now running


def test_claim_double_returns_false(tmp_path):
    # A double-delivered callback must not start two worktrees: the second claim
    # loses and leaves state untouched.
    dq = q.DevTaskQueue(base_dir=tmp_path)
    tid = dq.add("t")
    assert dq.claim(tid) is True
    assert dq.claim(tid) is False                    # already claimed -> lose
    assert dq.get(tid)["status"] == q.STATUS_RUNNING  # unchanged


def test_claim_non_queued_returns_false(tmp_path):
    dq = q.DevTaskQueue(base_dir=tmp_path)
    tid = dq.add("t")
    dq.set_status(tid, q.STATUS_AWAITING_REVIEW)     # not queued anymore
    assert dq.claim(tid) is False
    assert dq.claim("no-such-task") is False         # missing -> lose


def test_claim_only_one_winner(tmp_path):
    # Two racing handlers on the same fresh task: exactly one may win.
    dq = q.DevTaskQueue(base_dir=tmp_path)
    tid = dq.add("t")
    results = [dq.claim(tid), dq.claim(tid)]
    assert results.count(True) == 1


# ── month_cost (Фаза 8.2): sum of `cost` for cards created this month ────────
def _seed_cost(dq, created_at, cost, status=q.STATUS_MERGED):
    tid = dq.add("t")
    dq.set_status(tid, status, created_at=created_at, cost=cost)
    return tid


def test_month_cost_sums_current_month(tmp_path):
    dq = q.DevTaskQueue(base_dir=tmp_path)
    now = datetime(2026, 7, 15, 12, 0, 0)
    _seed_cost(dq, "2026-07-01T00:00:00", 1.5)
    _seed_cost(dq, "2026-07-31T23:59:59", 2.25)
    assert dq.month_cost(now) == 3.75


def test_month_cost_excludes_other_months(tmp_path):
    dq = q.DevTaskQueue(base_dir=tmp_path)
    now = datetime(2026, 7, 15)
    _seed_cost(dq, "2026-06-30T23:59:59", 10.0)     # previous month
    _seed_cost(dq, "2026-08-01T00:00:00", 5.0)      # next month
    _seed_cost(dq, "2026-07-10T00:00:00", 2.0)      # this month
    assert dq.month_cost(now) == 2.0


def test_month_cost_excludes_same_month_other_year(tmp_path):
    dq = q.DevTaskQueue(base_dir=tmp_path)
    now = datetime(2026, 7, 15)
    _seed_cost(dq, "2025-07-10T00:00:00", 9.0)      # July but wrong year
    assert dq.month_cost(now) == 0.0


def test_month_cost_none_cost_counts_zero(tmp_path):
    dq = q.DevTaskQueue(base_dir=tmp_path)
    now = datetime(2026, 7, 15)
    _seed_cost(dq, "2026-07-05T00:00:00", None, status=q.STATUS_FAILED)
    _seed_cost(dq, "2026-07-06T00:00:00", 3.0)
    assert dq.month_cost(now) == 3.0


def test_month_cost_empty_is_zero(tmp_path):
    dq = q.DevTaskQueue(base_dir=tmp_path)
    assert dq.month_cost(datetime(2026, 7, 15)) == 0.0


def test_month_cost_ignores_unparseable_created_at(tmp_path):
    dq = q.DevTaskQueue(base_dir=tmp_path)
    now = datetime(2026, 7, 15)
    _seed_cost(dq, "not-a-date", 4.0)               # must not crash
    _seed_cost(dq, "2026-07-08T00:00:00", 1.0)
    assert dq.month_cost(now) == 1.0


# ── due_reminders (queued-confirmation nag): queued cards left un-tapped ─────
def _seed_queued(dq, created_at, reminded=None):
    tid = dq.add("t")
    fields = {"created_at": created_at}
    if reminded is not None:
        fields["reminded"] = reminded
    dq.set_status(tid, q.STATUS_QUEUED, **fields)     # stays queued, older created_at
    return tid


def test_due_reminders_returns_stale_queued(tmp_path):
    dq = q.DevTaskQueue(base_dir=tmp_path)
    now = datetime(2026, 7, 15, 12, 0, 0)
    tid = _seed_queued(dq, "2026-07-15T11:49:00")     # 11 min old, > 10
    due = dq.due_reminders(now, remind_after_min=10)
    assert [d["id"] for d in due] == [tid]


def test_due_reminders_excludes_fresh_queued(tmp_path):
    dq = q.DevTaskQueue(base_dir=tmp_path)
    now = datetime(2026, 7, 15, 12, 0, 0)
    _seed_queued(dq, "2026-07-15T11:55:00")           # 5 min old, < 10
    assert dq.due_reminders(now, remind_after_min=10) == []


def test_due_reminders_excludes_already_reminded(tmp_path):
    dq = q.DevTaskQueue(base_dir=tmp_path)
    now = datetime(2026, 7, 15, 12, 0, 0)
    _seed_queued(dq, "2026-07-15T11:40:00", reminded=True)   # stale but flagged
    assert dq.due_reminders(now, remind_after_min=10) == []


def test_due_reminders_excludes_non_queued(tmp_path):
    dq = q.DevTaskQueue(base_dir=tmp_path)
    now = datetime(2026, 7, 15, 12, 0, 0)
    tid = dq.add("t")
    dq.set_status(tid, q.STATUS_RUNNING, created_at="2026-07-15T11:00:00")
    assert dq.due_reminders(now, remind_after_min=10) == []   # confirmed already


def test_due_reminders_ignores_unparseable_created_at(tmp_path):
    dq = q.DevTaskQueue(base_dir=tmp_path)
    now = datetime(2026, 7, 15, 12, 0, 0)
    _seed_queued(dq, "not-a-date")                    # must not crash
    good = _seed_queued(dq, "2026-07-15T11:40:00")
    assert [d["id"] for d in dq.due_reminders(now, remind_after_min=10)] == [good]


def test_mark_reminded_sets_flag_without_changing_status(tmp_path):
    dq = q.DevTaskQueue(base_dir=tmp_path)
    tid = dq.add("t")
    dq.mark_reminded(tid)
    item = dq.get(tid)
    assert item["reminded"] is True
    assert item["status"] == q.STATUS_QUEUED          # status untouched
