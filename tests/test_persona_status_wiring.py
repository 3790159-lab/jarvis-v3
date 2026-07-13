# -*- coding: utf-8 -*-
"""/persona_status bot wiring (control module). $0, mocks only, no network.

Covers: free (not paid) + admin-only registry, and that handle_command routes
through to app.handlers.persona_handler.handle_persona_status. Handler-level
behavior (card formatting, HEAD-alive check, LoRA status lookup) is covered in
tests/test_persona_status.py.
"""
import importlib

import app.handlers.persona_handler as ph

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = "237616472"


def test_persona_status_is_free_not_paid():
    from tools import intent_router as _ir
    assert _ir.is_paid("/persona_status") is False
    assert _ir.auto_exec_ok("/persona_status") is True


def test_persona_status_admin_only():
    assert "/persona_status" not in mod.FRIEND_ALLOWED_COMMANDS


def test_persona_status_command_dispatches(monkeypatch):
    fired = {}
    monkeypatch.setattr(
        ph, "handle_persona_status",
        lambda cid, args: fired.update(cid=cid, args=args),
    )
    monkeypatch.setattr(ph, "init_bot", lambda *a, **k: None)
    mod.handle_command(ADMIN, "/persona_status", "persona_abc123", {})
    assert fired == {"cid": int(ADMIN), "args": "persona_abc123"}
