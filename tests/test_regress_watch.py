# -*- coding: utf-8 -*-
"""Detached regress watchdog (Master-Plan Этап 1, хвост #6).

Core marker + pure decisions + the guarded runner and the in-process sweep.
Processes are mocked, wall-clock is injected — no real pytest, no real kills.
"""
import json
import subprocess
import threading

import pytest

import app.services.devtask.regress_watch as rw


# ── marker roundtrip ────────────────────────────────────────────────────────
def test_record_read_clear_roundtrip(tmp_path):
    rw.record(tmp_path, pid=4242, bot_pid=99, deadline_epoch=1180,
              label="regress", started_epoch=1000)
    data = json.loads((tmp_path / "regress_watch.json").read_text(encoding="utf-8"))
    assert data["pid"] == 4242 and data["bot_pid"] == 99
    assert data["deadline_epoch"] == 1180 and data["started_epoch"] == 1000
    assert data["label"] == "regress"

    got = rw.read(tmp_path)
    assert got["pid"] == 4242

    rw.clear(tmp_path)
    assert rw.read(tmp_path) is None
    assert not (tmp_path / "regress_watch.json").exists()


def test_read_missing_or_corrupt_returns_none(tmp_path):
    assert rw.read(tmp_path) is None
    (tmp_path / "regress_watch.json").write_text("{not json", encoding="utf-8")
    assert rw.read(tmp_path) is None


# ── pure decisions ──────────────────────────────────────────────────────────
def test_is_expired():
    assert rw.is_expired({"deadline_epoch": 100}, now=101) is True
    assert rw.is_expired({"deadline_epoch": 100}, now=100) is False
    assert rw.is_expired({"deadline_epoch": 100}, now=99) is False


def test_is_orphaned():
    assert rw.is_orphaned({"bot_pid": 5}, parent_alive=False) is True
    assert rw.is_orphaned({"bot_pid": 5}, parent_alive=True) is False


def test_runaway_reason_orphan_takes_precedence():
    w = {"deadline_epoch": 100}
    assert rw.runaway_reason(w, now=200, parent_alive=False) == "orphan"   # dead parent
    assert rw.runaway_reason(w, now=200, parent_alive=True) == "deadline"  # overdue only
    assert rw.runaway_reason(w, now=50, parent_alive=True) is None         # healthy run


# ── in-process sweep (bot periodic loop) ────────────────────────────────────
def test_sweep_noop_when_no_marker(tmp_path):
    kills = []
    out = rw.sweep(tmp_path, now=999, parent_alive_fn=lambda p: True,
                   kill_fn=lambda p: kills.append(p))
    assert out is None and kills == []


def test_sweep_noop_when_healthy(tmp_path):
    rw.record(tmp_path, pid=7, bot_pid=1, deadline_epoch=1000, started_epoch=1)
    kills = []
    out = rw.sweep(tmp_path, now=500, parent_alive_fn=lambda p: True,
                   kill_fn=lambda p: kills.append(p))
    assert out is None and kills == []
    assert rw.read(tmp_path) is not None  # left running, untouched


def test_sweep_kills_on_expired_deadline(tmp_path):
    rw.record(tmp_path, pid=7, bot_pid=1, deadline_epoch=1000, label="regress", started_epoch=1)
    kills, notes, logs = [], [], []
    out = rw.sweep(tmp_path, now=2000,
                   parent_alive_fn=lambda p: True,
                   kill_fn=lambda p: kills.append(p) or True,
                   notify_fn=notes.append, log_fn=logs.append)
    assert kills == [7]
    assert out["reason"] == "deadline" and out["pid"] == 7
    assert rw.read(tmp_path) is None          # cleared after kill
    assert notes and "убит" in notes[0]        # admin notified
    assert logs                                 # logged


def test_sweep_kills_orphan_when_parent_dead(tmp_path):
    rw.record(tmp_path, pid=8, bot_pid=1, deadline_epoch=99999, started_epoch=1)
    kills, notes = [], []
    out = rw.sweep(tmp_path, now=500,          # not expired yet…
                   parent_alive_fn=lambda p: False,  # …but parent bot is dead
                   kill_fn=lambda p: kills.append(p) or True,
                   notify_fn=notes.append)
    assert kills == [8] and out["reason"] == "orphan"
    assert rw.read(tmp_path) is None
    assert notes


def test_sweep_checks_the_recorded_bot_pid(tmp_path):
    rw.record(tmp_path, pid=8, bot_pid=4321, deadline_epoch=99999, started_epoch=1)
    seen = []
    rw.sweep(tmp_path, now=1, parent_alive_fn=lambda p: (seen.append(p) or True),
             kill_fn=lambda p: True)
    assert seen == [4321]


# ── guarded runner (spawn → record → wait → clear) ──────────────────────────
class _FakePopen:
    def __init__(self, *, pid=4242, out="1 passed in 1.0s", rc=0, timeout=False, observer=None):
        self.pid = pid
        self._out = out
        self.returncode = rc
        self._timeout = timeout
        self._observer = observer
        self.kills = 0
        self._calls = 0

    def communicate(self, timeout=None):
        self._calls += 1
        if self._observer:
            self._observer()  # inspect marker mid-run
        if self._timeout and self._calls == 1:
            raise subprocess.TimeoutExpired(cmd="pytest", timeout=timeout)
        return (self._out, "")


