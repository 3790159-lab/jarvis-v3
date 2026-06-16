# -*- coding: utf-8 -*-
"""Integration: the bot's shared video lock marks the swap sentinel.

All three long-running video flows (persona_video, video_face_swap, swapbatch)
acquire ``_get_video_lock()``. Wiring the sentinel onto that single lock means
every swap marks ``state/swap_active`` for the watchdog, with no per-site code.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.block_m2_video import swap_sentinel


def _get_mod():
    mod_name = f"_test_sentinel_bot_{id(object())}"
    spec = importlib.util.spec_from_file_location(
        mod_name, ROOT / "tools" / "jarvis_smart_telegram_control.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_get_video_lock_wires_swap_sentinel_callbacks():
    mod = _get_mod()
    mod._video_lock = None  # ensure a fresh singleton
    lock = mod._get_video_lock()
    assert lock._on_first_acquire is swap_sentinel.mark_swap_start
    assert lock._on_last_release is swap_sentinel.mark_swap_end
