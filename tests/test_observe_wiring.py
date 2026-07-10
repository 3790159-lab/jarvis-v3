# -*- coding: utf-8 -*-
"""Observation console — bot wiring (control module). $0, mocks only, no network.

Covers admin-only-by-omission tooth (Task 5), /git_status /logs_tail /health
dispatch (Task 5), and async /regress with single-flight (Task 6).
"""
import importlib

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


# ── Task 5: admin-only-by-omission tooth (both directions) ─────────────────
def test_observe_commands_are_admin_only_not_friend():
    for c in ("/git_status", "/regress", "/logs_tail", "/health"):
        assert c not in mod.FRIEND_ALLOWED_COMMANDS


def test_git_status_command_sends_text(monkeypatch):
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    import tools.jarvis_observe as o
    monkeypatch.setattr(o, "git_status_text", lambda **k: "HEAD da2312e")
    mod.handle_command(ADMIN, "/git_status", "", {})
    assert "da2312e" in sent["t"]


def test_logs_tail_command_sends_filtered_tail(monkeypatch):
    sent = {}
    seen = {}

    def _fake_tail(path, n=40, **k):
        seen["n"] = n
        return f"TAIL[{n}]"

    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    import tools.jarvis_observe as o
    monkeypatch.setattr(o, "tail_log", _fake_tail)
    mod.handle_command(ADMIN, "/logs_tail", "15", {})
    assert seen["n"] == 15 and "TAIL[15]" in sent["t"]


def test_logs_tail_defaults_to_40(monkeypatch):
    seen = {}

    def _fake_tail(path, n=40, **k):
        seen["n"] = n
        return "x"

    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    import tools.jarvis_observe as o
    monkeypatch.setattr(o, "tail_log", _fake_tail)
    mod.handle_command(ADMIN, "/logs_tail", "", {})
    assert seen["n"] == 40


def test_health_command_sends_snapshot(monkeypatch):
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    import tools.jarvis_observe as o
    monkeypatch.setattr(o, "health_snapshot", lambda readers: "HEALTH-OK")
    mod.handle_command(ADMIN, "/health", "", {})
    assert "HEALTH-OK" in sent["t"]


# ── Task 6: async /regress — verdict + single-flight ───────────────────────
def test_regress_reports_verdict(monkeypatch):
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    # batched runner now supplies the aggregate result; verdict vs baseline unchanged
    monkeypatch.setattr(mod, "_run_regress_batched",
                        lambda **kw: {"status": "complete",
                                      "summary": {"failed": 130, "passed": 3353, "errors": 4},
                                      "batches_run": 5, "batches_total": 5})
    monkeypatch.setattr(mod, "_regress_baseline", lambda: {"failed": 130})
    monkeypatch.setattr(mod._regress_batches, "write_baseline", lambda *a, **k: None)
    mod._REGRESS_RUNNING = False
    mod._regress_run(ADMIN)
    assert any("✅" in s for s in sent)  # not worse than baseline
    assert mod._REGRESS_RUNNING is False  # flag released after run


class _FakeThread:
    def __init__(self, *a, **k):
        self.target = k.get("target")
        self.args = k.get("args", ())

    def start(self):
        _FakeThread.spawned.append(1)  # claim only; do NOT run target


def test_regress_single_flight_blocks_second(monkeypatch):
    _FakeThread.spawned = []
    monkeypatch.setattr(mod.threading, "Thread", _FakeThread)
    msgs = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: msgs.append(t))
    mod._REGRESS_RUNNING = False
    try:
        mod.handle_command(ADMIN, "/regress", "", {})       # first: spawns
        assert len(_FakeThread.spawned) == 1
        mod.handle_command(ADMIN, "/regress", "", {})       # second: blocked
        assert len(_FakeThread.spawned) == 1
        assert any("уже идёт" in m for m in msgs)
    finally:
        mod._REGRESS_RUNNING = False
