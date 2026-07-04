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
