# -*- coding: utf-8 -*-
"""Wiring of the detached regress watchdog into the bot (Этап 1, хвост #6).

Two hooks: (1) every regress spawn goes through ``regress_watch.run_guarded`` so
a PID-group + deadline marker is recorded; (2) the periodic heartbeat loop sweeps
that marker. $0, mocks only — no real pytest, no real kills, no network.
"""
import importlib
import subprocess

mod = importlib.import_module("tools.jarvis_smart_telegram_control")


class _Done:
    returncode = 0
    stdout = "1 passed in 0.10s"


# ── periodic loop sweeps the marker ─────────────────────────────────────────
def test_heartbeat_tick_runs_regress_sweep(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(mod, "_regress_watch_sweep", lambda: calls.append(1))
    monkeypatch.setattr(mod, "_devtask_remind_queued", lambda: None)
    monkeypatch.setattr(mod, "_HEARTBEAT_FILE", tmp_path / "hb.txt")
    mod._heartbeat_tick()
    assert calls == [1]


def test_heartbeat_tick_survives_sweep_fault(monkeypatch):
    def boom():
        raise RuntimeError("sweep blew up")
    monkeypatch.setattr(mod, "_regress_watch_sweep", boom)
    monkeypatch.setattr(mod, "_devtask_remind_queued", lambda: None)
    # must NOT raise — a dead heartbeat would make the guardian restart the bot
    mod._heartbeat_tick()


def test_regress_watch_sweep_delegates_with_real_ops(monkeypatch):
    captured = {}

    def fake_sweep(state_dir, *, now, parent_alive_fn, kill_fn, notify_fn=None, log_fn=None):
        captured.update(state_dir=str(state_dir), now=now,
                        parent_alive_fn=parent_alive_fn, kill_fn=kill_fn,
                        notify_fn=notify_fn, log_fn=log_fn)
        return None

    monkeypatch.setattr(mod._regress_watch, "sweep", fake_sweep)
    mod._regress_watch_sweep()
    assert captured["state_dir"].replace("\\", "/").endswith("/state")
    assert callable(captured["parent_alive_fn"])
    assert callable(captured["kill_fn"])
    assert callable(captured["notify_fn"])   # admin notification wired
    assert callable(captured["log_fn"])      # logging wired


# ── every spawn site records a marker via run_guarded ───────────────────────
def test_full_regress_gate_uses_run_guarded(monkeypatch):
    captured = {}
    monkeypatch.setattr(mod._regress_watch, "run_guarded",
                        lambda cmd, **kw: captured.update(cmd=cmd, kw=kw) or _Done())
    monkeypatch.setattr(mod, "_regress_baseline", lambda: {"failed": 0})
    monkeypatch.setattr(mod, "_devtask_pytest_env", lambda: {"E": "1"})
    mod._devtask_run_regress("C:/wt")
    assert "pytest" in captured["cmd"] and captured["kw"]["cwd"] == "C:/wt"
    assert captured["kw"]["env"] == {"E": "1"}
    assert captured["kw"]["timeout_s"] >= 1
    assert captured["kw"]["label"]           # labelled for the kill notice


def test_targeted_gate_uses_run_guarded(monkeypatch):
    captured = {}
    monkeypatch.setattr(mod._regress_watch, "run_guarded",
                        lambda cmd, **kw: captured.update(cmd=cmd, kw=kw) or _Done())
    monkeypatch.setattr(mod, "_devtask_pytest_env", lambda: {"E": "1"})
    from app.services.devtask import target_tests as tt
    monkeypatch.setattr(tt, "changed_paths", lambda *a, **k: ["app/x.py"])
    monkeypatch.setattr(tt, "list_test_files", lambda *a, **k: ["tests/test_x.py"])
    monkeypatch.setattr(tt, "map_paths_to_tests", lambda *a, **k: ["tests/test_x.py"])
    mod._devtask_run_targeted("C:/wt", "base1")
    assert "pytest" in captured["cmd"] and captured["kw"]["cwd"] == "C:/wt"
    assert captured["kw"]["label"]


def test_slash_regress_uses_run_guarded(monkeypatch):
    captured = {}
    monkeypatch.setattr(mod._regress_watch, "run_guarded",
                        lambda cmd, **kw: captured.update(cmd=cmd, kw=kw) or _Done())
    out = mod._regress_run_pytest()
    assert "pytest" in captured["cmd"]
    assert captured["kw"]["cwd"] == "C:/jarvis"
    assert captured["kw"]["label"]
    assert "passed" in out


def test_run_guarded_timeout_surfaces_as_timeout_message(monkeypatch):
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=kw.get("timeout_s"))
    monkeypatch.setattr(mod._regress_watch, "run_guarded", boom)
    monkeypatch.setattr(mod, "_devtask_pytest_env", lambda: {"E": "1"})
    res = mod._devtask_run_regress("C:/wt")
    assert res["ok"] is False and "таймаут" in res["text"]
