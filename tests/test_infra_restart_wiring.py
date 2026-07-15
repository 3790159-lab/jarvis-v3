# -*- coding: utf-8 -*-
"""DEV-12 /infra_restart bot wiring (control module). $0, mocks only — no real
subprocess/schtasks/service ever runs; app.services.infra_control itself is
mocked out here (its own module has its own mock-only test suite,
tests/test_infra_control.py).

Covers: strictly-literal admin gate (req 2, logged on refusal — not the
general _is_admin_id/ALLOWED_CHAT_ID role gate, see feedback memory
no-unrequested-indirection), the 3-button + status keyboard (req 1), the
allowlist chokepoint on the callback (req 3), and the before/after report
(req 6) including the bot target's special two-message flow (req for a
self-restarting process, see infra_control.restart_bot docstring).
"""
import importlib

import pytest

mod = importlib.import_module("tools.jarvis_smart_telegram_control")
ADMIN = 237616472
NOT_ADMIN = 999999


class _ImmediateThread:
    """Runs the target synchronously — mirrors the pattern already used by
    tests/test_devtask_wiring.py for threading.Thread-dispatched work."""
    def __init__(self, *a, **k):
        self._t = k.get("target")
        self._args = k.get("args", ())

    def start(self):
        self._t(*self._args)


@pytest.fixture(autouse=True)
def _sync_threads(monkeypatch):
    monkeypatch.setattr(mod.threading, "Thread", _ImmediateThread)


# ---------------------------------------------------------------------------
# /infra_restart command — strict literal admin gate (req 2)
# ---------------------------------------------------------------------------

def test_infra_restart_admin_id_is_the_literal_spec_id():
    assert mod._INFRA_RESTART_ADMIN_ID == 237616472


def test_command_intercept_ignores_unrelated_text():
    upd = {"message": {"text": "/health", "from": {"id": ADMIN}, "chat": {"id": ADMIN}}}
    assert mod._infra_restart_command_intercept(upd) is False


def test_command_intercept_ignores_non_command_text():
    upd = {"message": {"text": "hello there", "from": {"id": ADMIN}, "chat": {"id": ADMIN}}}
    assert mod._infra_restart_command_intercept(upd) is False


def test_command_rejects_non_admin_and_logs(monkeypatch, caplog):
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    dispatched = []
    monkeypatch.setattr(mod, "_infra_restart_dispatch", lambda cid: dispatched.append(cid))
    upd = {"message": {"text": "/infra_restart", "from": {"id": NOT_ADMIN, "username": "eve"},
                        "chat": {"id": NOT_ADMIN}}}
    with caplog.at_level("WARNING"):
        handled = mod._infra_restart_command_intercept(upd)
    assert handled is True
    assert dispatched == []
    assert sent and "администратор" in sent[0].lower()
    assert any("infra_restart" in r.message and str(NOT_ADMIN) in r.message for r in caplog.records)


def test_command_dispatches_for_the_real_admin(monkeypatch):
    dispatched = []
    monkeypatch.setattr(mod, "_infra_restart_dispatch", lambda cid: dispatched.append(cid))
    upd = {"message": {"text": "/infra_restart", "from": {"id": ADMIN, "username": "daniil"},
                        "chat": {"id": ADMIN}}}
    handled = mod._infra_restart_command_intercept(upd)
    assert handled is True
    assert dispatched == [str(ADMIN)]


def test_command_dispatch_never_reached_before_admin_check(monkeypatch):
    """Security-order regression: the admin check must run BEFORE anything
    that could touch a real service is even considered."""
    calls = []
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    def _boom(cid):
        calls.append(cid)
        raise AssertionError("dispatch must never run for a non-admin")

    monkeypatch.setattr(mod, "_infra_restart_dispatch", _boom)
    upd = {"message": {"text": "/infra_restart", "from": {"id": NOT_ADMIN}, "chat": {"id": NOT_ADMIN}}}
    mod._infra_restart_command_intercept(upd)
    assert calls == []


# ---------------------------------------------------------------------------
# Status + keyboard (req 1)
# ---------------------------------------------------------------------------

def test_dispatch_shows_all_three_targets_with_status(monkeypatch):
    from app.services import infra_control as ic
    monkeypatch.setattr(ic, "status_all",
                        lambda **k: {"cloudflared": "StopPending", "backend": "Running", "bot": "Running"})
    kb_sent = []
    monkeypatch.setattr(mod, "send_with_keyboard", lambda cid, t, kb, *a, **k: kb_sent.append((t, kb)))

    mod._infra_restart_dispatch(str(ADMIN))

    assert len(kb_sent) == 1
    text, kb = kb_sent[0]
    assert "cloudflared" in text and "StopPending" in text
    assert "backend" in text and "bot" in text
    datas = [b["callback_data"] for row in kb for b in row]
    assert sorted(datas) == ["infra:restart:backend", "infra:restart:bot", "infra:restart:cloudflared"]


def test_keyboard_has_exactly_three_buttons_one_per_target():
    kb = mod._infra_restart_keyboard()
    datas = [b["callback_data"] for row in kb for b in row]
    assert len(datas) == 3
    assert set(datas) == {"infra:restart:cloudflared", "infra:restart:backend", "infra:restart:bot"}