def test_run_guarded_records_during_and_clears_after(tmp_path):
    seen = {}

    def _observe():
        seen["marker"] = rw.read(tmp_path)

    fake = _FakePopen(pid=555, observer=_observe)
    result = rw.run_guarded(
        ["python", "-m", "pytest"], cwd="C:/jarvis", env={"X": "1"},
        timeout_s=900, creationflags=0, state_dir=tmp_path, label="regress",
        bot_pid=77, now_fn=lambda: 1000, popen_factory=lambda *a, **k: fake,
        kill_fn=lambda p: True)

    assert result.returncode == 0 and "passed" in result.stdout
    # marker existed DURING the run with the right pid + deadline
    assert seen["marker"]["pid"] == 555
    assert seen["marker"]["bot_pid"] == 77
    assert seen["marker"]["deadline_epoch"] == 1900   # 1000 + 900
    # …and is gone AFTER
    assert rw.read(tmp_path) is None


def test_run_guarded_kills_group_and_clears_on_timeout(tmp_path):
    killed = []
    fake = _FakePopen(pid=666, timeout=True)
    with pytest.raises(subprocess.TimeoutExpired):
        rw.run_guarded(
            ["python", "-m", "pytest"], cwd="C:/jarvis", env=None,
            timeout_s=1, creationflags=0, state_dir=tmp_path, label="regress",
            bot_pid=77, now_fn=lambda: 1000, popen_factory=lambda *a, **k: fake,
            kill_fn=lambda p: killed.append(p) or True)
    assert killed == [666]               # whole group killed on timeout
    assert rw.read(tmp_path) is None      # marker cleared even on timeout


def test_run_guarded_passes_through_popen_kwargs(tmp_path):
    captured = {}

    def _factory(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakePopen(pid=1)

    rw.run_guarded(["python", "-m", "pytest", "tests/"], cwd="C:/wt", env={"E": "2"},
                   timeout_s=900, creationflags=0x4000, state_dir=tmp_path,
                   now_fn=lambda: 0, popen_factory=_factory, kill_fn=lambda p: True)
    assert captured["cmd"] == ["python", "-m", "pytest", "tests/"]
    assert captured["kwargs"]["cwd"] == "C:/wt"
    assert captured["kwargs"]["env"] == {"E": "2"}
    assert captured["kwargs"]["creationflags"] == 0x4000
    assert captured["kwargs"]["stdout"] is subprocess.PIPE
    assert captured["kwargs"]["stderr"] is subprocess.PIPE


# ── intra-batch RAM guard (Этап 1, хвост #4 добор: kill a ballooning batch) ──
def test_ram_breach_below_floor_only():
    assert rw.ram_breach(2.0, 2.5) is True
    assert rw.ram_breach(2.5, 2.5) is False   # boundary is NOT a breach
    assert rw.ram_breach(3.0, 2.5) is False


class _RamFakePopen:
    """communicate() blocks until the RAM guard kills us (event set by kill_fn),
    or returns healthy after a short safety wait when never killed."""

    def __init__(self, pid):
        self.pid = pid
        self.returncode = None
        self._dead = threading.Event()

    def communicate(self, timeout=None):
        fired = self._dead.wait(2.0)
        self.returncode = -9 if fired else 0
        return ("killed" if fired else "1 passed in 1.0s", "")


def test_run_guarded_ram_kill_raises_and_clears(tmp_path):
    fake = _RamFakePopen(pid=999)
    killed = []

    def _kill(pid):
        killed.append(pid)
        fake._dead.set()          # unblock communicate → the "process" dies
        return True

    with pytest.raises(rw.RegressRamKilled) as ei:
        rw.run_guarded(
            ["python", "-m", "pytest"], cwd="C:/j", env=None, timeout_s=900,
            creationflags=0, state_dir=tmp_path, bot_pid=7, now_fn=lambda: 0,
            popen_factory=lambda *a, **k: fake, kill_fn=_kill,
            free_gb_fn=lambda: 1.0, kill_free_gb=2.5, sample_s=0.01)

    assert ei.value.pid == 999 and ei.value.free_gb == 1.0
    assert killed == [999]                 # the whole group was tree-killed
    assert rw.read(tmp_path) is None        # marker cleared even on a RAM kill


def test_run_guarded_ram_guard_healthy_completes_normally(tmp_path):
    fake = _FakePopen(pid=222, out="3 passed in 1.0s")
    res = rw.run_guarded(
        ["python", "-m", "pytest"], cwd="C:/j", env=None, timeout_s=900,
        creationflags=0, state_dir=tmp_path, bot_pid=7, now_fn=lambda: 0,
        popen_factory=lambda *a, **k: fake, kill_fn=lambda p: True,
        free_gb_fn=lambda: 8.0, kill_free_gb=2.5, sample_s=0.01)
    assert res.returncode == 0 and "passed" in res.stdout
    assert rw.read(tmp_path) is None


def test_run_guarded_without_ram_knobs_is_unchanged(tmp_path):
    # no free_gb_fn/kill_free_gb → no sampler thread, classic behaviour
    fake = _FakePopen(pid=333, out="5 passed in 1.0s")
    res = rw.run_guarded(
        ["python", "-m", "pytest"], cwd="C:/j", env=None, timeout_s=900,
        creationflags=0, state_dir=tmp_path, bot_pid=7, now_fn=lambda: 0,
        popen_factory=lambda *a, **k: fake, kill_fn=lambda p: True)
    assert res.returncode == 0 and "passed" in res.stdout
