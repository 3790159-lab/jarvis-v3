# -*- coding: utf-8 -*-
"""Batched full-regress runner with a RAM-guard (Master-Plan Этап 1, хвост #4).

The full ``pytest tests/`` suite in one process OOMs / swaps on 16 GB (live
incident 2026-07-09). This module runs it as deterministic sequential batches,
checks free RAM before each batch (pausing/retrying, then failing HONESTLY
rather than starting a batch into starvation), aggregates the per-batch pytest
summaries into a single verdict, refreshes the baseline after the first clean
full run, and drives every batch through the existing detached watchdog.

Everything is pure / injected — no real pytest, no real psutil, no filesystem
beyond tmp_path. $0, mocks only.
"""
import json

import pytest

import app.services.devtask.regress_batches as rb


# ── batch planning: deterministic order + configurable size ─────────────────
def test_plan_batches_sorts_and_chunks():
    files = ["tests/test_c.py", "tests/test_a.py", "tests/test_b.py", "tests/test_d.py"]
    batches = rb.plan_batches(files, 2)
    assert batches == [["tests/test_a.py", "tests/test_b.py"],
                       ["tests/test_c.py", "tests/test_d.py"]]


def test_plan_batches_last_batch_partial():
    files = [f"tests/test_{i}.py" for i in range(5)]
    batches = rb.plan_batches(files, 2)
    assert [len(b) for b in batches] == [2, 2, 1]


def test_plan_batches_dedups_and_normalizes_separators():
    files = ["tests\\test_a.py", "tests/test_a.py", "tests/test_b.py"]
    batches = rb.plan_batches(files, 10)
    assert batches == [["tests/test_a.py", "tests/test_b.py"]]


def test_plan_batches_nonpositive_size_is_single_batch():
    files = ["tests/test_b.py", "tests/test_a.py"]
    assert rb.plan_batches(files, 0) == [["tests/test_a.py", "tests/test_b.py"]]
    assert rb.plan_batches(files, -3) == [["tests/test_a.py", "tests/test_b.py"]]


def test_plan_batches_empty():
    assert rb.plan_batches([], 5) == []
    assert rb.plan_batches([""], 5) == []


# ── aggregation of per-batch summaries ──────────────────────────────────────
def test_aggregate_summaries_sums_fields():
    got = rb.aggregate_summaries([
        {"failed": 1, "passed": 10, "errors": 0},
        {"failed": 2, "passed": 5, "errors": 1},
    ])
    assert got == {"failed": 3, "passed": 15, "errors": 1}


def test_aggregate_summaries_empty_is_zero():
    assert rb.aggregate_summaries([]) == {"failed": 0, "passed": 0, "errors": 0}


def test_aggregate_summaries_tolerates_missing_keys():
    assert rb.aggregate_summaries([{"passed": 3}]) == {"failed": 0, "passed": 3, "errors": 0}


# ── RAM gate: pure decision + retry loop ────────────────────────────────────
def test_ram_ok_boundary():
    assert rb.ram_ok(3.0, 3.0) is True     # at threshold is OK
    assert rb.ram_ok(2.9, 3.0) is False
    assert rb.ram_ok(10.0, 3.0) is True


def test_wait_for_ram_ok_immediately_no_sleep():
    slept = []
    ok, free = rb.wait_for_ram(3.0, free_gb_fn=lambda: 8.0,
                               sleep_fn=lambda s: slept.append(s),
                               retries=5, pause_s=10)
    assert ok is True and free == 8.0
    assert slept == []                      # never paused when RAM already fine


def test_wait_for_ram_recovers_after_retries():
    seq = iter([1.0, 1.5, 4.0])             # low, low, then recovered
    slept = []
    ok, free = rb.wait_for_ram(3.0, free_gb_fn=lambda: next(seq),
                               sleep_fn=lambda s: slept.append(s),
                               retries=5, pause_s=7)
    assert ok is True and free == 4.0
    assert slept == [7, 7]                   # paused twice before recovery


def test_wait_for_ram_never_recovers_fails_after_n():
    slept = []
    ok, free = rb.wait_for_ram(3.0, free_gb_fn=lambda: 1.0,
                               sleep_fn=lambda s: slept.append(s),
                               retries=3, pause_s=5)
    assert ok is False and free == 1.0
    assert slept == [5, 5, 5]                # exactly N retries, then honest fail


# ── free_gb probe: psutil injected ──────────────────────────────────────────
def test_free_gb_from_injected_vm():
    class _VM:
        available = 4 * (1024 ** 3)
    assert rb.free_gb(vm_fn=lambda: _VM()) == pytest.approx(4.0)


def test_free_gb_fail_open_on_error():
    def _boom():
        raise RuntimeError("no psutil")
    assert rb.free_gb(vm_fn=_boom) == float("inf")   # can't measure → don't block


