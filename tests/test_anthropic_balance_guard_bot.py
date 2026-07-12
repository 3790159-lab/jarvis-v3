# -*- coding: utf-8 -*-
"""Bot-level wiring for the ``credit_balance_too_low`` fail-closed guard.

The core detection/fail-closed mechanism lives in
``app.services.unified.llm_router.llm_client`` (see ``tests/test_llm_client.py``).
This module covers how ``tools.jarvis_smart_telegram_control`` reacts to it:

  * the callback_query exception handler turns a ``CreditBalanceDepletedError``
    into the exact user-facing refusal text + a single per-episode owner alert
    (instead of the generic "❌ Ошибка" toast);
  * unrelated exceptions still take the old generic-error path unchanged;
  * ``/reset_balance_flag`` is an admin-only command that clears the flag.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.jarvis_smart_telegram_control as ctl
from app.services.unified.llm_router import llm_client as llmc

_USER_MSG = "⚠️ AI-сервис временно недоступен: закончился баланс Anthropic"
_OWNER_MSG = "🔴 Anthropic credit exhausted — пополни баланс"


@pytest.fixture(autouse=True)
def _reset_balance_flag():
    llmc.reset_balance_flag()
    yield
    llmc.reset_balance_flag()


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(ctl, "send", lambda cid, txt, *a, **k: out.append((cid, txt)))
    return out


@pytest.fixture
def acks(monkeypatch):
    out = []
    monkeypatch.setattr(
        ctl, "answer_callback_query", lambda cq_id, text="": out.append((cq_id, text))
    )
    return out


@pytest.fixture
def owner_alerts(monkeypatch):
    out = []
    monkeypatch.setattr(
        "app.services.notifications.send_alert", lambda text: out.append(text) or True
    )
    return out


def _cq(cq_id="cq1", uid=1, chat_id=99):
    return {
        "id": cq_id,
        "data": "confirm:run",
        "from": {"id": uid},
        "message": {"chat": {"id": chat_id}},
    }


# ── callback_query exception handler ────────────────────────────────────────


def test_callback_query_credit_balance_error_notifies_user(monkeypatch, sent, acks):
    monkeypatch.setattr(
        ctl, "handle_callback_query",
        lambda cq, state: (_ for _ in ()).throw(
            llmc.CreditBalanceDepletedError("credit balance is too low")
        ),
    )
    ctl._handle_callback_query_safely(_cq(chat_id=99), {})

    assert sent == [("99", _USER_MSG)]
    assert llmc.is_balance_depleted() is True
    assert acks  # callback still acknowledged, no exception escapes


def test_callback_query_credit_balance_error_alerts_owner_once(
    monkeypatch, sent, acks, owner_alerts
):
    monkeypatch.setattr(
        ctl, "handle_callback_query",
        lambda cq, state: (_ for _ in ()).throw(
            llmc.CreditBalanceDepletedError("credit balance is too low")
        ),
    )
    # A series of errors within the same episode.
    ctl._handle_callback_query_safely(_cq(cq_id="cq1", chat_id=99), {})
    ctl._handle_callback_query_safely(_cq(cq_id="cq2", chat_id=99), {})
    ctl._handle_callback_query_safely(_cq(cq_id="cq3", chat_id=100), {})

    assert owner_alerts == [_OWNER_MSG]  # exactly once, no repeats
    assert sent == [("99", _USER_MSG), ("99", _USER_MSG), ("100", _USER_MSG)]


def test_callback_query_other_exception_keeps_generic_error_path(
    monkeypatch, sent, acks, owner_alerts
):
    monkeypatch.setattr(
        ctl, "handle_callback_query",
        lambda cq, state: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    ctl._handle_callback_query_safely(_cq(), {})

    assert sent == []  # no balance-specific user message
    assert owner_alerts == []
    assert acks == [("cq1", "❌ Ошибка")]
    assert llmc.is_balance_depleted() is False


def test_callback_query_success_path_unaffected(monkeypatch, sent, acks):
    calls = []
    monkeypatch.setattr(
        ctl, "handle_callback_query", lambda cq, state: calls.append((cq, state))
    )
    ctl._handle_callback_query_safely(_cq(), {"k": "v"})

    assert calls == [(_cq(), {"k": "v"})]
    assert sent == []
    assert acks == []


# ── process_update wiring ───────────────────────────────────────────────────


def test_process_update_routes_callback_errors_through_safely(monkeypatch, sent, acks):
    monkeypatch.setattr(ctl, "_whitelist_gate", lambda upd: True)
    monkeypatch.setattr(ctl, "_audit_message", lambda upd: None)
    monkeypatch.setattr(ctl, "_remember_identity", lambda upd: None)
    monkeypatch.setattr(ctl, "_is_member_id", lambda uid: True)
    monkeypatch.setattr(ctl, "load_state", lambda: {})
    monkeypatch.setattr(
        ctl, "handle_callback_query",
        lambda cq, state: (_ for _ in ()).throw(
            llmc.CreditBalanceDepletedError("credit balance is too low")
        ),
    )

    ctl.process_update({"callback_query": _cq(chat_id=99)})

    assert sent == [("99", _USER_MSG)]


# ── /reset_balance_flag (admin-only) ────────────────────────────────────────


def _reset_upd(uid=1):
    return {"message": {"text": "/reset_balance_flag",
                         "from": {"id": uid}, "chat": {"id": uid}}}


def test_reset_balance_flag_rejects_non_admin(monkeypatch, sent):
    monkeypatch.setattr(ctl, "_is_admin_id", lambda uid: False)
    monkeypatch.setattr(ctl, "ALLOWED_CHAT_ID", "999999")
    llmc.mark_balance_depleted()

    consumed = ctl._reset_balance_flag_intercept(_reset_upd(uid=1))

    assert consumed is True
    assert sent == [("1", "🚫 Команда доступна только администратору.")]
    assert llmc.is_balance_depleted() is True  # untouched


def test_reset_balance_flag_clears_for_admin(monkeypatch, sent):
    monkeypatch.setattr(ctl, "_is_admin_id", lambda uid: True)
    llmc.mark_balance_depleted()
    llmc.should_alert_owner()

    consumed = ctl._reset_balance_flag_intercept(_reset_upd(uid=1))

    assert consumed is True
    assert llmc.is_balance_depleted() is False
    assert sent and "сброшен" in sent[0][1]
    # A fresh episode after reset must be able to alert the owner again.
    assert llmc.should_alert_owner() is True


def test_reset_balance_flag_ignores_unrelated_commands():
    assert ctl._reset_balance_flag_intercept({"message": {"text": "/start"}}) is False
    assert ctl._reset_balance_flag_intercept({"message": {}}) is False
