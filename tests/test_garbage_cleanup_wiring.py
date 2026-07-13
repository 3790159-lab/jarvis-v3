# -*- coding: utf-8 -*-
"""``/garbage_cleanup`` bot wiring (control module). $0, mocks only, no CC, no
network, no real APScheduler, no real filesystem I/O — mirrors the pattern in
tests/test_devtask_stats_wiring.py."""
import importlib

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


def test_garbage_cleanup_is_admin_only_not_friend():
    assert "/garbage_cleanup" not in mod.FRIEND_ALLOWED_COMMANDS


def test_garbage_cleanup_dispatch_calls_scheduler_run(monkeypatch):
    calls = []

    class _FakeScheduler:
        def run_garbage_cleanup(self, chat_id):
            calls.append(chat_id)

    monkeypatch.setattr(mod, "_get_scheduler", lambda: _FakeScheduler())
    mod._garbage_cleanup_dispatch(ADMIN)
    assert calls == [ADMIN]


def test_garbage_cleanup_command_dispatches_via_handle_command(monkeypatch):
    calls = []
    monkeypatch.setattr(mod, "_garbage_cleanup_dispatch", lambda cid: calls.append(cid))
    mod.handle_command(ADMIN, "/garbage_cleanup", "", {})
    assert calls == [ADMIN]


def test_get_scheduler_registers_weekly_job_on_first_creation(monkeypatch):
    registered = []

    class _FakeJarvisScheduler:
        def __init__(self, send_fn=None):
            pass

        def start(self):
            pass

        def ensure_garbage_cleanup_job(self, chat_id, cron="0 4 * * 1"):
            registered.append(chat_id)

    import app.services.scheduler as scheduler_mod
    monkeypatch.setattr(scheduler_mod, "JarvisScheduler", _FakeJarvisScheduler)
    monkeypatch.setattr(mod, "_JARVIS_SCHEDULER", None, raising=False)
    monkeypatch.setattr(mod, "ALLOWED_CHAT_ID", "42", raising=False)

    mod._get_scheduler()

    assert registered == ["42"]
