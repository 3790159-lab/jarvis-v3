# -*- coding: utf-8 -*-
"""``/devtask_stats`` bot wiring (control module). $0, mocks only, no CC, no network.

Same fresh-singleton-swap pattern as tests/test_devtask_wiring.py — the module
is imported once, and each test injects its own DevTaskQueue on tmp via
``monkeypatch.setattr(mod, "_DEVTASK_QUEUE", ...)``.
"""
import importlib

from app.services.devtask.queue import (
    DevTaskQueue,
    STATUS_MERGED,
    STATUS_ROLLED_BACK,
    STATUS_FAILED,
)

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


def test_devtask_stats_is_admin_only_not_friend():
    assert "/devtask_stats" not in mod.FRIEND_ALLOWED_COMMANDS


def test_devtask_stats_default_30_days(monkeypatch, tmp_path):
    q = DevTaskQueue(base_dir=tmp_path)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    mod.handle_command(ADMIN, "/devtask_stats", "", {})
    assert sent
    assert "30д" in sent[-1]
    assert "Всего задач: 0" in sent[-1]


def test_devtask_stats_custom_days_arg(monkeypatch, tmp_path):
    q = DevTaskQueue(base_dir=tmp_path)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    mod.handle_command(ADMIN, "/devtask_stats", "7", {})
    assert "7д" in sent[-1]


def test_devtask_stats_invalid_days_arg_falls_back_to_default(monkeypatch, tmp_path):
    q = DevTaskQueue(base_dir=tmp_path)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    mod.handle_command(ADMIN, "/devtask_stats", "banana", {})
    assert "30д" in sent[-1]


def test_devtask_stats_reflects_seeded_cards(monkeypatch, tmp_path):
    q = DevTaskQueue(base_dir=tmp_path)
    t1 = q.add("shipped fix")
    q.set_status(t1, STATUS_MERGED, cost=1.5)
    t2 = q.add("bad run")
    q.set_status(t2, STATUS_FAILED, error="no_report")
    t3 = q.add("aborted")
    q.set_status(t3, STATUS_ROLLED_BACK)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    mod.handle_command(ADMIN, "/devtask_stats", "", {})
    body = sent[-1]
    assert "Всего задач: 3" in body
    assert "Смерджено: 1" in body
    assert "Откат: 1" in body
    assert "No report: 1" in body
    assert "$1.50" in body
