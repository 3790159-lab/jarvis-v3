# -*- coding: utf-8 -*-
"""Dev-task bot wiring (control module). $0, mocks only, no CC, no network."""
import importlib

from app.services.devtask.queue import DevTaskQueue, STATUS_RUNNING

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


# ── Task 5: /dev_task admin-only + dispatch + single-flight ────────────────
def test_dev_task_is_admin_only_not_friend():
    assert "/dev_task" not in mod.FRIEND_ALLOWED_COMMANDS


def test_dev_task_enqueues_and_sends_confirm(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", DevTaskQueue(base_dir=tmp_path), raising=False)
    sent = {}
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, t, kb: sent.setdefault("kb", (t, kb)))
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    mod.handle_command(ADMIN, "/dev_task", "add a foo command", {})
    t, kb = sent["kb"]
    assert "add a foo command" in t
    datas = [b["callback_data"] for row in kb for b in row]
    assert any(d.startswith("devtask:confirm:") for d in datas)
    assert any(d.startswith("devtask:cancel:") for d in datas)


def test_dev_task_rejects_when_active(monkeypatch, tmp_path):
    q = DevTaskQueue(base_dir=tmp_path)
    tid = q.add("busy")
    q.set_status(tid, STATUS_RUNNING)
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", q, raising=False)
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: sent.setdefault("kb", True))
    mod.handle_command(ADMIN, "/dev_task", "another", {})
    assert "активная" in sent["t"] and "kb" not in sent


def test_dev_task_empty_shows_usage(monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "_DEVTASK_QUEUE", DevTaskQueue(base_dir=tmp_path), raising=False)
    sent = {}
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.setdefault("t", t))
    mod.handle_command(ADMIN, "/dev_task", "", {})
    assert "Использование" in sent["t"]
