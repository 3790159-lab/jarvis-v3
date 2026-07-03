# -*- coding: utf-8 -*-
"""Тоггл плавности 48fps (RIFE) для одиночного /animate (порт batch sbsmooth: → asmooth:).

Зеркало T5 (sbq:→aq:). Тоггл 🪶 в quality-меню пишет _ANIMATE_PENDING["smooth"]
(дефолт OFF, money-safe). Money-safe делегированием: успех сглаживания идёт через
существующий handler._interpolate_batch (fail→оригинал, биллит ТОЛЬКО успех внутри
себя → без двойного списания). quote==charge: est на кнопке И в check_limit-гейте
включают rife_surcharge_usd(1, seconds) при smooth ON. Префикс asmooth: во FRIEND_ALLOWED.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.block_m2_video import prompt_intake
from app.services.block_m2_video.generation_lock import GenerationLockBusy

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHAT = 555


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
    mod_name = f"_test_anim_smooth_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _gen_button_usd(rows) -> float:
    """Extract the $ number from the aq:done ('Генерировать') button."""
    txt = [b["text"] for row in rows for b in row
           if b["callback_data"] == "aq:done"][0]
    return float(re.search(r"\$([\d.]+)", txt).group(1))


# ── T-a: клавиатура содержит 🪶-тоггл + префикс во FRIEND_ALLOWED ──────────────
def test_keyboard_has_smooth_toggle_and_prefix_allowed():
    mod = _get_mod()
    mod._ANIMATE_PENDING[CHAT] = {"photo": "/x.jpg", "engine_mode": "spicy"}
    rows = mod._animate_quality_keyboard(CHAT)
    callbacks = [b["callback_data"] for row in rows for b in row]
    # дефолт OFF → тап включает → callback "asmooth:on"
    assert "asmooth:on" in callbacks
    assert "asmooth:" in mod.FRIEND_ALLOWED_CALLBACK_PREFIXES


# ── T-b: _animate_set_smooth флипает обе стороны ──────────────────────────────
def test_set_smooth_flips_both_ways():
    mod = _get_mod()
    mod._ANIMATE_PENDING[CHAT] = {"photo": "/x.jpg", "engine_mode": "spicy"}
    mod._animate_set_smooth(str(CHAT), True)
    assert mod._ANIMATE_PENDING[CHAT]["smooth"] is True
    mod._animate_set_smooth(str(CHAT), False)
    assert mod._ANIMATE_PENDING[CHAT]["smooth"] is False


# ── T-c: est на кнопке == cost + surcharge при smooth ON ──────────────────────
def test_keyboard_est_includes_surcharge_when_smooth_on():
    mod = _get_mod()
    from app.handlers.face_swap_handler import rife_surcharge_usd
    mod._ANIMATE_PENDING[CHAT] = {
        "photo": "/x.jpg", "engine_mode": "spicy",
        "seconds": 5, "resolution": "720p", "smooth": False,
    }
    val_off = _gen_button_usd(mod._animate_quality_keyboard(CHAT))
    mod._ANIMATE_PENDING[CHAT]["smooth"] = True
    val_on = _gen_button_usd(mod._animate_quality_keyboard(CHAT))
    assert val_on - val_off == pytest.approx(rife_surcharge_usd(1, 5), abs=0.005)


# ── T-d: check_limit-гейт включает surcharge при smooth ON (спай) ─────────────
def test_gate_includes_rife_surcharge_when_smooth_on(monkeypatch):
    mod = _get_mod()
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    sys.modules.setdefault("replicate", MagicMock())

    fake_handler = MagicMock()
    fake_handler.build_single_animate_request = MagicMock(return_value="REQ")
    monkeypatch.setattr(mod, "_swapbatch_get_handler", lambda: (fake_handler, None))

    limit_spy = MagicMock(return_value=(True, ""))
    import app.services.auth.access_control as _ac
    monkeypatch.setattr(_ac, "check_limit", limit_spy)

    # Остановить сразу после гейта (до воркер-треда) — как в T5-тесте качества.
    fake_lock = MagicMock()
    fake_lock.acquire.side_effect = GenerationLockBusy()
    monkeypatch.setattr(mod, "_get_video_lock", lambda: fake_lock)

    from app.services.block_m2_video.engines.capabilities import caps_for
    from app.handlers.face_swap_handler import rife_surcharge_usd
    caps = caps_for("spicy")
    expected = caps.cost_for(5, "720p") + rife_surcharge_usd(1, 5)

    mod._ANIMATE_PENDING[CHAT] = {
        "photo": "/x.jpg", "engine_mode": "spicy",
        "seconds": 5, "resolution": "720p", "smooth": True,
    }
    mod._animate_run_single(str(CHAT), "spicy")
    assert limit_spy.call_args.kwargs["estimated_usd"] == pytest.approx(expected)


# ── T-d': smooth OFF → гейт БЕЗ surcharge (мутация: всегда добавлять → падает) ─
def test_gate_no_surcharge_when_smooth_off(monkeypatch):
    mod = _get_mod()
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    sys.modules.setdefault("replicate", MagicMock())
    fake_handler = MagicMock()
    fake_handler.build_single_animate_request = MagicMock(return_value="REQ")
    monkeypatch.setattr(mod, "_swapbatch_get_handler", lambda: (fake_handler, None))
    limit_spy = MagicMock(return_value=(True, ""))
    import app.services.auth.access_control as _ac
    monkeypatch.setattr(_ac, "check_limit", limit_spy)
    fake_lock = MagicMock()
    fake_lock.acquire.side_effect = GenerationLockBusy()
    monkeypatch.setattr(mod, "_get_video_lock", lambda: fake_lock)

    from app.services.block_m2_video.engines.capabilities import caps_for
    caps = caps_for("spicy")
    mod._ANIMATE_PENDING[CHAT] = {
        "photo": "/x.jpg", "engine_mode": "spicy",
        "seconds": 5, "resolution": "720p", "smooth": False,
    }
    mod._animate_run_single(str(CHAT), "spicy")
    assert limit_spy.call_args.kwargs["estimated_usd"] == pytest.approx(
        caps.cost_for(5, "720p")
    )


# ── T-e: делегирование — smooth ON зовёт _interpolate_batch РОВНО раз ──────────
def _drive_worker_sync(mod, monkeypatch):
    """Прогнать воркер-тред синхронно + замокать движок/анимацию/доставку.
    Возвращает fake_handler (со спаем _interpolate_batch)."""
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_send_local_video", lambda *a, **k: None)
    sys.modules.setdefault("replicate", MagicMock())

    # синхронный Thread: .start() выполняет target немедленно
    class _SyncThread:
        def __init__(self, target=None, **k):
            self._t = target

        def start(self):
            self._t()
    monkeypatch.setattr("threading.Thread", _SyncThread)

    fake_handler = MagicMock()
    fake_handler.build_single_animate_request = MagicMock(return_value="REQ")
    fake_handler._interpolate_batch = AsyncMock(return_value=[Path("/smooth.mp4")])
    monkeypatch.setattr(mod, "_swapbatch_get_handler", lambda: (fake_handler, None))

    import app.services.auth.access_control as _ac
    monkeypatch.setattr(_ac, "check_limit", lambda *a, **k: (True, ""))

    fake_lock = MagicMock()
    fake_lock.acquire.return_value = "TOK"
    monkeypatch.setattr(mod, "_get_video_lock", lambda: fake_lock)

    # движок + анимация → отдать один готовый видос
    import app.services.block_m2_video.engines.router as _router
    fake_router = MagicMock()
    fake_router.select = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(_router, "EngineRouter", lambda *a, **k: fake_router)
    import app.services.block_m2_video.batch_animate as _ba
    monkeypatch.setattr(_ba, "animate_batch", AsyncMock(return_value=[Path("/out.mp4")]))
    return fake_handler


def test_smooth_on_calls_interpolate_batch(monkeypatch):
    mod = _get_mod()
    fake_handler = _drive_worker_sync(mod, monkeypatch)
    mod._ANIMATE_PENDING[CHAT] = {
        "photo": "/x.jpg", "engine_mode": "spicy",
        "seconds": 5, "resolution": "720p", "smooth": True,
    }
    mod._animate_run_single(str(CHAT), "spicy")
    assert fake_handler._interpolate_batch.await_count == 1
    call = fake_handler._interpolate_batch.await_args
    # делегат зовёт _interpolate_batch([video], seconds, chat, uname)
    assert call.args[0] == [Path("/out.mp4")]
    assert call.args[1] == 5


def test_smooth_off_never_calls_interpolate_batch(monkeypatch):
    mod = _get_mod()
    fake_handler = _drive_worker_sync(mod, monkeypatch)
    mod._ANIMATE_PENDING[CHAT] = {
        "photo": "/x.jpg", "engine_mode": "spicy",
        "seconds": 5, "resolution": "720p", "smooth": False,
    }
    mod._animate_run_single(str(CHAT), "spicy")
    assert fake_handler._interpolate_batch.await_count == 0


# ── T-f: RIFE упал → делегат отдаёт ОРИГИНАЛ без краша (money-safe) ────────────
def test_maybe_smooth_returns_original_on_rife_failure():
    mod = _get_mod()
    handler = MagicMock()
    handler._interpolate_batch = AsyncMock(side_effect=RuntimeError("RIFE down"))
    out, smoothed = mod._animate_maybe_smooth(handler, "/vid.mp4", 5, CHAT, "u")
    assert smoothed is False
    assert str(out) == str(Path("/vid.mp4"))   # оригинал доставлен, без исключения


def test_maybe_smooth_original_when_interpolate_returns_same():
    """Soft-fail внутри _interpolate_batch (вернул тот же объект, без raise):
    делегат должен report smoothed=False → в сообщении нет surcharge."""
    mod = _get_mod()
    handler = MagicMock()

    async def _fake(vids, *a, **k):
        return [vids[0]]          # тот же объект → НЕ сглажено

    handler._interpolate_batch = _fake
    out, smoothed = mod._animate_maybe_smooth(handler, "/vid.mp4", 5, CHAT, "u")
    assert smoothed is False


def test_maybe_smooth_returns_smoothed_on_success():
    mod = _get_mod()
    handler = MagicMock()
    handler._interpolate_batch = AsyncMock(return_value=[Path("/smooth.mp4")])
    out, smoothed = mod._animate_maybe_smooth(handler, "/vid.mp4", 5, CHAT, "u")
    assert smoothed is True
    assert str(out) == str(Path("/smooth.mp4"))
