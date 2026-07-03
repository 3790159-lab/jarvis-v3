# -*- coding: utf-8 -*-
"""T4: свой motion-промт для /videoref — БЕСПЛАТНЫЙ путь + сброс.

Ручной промт сосуществует с платным Grok-анализом (решение a): если motion
задан вручную, generate_video_motion_prompt (Grok) НЕ вызывается, record_cost и
check_limit — тоже. Ручной путь зовёт только бесплатный select_best_frame.
Сброс: тоггл vref:custom + disarm при переключении на Grok.
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

CHAT = 777


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", str(CHAT))
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", str(CHAT))
    monkeypatch.setenv("JARVIS_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "0")
    prompt_intake._AWAITING.clear()
    yield
    prompt_intake._AWAITING.clear()


def _get_mod():
    mod_name = f"_test_vref_custom_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _result(motion="a woman dances slowly"):
    return prompt_intake.ConsumeResult(kind="videoref", motion=motion, truncated=False)


def test_offer_keyboard_has_both_buttons():
    mod = _get_mod()
    rows = mod._videoref_offer_keyboard()
    callbacks = [b["callback_data"] for row in rows for b in row]
    assert "vref:motion" in callbacks   # платный Grok
    assert "vref:custom" in callbacks   # бесплатный ручной


def test_manual_path_is_free_no_grok_no_charge(monkeypatch):
    """Money-зуб: ручной путь НЕ зовёт Grok, record_cost, check_limit; motion
    берётся из result. Мутация: если ручной путь позовёт Grok — падает."""
    mod = _get_mod()
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_send_local_photo", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_videoref_duration_keyboard", lambda *a, **k: [])
    monkeypatch.setattr(mod, "select_best_frame", lambda frames, validator: "best.jpg")

    grok_spy = MagicMock()
    record_spy = MagicMock()
    limit_spy = MagicMock()
    monkeypatch.setattr(mod, "generate_video_motion_prompt", grok_spy)
    monkeypatch.setattr(mod._cost, "record_cost", record_spy)
    monkeypatch.setattr(mod, "_check_limit", limit_spy)

    mod._VIDEOREF_PENDING[CHAT] = {"frames_dir": None, "duration": 5}
    mod._videoref_apply_custom_motion(str(CHAT), _result("she winks"))

    grok_spy.assert_not_called()
    record_spy.assert_not_called()
    limit_spy.assert_not_called()
    assert mod._VIDEOREF_SWAP_PENDING[CHAT]["motion_prompt"] == "she winks"


def test_manual_path_no_face_no_pending(monkeypatch):
    mod = _get_mod()
    sent = []
    monkeypatch.setattr(mod, "send", lambda cid, txt, *a, **k: sent.append(txt))
    monkeypatch.setattr(mod, "select_best_frame", lambda frames, validator: None)
    mod._VIDEOREF_PENDING[CHAT] = {"frames_dir": None, "duration": 5}

    mod._videoref_apply_custom_motion(str(CHAT), _result())

    assert CHAT not in mod._VIDEOREF_SWAP_PENDING
    assert any("лицо" in t for t in sent)


def test_custom_toggle_arms_then_disarms(monkeypatch):
    """Сброс-зуб: тоггл vref:custom. Первый тап арм-ит, повторный — снимает."""
    mod = _get_mod()
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)

    mod._videoref_custom_toggle(str(CHAT))
    assert prompt_intake.is_awaiting(CHAT) is True

    mod._videoref_custom_toggle(str(CHAT))
    assert prompt_intake.is_awaiting(CHAT) is False


def test_grok_run_disarms_pending_manual_wait(monkeypatch):
    """Сброс-зуб: переключение на Grok (_videoref_motion_run) снимает ожидание
    intake, чтобы набранный после текст не ушёл в ручной путь. Мутация: убрать
    disarm → падает."""
    mod = _get_mod()
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    prompt_intake.arm(CHAT, "videoref")
    # Нет _VIDEOREF_PENDING → run вернётся рано, но ДО этого снимет ожидание.
    mod._videoref_motion_run(str(CHAT))
    assert prompt_intake.is_awaiting(CHAT) is False
