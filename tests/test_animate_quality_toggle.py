# -*- coding: utf-8 -*-
"""T5: тогглы качества/длины для одиночного /animate (порт batch sbq: → aq:).

После выбора движка показываются caps-aware кнопки длины/разрешения (prefix aq:);
выбор пишется в _ANIMATE_PENDING; генерация читает pend seconds/resolution
(quote==charge). Префикс aq: — во FRIEND_ALLOWED (иначе друг не нажмёт).
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
    mod_name = f"_test_anim_quality_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_quality_keyboard_is_caps_aware():
    mod = _get_mod()
    mod._ANIMATE_PENDING[CHAT] = {"photo": "/x.jpg", "engine_mode": "spicy"}
    rows = mod._animate_quality_keyboard(CHAT)
    callbacks = [b["callback_data"] for row in rows for b in row]
    # spicy caps: durations 5/10/15, resolutions 720p/1080p
    assert "aq:dur:5" in callbacks
    assert "aq:dur:15" in callbacks
    assert "aq:res:720p" in callbacks
    assert "aq:res:1080p" in callbacks
    assert "aq:done" in callbacks
    # seedance-only 480p must NOT appear for a spicy session
    assert "aq:res:480p" not in callbacks


def test_set_quality_updates_pending():
    mod = _get_mod()
    mod._ANIMATE_PENDING[CHAT] = {"photo": "/x.jpg", "engine_mode": "spicy"}
    mod._animate_set_quality(str(CHAT), "dur", "15")
    mod._animate_set_quality(str(CHAT), "res", "1080p")
    assert mod._ANIMATE_PENDING[CHAT]["seconds"] == 15
    assert mod._ANIMATE_PENDING[CHAT]["resolution"] == "1080p"


def test_pick_engine_stores_engine_and_shows_quality(monkeypatch):
    mod = _get_mod()
    kb_shown = {}
    monkeypatch.setattr(mod, "send_with_keyboard",
                        lambda cid, txt, kb, *a, **k: kb_shown.setdefault("kb", kb))
    mod._ANIMATE_PENDING[CHAT] = {"photo": "/x.jpg"}
    mod._animate_pick_engine(str(CHAT), "spicy")
    assert mod._ANIMATE_PENDING[CHAT]["engine_mode"] == "spicy"
    callbacks = [b["callback_data"] for row in kb_shown["kb"] for b in row]
    assert "aq:done" in callbacks


def test_aq_prefix_is_friend_allowed():
    mod = _get_mod()
    assert "aq:" in mod.FRIEND_ALLOWED_CALLBACK_PREFIXES


def test_generation_uses_pending_quality(monkeypatch):
    """quote==charge зуб: check_limit и build_single_animate_request читают
    ВЫБРАННЫЕ pend seconds/resolution (15с/1080p), а не caps-дефолты (10с/720p).
    Мутация: вернуть caps.default_* → падает."""
    mod = _get_mod()
    monkeypatch.setattr(mod, "send", lambda *a, **k: None)
    sys.modules.setdefault("replicate", MagicMock())

    build_spy = MagicMock(return_value="REQ")
    fake_handler = MagicMock()
    fake_handler.build_single_animate_request = build_spy
    monkeypatch.setattr(mod, "_swapbatch_get_handler", lambda: (fake_handler, None))

    limit_spy = MagicMock(return_value=(True, ""))
    import app.services.auth.access_control as _ac
    monkeypatch.setattr(_ac, "check_limit", limit_spy)

    fake_lock = MagicMock()
    fake_lock.acquire.side_effect = GenerationLockBusy()
    monkeypatch.setattr(mod, "_get_video_lock", lambda: fake_lock)

    from app.services.block_m2_video.engines.capabilities import caps_for
    caps = caps_for("spicy")
    expected_est = caps.cost_for(15, "1080p")

    mod._ANIMATE_PENDING[CHAT] = {
        "photo": "/x.jpg", "engine_mode": "spicy",
        "seconds": 15, "resolution": "1080p",
    }
    mod._animate_run_single(str(CHAT), "spicy")

    assert build_spy.call_args.kwargs["seconds"] == 15
    assert build_spy.call_args.kwargs["resolution"] == "1080p"
    assert limit_spy.call_args.kwargs["estimated_usd"] == pytest.approx(expected_est)