# ── orchestrator: happy path runs every batch, aggregates, labels ───────────
def test_run_batched_regress_complete_aggregates_and_labels():
    files = [f"tests/test_{i}.py" for i in range(4)]
    calls = []

    def _run_batch(batch, label):
        calls.append((tuple(batch), label))
        return {"failed": 0, "passed": len(batch), "errors": 0}

    res = rb.run_batched_regress(
        files, batch_size=2, min_free_gb=3.0,
        run_batch_fn=_run_batch, free_gb_fn=lambda: 8.0, sleep_fn=lambda s: None,
        ram_retries=3, ram_pause_s=1)

    assert res["status"] == "complete"
    assert res["summary"] == {"failed": 0, "passed": 4, "errors": 0}
    assert res["batches_run"] == 2 and res["batches_total"] == 2
    # deterministic per-batch labels carry index/total for the watchdog marker
    assert [c[1] for c in calls] == ["regress-batch-1/2", "regress-batch-2/2"]


def test_run_batched_regress_ram_exhausted_stops_without_starving():
    files = [f"tests/test_{i}.py" for i in range(4)]
    ran = []

    def _run_batch(batch, label):
        ran.append(label)
        return {"failed": 0, "passed": len(batch), "errors": 0}

    # first batch fine; RAM collapses and never recovers before batch 2
    free_seq = iter([8.0] + [1.0] * 20)
    res = rb.run_batched_regress(
        files, batch_size=2, min_free_gb=3.0,
        run_batch_fn=_run_batch, free_gb_fn=lambda: next(free_seq),
        sleep_fn=lambda s: None, ram_retries=2, ram_pause_s=1)

    assert res["status"] == "ram_exhausted"
    assert ran == ["regress-batch-1/2"]      # 2nd batch NEVER started into starvation
    assert res["batches_run"] == 1 and res["batches_total"] == 2
    assert res["free_gb"] == 1.0 and res["min_free_gb"] == 3.0


def test_run_batched_regress_batch_timeout_reported():
    files = [f"tests/test_{i}.py" for i in range(4)]

    def _run_batch(batch, label):
        return None if "2/2" in label else {"failed": 0, "passed": 2, "errors": 0}

    res = rb.run_batched_regress(
        files, batch_size=2, min_free_gb=3.0,
        run_batch_fn=_run_batch, free_gb_fn=lambda: 8.0, sleep_fn=lambda s: None,
        ram_retries=1, ram_pause_s=1)

    assert res["status"] == "timeout"
    assert res["batches_run"] == 1 and res["batches_total"] == 2


# ── baseline refresh after the first clean full run ─────────────────────────
def test_should_update_baseline_first_success_establishes():
    assert rb.should_update_baseline("complete", {"failed": 0}, None) is True


def test_should_update_baseline_refreshes_when_not_worse():
    assert rb.should_update_baseline("complete", {"failed": 1}, {"failed": 2}) is True
    assert rb.should_update_baseline("complete", {"failed": 2}, {"failed": 2}) is True


def test_should_update_baseline_keeps_old_when_worse():
    assert rb.should_update_baseline("complete", {"failed": 5}, {"failed": 2}) is False


def test_should_update_baseline_never_on_incomplete_run():
    assert rb.should_update_baseline("ram_exhausted", {"failed": 0}, None) is False
    assert rb.should_update_baseline("timeout", {"failed": 0}, None) is False


def test_write_baseline_roundtrip(tmp_path):
    p = tmp_path / "regress_baseline.json"
    rb.write_baseline(p, {"failed": 1, "passed": 20, "errors": 2, "extra": "x"})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data == {"failed": 1, "passed": 20, "errors": 2}   # only the counts persisted


# ── env-configurable knobs ──────────────────────────────────────────────────
def test_batch_size_from_env_default_and_override():
    assert rb.batch_size_from_env({}) == rb.DEFAULT_BATCH_SIZE
    assert rb.batch_size_from_env({"REGRESS_BATCH_SIZE": "12"}) == 12
    assert rb.batch_size_from_env({"REGRESS_BATCH_SIZE": "bad"}) == rb.DEFAULT_BATCH_SIZE
    assert rb.batch_size_from_env({"REGRESS_BATCH_SIZE": "0"}) == rb.DEFAULT_BATCH_SIZE


def test_min_free_gb_from_env_default_and_override():
    assert rb.min_free_gb_from_env({}) == 3.0
    assert rb.min_free_gb_from_env({"REGRESS_MIN_FREE_GB": "5.5"}) == 5.5
    assert rb.min_free_gb_from_env({"REGRESS_MIN_FREE_GB": "bad"}) == 3.0


