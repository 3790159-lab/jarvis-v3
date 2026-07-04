# -*- coding: utf-8 -*-
"""Dev-task queue — pure state on tmp, $0, no network, no CC."""
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
