# -*- coding: utf-8 -*-
"""T6: эмодзи кнопки движка в /videoref-клавиатуре ≠ эмодзи длительностей.

Раньше и кнопки длины (🎬/⭐), и кнопка движка (🎬) использовали 🎬 → визуально
сливались. Движок теперь ⚙️, длительности остаются 🎬/⭐.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CHAT = 333


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_ALLOWED_USER_IDS", str(CHAT))
    monkeypatch.setenv("JARVIS_ADMIN_USER_ID", str(CHAT))
    monkeypatch.setenv("JARVIS_AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    monkeypatch.setenv("JARVIS_ROUTER_ENABLED", "0")


def _get_mod():
    mod_name = f"_test_vref_emoji_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_engine_button_emoji_distinct_from_durations():
    mod = _get_mod()
    mod._VIDEOREF_SWAP_PENDING[CHAT] = {
        "best_frame": "b.jpg", "motion_prompt": "m",
        "seconds": 5, "smooth": False, "engine_mode": "spicy",
        "wardrobe_mode": "preserve",
    }
    rows = mod._videoref_duration_keyboard(CHAT)
    engine_btns = [b for row in rows for b in row
                   if b["callback_data"].startswith("vref:eng:")]
    dur_btns = [b for row in rows for b in row
                if b["callback_data"].startswith("vref:sa:")]
    assert engine_btns, "нет кнопки движка"
    assert dur_btns, "нет кнопок длины"
    # Движок теперь ⚙️, не 🎬
    assert engine_btns[0]["text"].startswith("⚙️")
    assert "🎬" not in engine_btns[0]["text"]
    # Длительности остаются 🎬/⭐
    for b in dur_btns:
        assert b["text"][0] in ("🎬", "⭐")
