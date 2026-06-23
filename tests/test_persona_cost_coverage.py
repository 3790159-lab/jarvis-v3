# tests/test_persona_cost_coverage.py
# -*- coding: utf-8 -*-
"""Persona dual-write: me_swap_* must record to the PER-USER ledger too.

The friend daily-limit gate reads the per-user ledger
(``app.services.audit.cost_tracker``). The persona handlers previously only
wrote the GLOBAL fuse (``block_m_common.cost_tracker.log_expense``), so a
friend could spend past the limit via /me_swap_*. These tests pin the
per-user record alongside the (kept) global log_expense.
"""
from __future__ import annotations

import time

import pytest


class _Client:
    """Stub ReplicateVideoClient — returns a fixed-cost result offline."""

    async def generate_flux_with_lora(self, **kw):
        return {"cost_usd": 0.30, "image_url": "http://x/y.png"}


class _Data:
    is_trained = True
    lora_weights_url = "u"
    trigger_word = "t"
    persona_id = "p1"
    seed_photos = ["s.jpg"]


class _Mgr:
    async def get(self, cid):
        return _Data()


class _NoopTracker:
    """Stub CostTracker — keeps the global fuse out of the test's way."""

    def __init__(self, *a, **k):
        pass

    async def log_expense(self, *a, **k):
        return None


def _common_patches(ph, monkeypatch):
    monkeypatch.setattr(ph, "ReplicateVideoClient", lambda *a, **k: _Client())
    monkeypatch.setattr(
        "app.services.block_m22_fun.me_persona.MePersonaManager", _Mgr
    )
    monkeypatch.setattr(ph, "CostTracker", _NoopTracker)
    monkeypatch.setattr(ph, "_safe_send", lambda *a, **k: None)
    monkeypatch.setattr(ph, "_safe_send_photo", lambda *a, **k: None)


def test_persona_me_swap_photo_records_per_user_cost(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    import app.handlers.persona_handler as ph
    from app.services.audit import cost_tracker as ct

    _common_patches(ph, monkeypatch)

    ph.handle_me_swap_photo(555, "тест промт")
    time.sleep(0.5)  # worker thread

    assert ct.get_user_stats(555)["today"] == pytest.approx(0.30)


def test_persona_me_swap_video_records_per_user_cost(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    import app.handlers.persona_handler as ph
    from app.services.audit import cost_tracker as ct

    _common_patches(ph, monkeypatch)
    monkeypatch.setattr(ph, "_safe_send_video", lambda *a, **k: None)

    class _Pipeline:
        def __init__(self, client):
            pass

        async def swap_video(self, chat_id, url, data):
            return {"cost_usd": 0.30, "output_url": "http://x/out.mp4"}

    monkeypatch.setattr(
        "app.services.block_m22_fun.video_face_swap.VideoFaceSwapPipeline",
        _Pipeline,
    )

    ph.handle_me_swap_video(555, "http://x/in.mp4")
    time.sleep(0.5)  # worker thread

    assert ct.get_user_stats(555)["today"] == pytest.approx(0.30)