# ---------------------------------------------------------------------------
# Callback tap — strict literal admin gate + allowlist chokepoint (req 2, 3)
# ---------------------------------------------------------------------------

def _cq(uid, data):
    return {"id": "cq1", "data": data,
            "message": {"chat": {"id": uid}, "message_id": 1},
            "from": {"id": uid}}


def test_callback_rejects_non_admin_and_logs(monkeypatch, caplog):
    acked = []
    monkeypatch.setattr(mod, "answer_callback_query", lambda cid, t="": acked.append(t))
    restarted = []
    monkeypatch.setattr("app.services.infra_control.restart",
                        lambda target, **k: restarted.append(target) or {"ok": True})
    with caplog.at_level("WARNING"):
        mod.handle_callback_query(_cq(NOT_ADMIN, "infra:restart:cloudflared"), {})
    assert restarted == []
    assert acked and "администратор" in acked[0].lower()
    assert any("infra_restart" in r.message and str(NOT_ADMIN) in r.message for r in caplog.records)


def test_callback_rejects_target_outside_allowlist(monkeypatch):
    acked = []
    monkeypatch.setattr(mod, "answer_callback_query", lambda cid, t="": acked.append(t))
    restarted = []
    monkeypatch.setattr("app.services.infra_control.restart",
                        lambda target, **k: restarted.append(target) or {"ok": True})
    mod.handle_callback_query(_cq(ADMIN, "infra:restart:shell"), {})
    assert restarted == []


def test_callback_rejects_unknown_action(monkeypatch):
    restarted = []
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr("app.services.infra_control.restart",
                        lambda target, **k: restarted.append(target) or {"ok": True})
    mod.handle_callback_query(_cq(ADMIN, "infra:poke:bot"), {})
    assert restarted == []


@pytest.mark.parametrize("target", ["cloudflared", "backend", "bot"])
def test_callback_dispatches_exactly_the_tapped_target(monkeypatch, target):
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    restarted = []
    monkeypatch.setattr(
        "app.services.infra_control.restart",
        lambda tgt, **k: restarted.append(tgt) or {"ok": True, "before": "Down", "after": "Running"},
    )
    mod.handle_callback_query(_cq(ADMIN, "infra:restart:%s" % target), {})
    assert restarted == [target]


def test_callback_dispatch_reports_before_after_for_cloudflared(monkeypatch):
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(
        "app.services.infra_control.restart",
        lambda tgt, **k: {"ok": True, "before": "StopPending", "after": "Running"},
    )
    mod.handle_callback_query(_cq(ADMIN, "infra:restart:cloudflared"), {})
    assert len(sent) == 1
    assert "StopPending" in sent[0] and "Running" in sent[0]
    assert "✅" in sent[0]


def test_callback_dispatch_reports_failure_honestly(monkeypatch):
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(
        "app.services.infra_control.restart",
        lambda tgt, **k: {"ok": False, "before": "Stopped", "after": "Stopped"},
    )
    mod.handle_callback_query(_cq(ADMIN, "infra:restart:backend"), {})
    assert len(sent) == 1
    assert "⚠️" in sent[0]
    assert "Stopped" in sent[0]


def test_callback_dispatch_reports_path_letter_for_cloudflared_success(monkeypatch):
    """DEV-12a req: the report must say which cascade path (A/B) fired."""
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(
        "app.services.infra_control.restart",
        lambda tgt, **k: {"ok": True, "before": "Stopped", "after": "Running",
                          "path": "A", "detail": "путь A: elevated, прямой Start-Service"},
    )
    mod.handle_callback_query(_cq(ADMIN, "infra:restart:cloudflared"), {})
    assert len(sent) == 1
    assert "[путь A]" in sent[0]


def test_callback_dispatch_reports_physical_access_needed_honestly(monkeypatch):
    """Both cascade paths unavailable (not elevated + registration failed)
    must produce an honest, specific report — not a silent/generic failure."""
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(
        "app.services.infra_control.restart",
        lambda tgt, **k: {"ok": False, "before": "StopPending", "after": "StopPending",
                          "path": "B", "detail": "нужен физический доступ: не elevated, "
                                                   "задача не зарегистрирована и авторегистрация "
                                                   "на лету не удалась"},
    )
    mod.handle_callback_query(_cq(ADMIN, "infra:restart:cloudflared"), {})
    assert len(sent) == 1
    assert "физическ" in sent[0].lower()
    assert "⚠️" in sent[0]
    assert "[путь B]" in sent[0]


def test_callback_dispatch_bot_target_has_no_synchronous_after(monkeypatch):
    """Self-restart special case: 'after' is unknowable synchronously (this
    very process dies), so the report must say a confirmation follows, not
    fabricate a fake 'after' status."""
    monkeypatch.setattr(mod, "answer_callback_query", lambda *a, **k: None)
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, t, *a, **k: sent.append(t))
    monkeypatch.setattr(
        "app.services.infra_control.restart",
        lambda tgt, **k: {"ok": True, "before": "Running", "after": None,
                          "detail": "restart triggered — confirmation follows"},
    )
    mod.handle_callback_query(_cq(ADMIN, "infra:restart:bot"), {})
    assert len(sent) == 1
    assert "Running" in sent[0]
    assert "отдельным сообщением" in sent[0] or "подтвержд" in sent[0].lower()
