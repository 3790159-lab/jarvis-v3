# -*- coding: utf-8 -*-
"""T3: свой motion-промт для одиночного /animate.

Кнопка «✍️ Свой промт» (anim:custom) арм-ит prompt_intake; съеденная строка
пишется в _ANIMATE_PENDING["motion"]; генерация читает pend["motion"] вместо
хардкода motion="". Batch build_engine_keyboard кнопку НЕ содержит.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.block_m2_video import prompt_intake
from app.services.block_m2_video.generation_lock import GenerationLockBusy

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHAT = 999


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", str(CHAT))
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", str(CHAT))  # admin → без лимита
    monkeypatch.setenv("JARVIS_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "0")
    prompt_intake._AWAITING.clear()
    yield
    prompt_intake._AWAITING.clear()


def _get_mod():
    mod_name = f"_test_anim_custom_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_standalone_keyboard_has_custom_button():
    mod = _get_mod()
    rows = mod._animate_engine_keyboard(CHAT)
    callbacks = [b["callback_data"] for row in rows for b in row]
    assert "anim:custom" in callbacks
    # engine rewrite still present, cancel row present
    assert "anim:spicy" in callbacks
    assert "anim:none" in callbacks


def test_batch_keyboard_has_no_custom_button():
    """Batch build_engine_keyboard не должна содержать ручную кнопку (standalone-
    only). Мутация-страховка: если протечёт в batch — падает."""
    from app.handlers.face_swap_handler import FaceSwapHandler
    kb = FaceSwapHandler.build_engine_keyboard()
    callbacks = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
    assert "anim:custom" not in callbacks
    texts = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert not any("Свой промт" in t for t in texts)


def test_custom_arm_sets_awaiting():
    mod = _get_mod()
    mod._animate_custom_arm(str(CHAT))
    assert prompt_intake.is_awaiting(CHAT) is True
    assert prompt_intake.awaiting_kind(CHAT) == "animate"


def test_dispatch_writes_motion_into_pending(monkeypatch):
    mod = _get_mod()
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    mod._ANIMATE_PENDING[CHAT] = {"photo": "/x.jpg"}
    result = prompt_intake.ConsumeResult(kind="animate", motion="she winks", truncated=False)

    mod._prompt_intake_dispatch(str(CHAT), result)

    assert mod._ANIMATE_PENDING[CHAT]["motion"] == "she winks"


def test_dispatch_empty_motion_not_overwritten(monkeypatch):
    mod = _get_mod()
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    monkeypatch.setattr(mod, "send_with_keyboard", lambda *a, **k: None)
    mod._ANIMATE_PENDING[CHAT] = {"photo": "/x.jpg", "motion": "prev"}
    result = prompt_intake.ConsumeResult(kind="animate", motion="", truncated=False)

    mod._prompt_intake_dispatch(str(CHAT), result)

    assert mod._ANIMATE_PENDING[CHAT]["motion"] == "prev"


def test_generation_reads_pending_motion(monkeypatch):
    """Зуб генерации: build_single_animate_request получает pend['motion'],
    а не хардкод ''. Мутация: вернуть '' → падает."""
    mod = _get_mod()
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    # _animate_run_single imports EngineRouter (→ replicate) at entry; replicate
    # isn't installed in the test env, so stub it — we never reach the worker.
    sys.modules.setdefault("replicate", MagicMock())

    spy = MagicMock(return_value="FAKE_REQ")
    fake_handler = MagicMock()
    fake_handler.build_single_animate_request = spy
    monkeypatch.setattr(mod, "_swapbatch_get_handler", lambda: (fake_handler, None))

    # check_limit → allow (patch at source module the function is imported from)
    import app.services.auth.access_control as _ac
    monkeypatch.setattr(_ac, "check_limit", lambda *a, **k: (True, ""))

    # lock busy → return right after building the request (no worker thread)
    fake_lock = MagicMock()
    fake_lock.acquire.side_effect = GenerationLockBusy()
    monkeypatch.setattr(mod, "_get_video_lock", lambda: fake_lock)

    mod._ANIMATE_PENDING[CHAT] = {"photo": "/x.jpg", "motion": "custom words"}
    mod._animate_run_single(str(CHAT), "spicy")

    spy.assert_called_once()
    assert spy.call_args.kwargs["motion"] == "custom words"