def test_ram_retry_knobs_from_env():
    assert rb.ram_retries_from_env({}) == rb.DEFAULT_RAM_RETRIES
    assert rb.ram_retries_from_env({"REGRESS_RAM_RETRIES": "9"}) == 9
    assert rb.ram_pause_s_from_env({}) == rb.DEFAULT_RAM_PAUSE_S
    assert rb.ram_pause_s_from_env({"REGRESS_RAM_PAUSE_S": "20"}) == 20


# ── result → human verdict (verdict_fn injected to stay decoupled) ───────────
def _fake_verdict(summary, baseline):
    f = summary.get("failed", 0)
    b = (baseline or {}).get("failed", 0)
    return f"✅ ok ({f}≤{b})" if f <= b else f"⚠️ регресс +{f - b}"


def test_summarize_result_complete_green():
    res = {"status": "complete", "summary": {"failed": 0, "passed": 9, "errors": 0},
           "batches_run": 3, "batches_total": 3}
    out = rb.summarize_result(res, {"failed": 0}, verdict_fn=_fake_verdict)
    assert out["ok"] is True and "3/3" in out["text"]


def test_summarize_result_ram_exhausted_is_honest_failure():
    res = {"status": "ram_exhausted", "summary": {"failed": 0, "passed": 2, "errors": 0},
           "batches_run": 1, "batches_total": 4, "free_gb": 1.2, "min_free_gb": 3.0,
           "ram_retries": 6}
    out = rb.summarize_result(res, None, verdict_fn=_fake_verdict)
    assert out["ok"] is False
    assert "RAM" in out["text"] and "1.2" in out["text"] and "1/4" in out["text"]


def test_summarize_result_timeout_is_failure():
    res = {"status": "timeout", "summary": {"failed": 0, "passed": 2, "errors": 0},
           "batches_run": 2, "batches_total": 5}
    out = rb.summarize_result(res, None, verdict_fn=_fake_verdict)
    assert out["ok"] is False and "аймаут" in out["text"] and "2/5" in out["text"]


# ── intra-batch RAM kill: mark failed, name files, CONTINUE the rest ─────────
def test_run_batched_regress_ram_killed_batch_continues_and_flags():
    files = [f"tests/test_{i}.py" for i in range(6)]   # 3 batches of 2

    ran = []

    def _run_batch(batch, label):
        ran.append(label)
        if "2/3" in label:                       # 2nd batch balloons → killed on the fly
            return {"ram_killed": True, "free_gb": 1.1}
        return {"failed": 0, "passed": len(batch), "errors": 0}

    res = rb.run_batched_regress(
        files, batch_size=2, min_free_gb=3.0,
        run_batch_fn=_run_batch, free_gb_fn=lambda: 8.0, sleep_fn=lambda s: None,
        ram_retries=1, ram_pause_s=1)

    assert res["status"] == "ram_killed"
    # the kill did NOT abort the run — every batch was still attempted
    assert ran == ["regress-batch-1/3", "regress-batch-2/3", "regress-batch-3/3"]
    assert len(res["ram_killed"]) == 1
    k = res["ram_killed"][0]
    assert k["idx"] == 2 and k["free_gb"] == 1.1
    assert k["files"] == ["tests/test_2.py", "tests/test_3.py"]     # names the victims
    # aggregate covers only the batches that produced a real summary (1 & 3 → 4 passed)
    assert res["summary"] == {"failed": 0, "passed": 4, "errors": 0}
    assert res["batches_total"] == 3


def test_should_update_baseline_never_on_ram_killed():
    assert rb.should_update_baseline("ram_killed", {"failed": 0}, None) is False


def test_summarize_result_ram_killed_is_honest_failure_naming_files():
    res = {"status": "ram_killed",
           "summary": {"failed": 0, "passed": 4, "errors": 0},
           "batches_run": 2, "batches_total": 3,
           "ram_killed": [{"idx": 2, "free_gb": 1.1,
                           "files": ["tests/test_2.py", "tests/test_3.py"]}]}
    out = rb.summarize_result(res, None, verdict_fn=_fake_verdict)
    assert out["ok"] is False
    assert "test_2.py" in out["text"] and "1.1" in out["text"]


def test_batch_ram_guard_knobs_from_env():
    assert rb.batch_kill_free_gb_from_env({}) == rb.DEFAULT_BATCH_KILL_FREE_GB
    assert rb.batch_kill_free_gb_from_env({"REGRESS_BATCH_KILL_FREE_GB": "1.5"}) == 1.5
    assert rb.batch_kill_free_gb_from_env(
        {"REGRESS_BATCH_KILL_FREE_GB": "bad"}) == rb.DEFAULT_BATCH_KILL_FREE_GB
    assert rb.batch_sample_s_from_env({}) == rb.DEFAULT_BATCH_SAMPLE_S
    assert rb.batch_sample_s_from_env({"REGRESS_BATCH_SAMPLE_S": "3"}) == 3
