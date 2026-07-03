# -*- coding: utf-8 -*-
"""T2: интеграция prompt_intake в текст-роутер process_update.

Инвариант (решение c): ``_swapbatch_text_intercept`` проверяется ПЕРВЫМ,
``_prompt_intake_intercept`` — строго ПОСЛЕ. Двойное ожидание (batch-FSM ждёт
текст И prompt_intake ждёт → оба консьюмят одно сообщение) недопустимо: batch
короткозамыкает роутер до prompt_intake.

Тот же fresh-module-load harness, что и tests/test_process_update_port.py.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.block_m2_video import prompt_intake

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ALLOWED_CHAT = 12345


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", str(ALLOWED_CHAT))
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", "111")
    monkeypatch.setenv("JARVIS_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "0")
    monkeypatch.setenv("JARVIS_VOICE_REPLY_ENABLED", "0")
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_ID", str(ALLOWED_CHAT))
    prompt_intake._AWAITING.clear()
    yield
    prompt_intake._AWAITING.clear()


def _get_mod():
    mod_name = f"_test_pi_router_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _text_update(chat_id, text):
    return {
        "update_id": 1,
        "message": {
            "from": {"id": chat_id, "username": "tester"},
            "chat": {"id": chat_id},
            "text": text,
        },
    }


def test_batch_intercept_short_circuits_prompt_intake(monkeypatch):
    """Порядок-зуб: batch ждёт текст (intercept=True) → prompt_intake НЕ дёргается
    даже если тоже был бы готов консьюмить. Двойное ожидание исключено."""
    mod = _get_mod()
    monkeypatch.setattr(mod, "_persona_video_intercept", lambda *a, **k: False)
    monkeypatch.setattr(mod, "_swapbatch_text_intercept", MagicMock(return_value=True))
    pi_intercept = MagicMock(return_value=True)
    monkeypatch.setattr(mod, "_prompt_intake_intercept", pi_intercept)
    monkeypatch.setattr(mod, "_route_plain_text", MagicMock(return_value=False))
    monkeypatch.setattr(mod, "handle", MagicMock())

    mod.process_update(_text_update(ALLOWED_CHAT, "1. neon"), {})

    pi_intercept.assert_not_called()


def test_prompt_intake_consulted_after_batch_false(monkeypatch):
    """batch не ждёт → prompt_intake-перехват дёрнут; если он консьюмит (True),
    ни LLM-роутер, ни legacy-dispatcher не бегут."""
    mod = _get_mod()
    monkeypatch.setattr(mod, "_persona_video_intercept", lambda *a, **k: False)
    monkeypatch.setattr(mod, "_swapbatch_text_intercept", MagicMock(return_value=False))
    pi_intercept = MagicMock(return_value=True)
    monkeypatch.setattr(mod, "_prompt_intake_intercept", pi_intercept)
    route_plain = MagicMock(return_value=False)
    handle = MagicMock()
    monkeypatch.setattr(mod, "_route_plain_text", route_plain)
    monkeypatch.setattr(mod, "handle", handle)

    mod.process_update(_text_update(ALLOWED_CHAT, "she turns"), {})

    pi_intercept.assert_called_once_with(str(ALLOWED_CHAT), "she turns")
    route_plain.assert_not_called()
    handle.assert_not_called()


def test_falls_through_when_nobody_awaits(monkeypatch):
    """Никто не ждёт → оба перехвата False → уходим в legacy-dispatcher."""
    mod = _get_mod()
    monkeypatch.setattr(mod, "_persona_video_intercept", lambda *a, **k: False)
    monkeypatch.setattr(mod, "_swapbatch_text_intercept", MagicMock(return_value=False))
    monkeypatch.setattr(mod, "_prompt_intake_intercept", MagicMock(return_value=False))
    monkeypatch.setattr(mod, "_route_plain_text", MagicMock(return_value=False))
    handle = MagicMock()
    monkeypatch.setattr(mod, "handle", handle)

    mod.process_update(_text_update(ALLOWED_CHAT, "обычный текст"), {})

    handle.assert_called_once_with(str(ALLOWED_CHAT), "обычный текст")


def test_intercept_returns_false_when_not_armed(monkeypatch):
    """Внутренности: не в ожидании → перехват возвращает False (fall-through)."""
    mod = _get_mod()
    assert mod._prompt_intake_intercept(str(ALLOWED_CHAT), "text") is False


def test_intercept_consumes_and_dispatches_when_armed(monkeypatch):
    """Внутренности: в ожидании → consume + dispatch по kind, возвращает True."""
    mod = _get_mod()
    dispatch = MagicMock()
    monkeypatch.setattr(mod, "_prompt_intake_dispatch", dispatch)
    prompt_intake.arm(ALLOWED_CHAT, "animate")

    ok = mod._prompt_intake_intercept(str(ALLOWED_CHAT), "she smiles")

    assert ok is True
    dispatch.assert_called_once()
    result = dispatch.call_args[0][1]
    assert result.kind == "animate"
    assert result.motion == "she smiles"
    # single-shot: ожидание снято
    assert prompt_intake.is_awaiting(ALLOWED_CHAT) is False


def test_command_not_consumed_and_disarms(monkeypatch):
    """Guard: в ожидании юзер шлёт команду (/...) → не консьюмим (команда идёт
    в свой обработчик), ожидание снимается (не зависает на manual-режиме)."""
    mod = _get_mod()
    dispatch = MagicMock()
    monkeypatch.setattr(mod, "_prompt_intake_dispatch", dispatch)
    prompt_intake.arm(ALLOWED_CHAT, "animate")

    ok = mod._prompt_intake_intercept(str(ALLOWED_CHAT), "/videoref")

    assert ok is False
    dispatch.assert_not_called()
    assert prompt_intake.is_awaiting(ALLOWED_CHAT) is False
