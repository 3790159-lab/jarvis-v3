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
    # me_swap теперь за пре-гейтом check_limit (Арка 1 T7); эти тесты проверяют
    # запись фактической стоимости — пропускаем гейт (allow). Сам гейт — в
    # tests/test_money_gate_persona.py.
    monkeypatch.setattr(ph, "check_limit", lambda uid, estimated_usd: (True, ""), raising=False)
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


# ── friend persona commands (/persona_photo, /persona_batch) ──────────────────


def _patch_persona_storage(ph, monkeypatch):
    # persona_photo/batch теперь за пре-гейтом check_limit (money-consolidation
    # hole a); эти тесты проверяют факт-запись, поэтому гейт открыт.
    monkeypatch.setattr(ph, "check_limit", lambda uid, estimated_usd: (True, ""), raising=False)
    monkeypatch.setattr(ph, "ReplicateVideoClient", lambda *a, **k: object())
    monkeypatch.setattr(ph, "CostTracker", _NoopTracker)
    monkeypatch.setattr(ph, "PersonaStorage", lambda *a, **k: object())
    monkeypatch.setattr(ph, "_safe_send", lambda *a, **k: None)
    monkeypatch.setattr(ph, "_safe_send_photo", lambda *a, **k: None)
    monkeypatch.setattr(ph, "setup_block_m_logging", lambda *a, **k: None)


def test_persona_photo_records_per_user_cost(tmp_path, monkeypatch):
    """Gap: /persona_photo → PhotoGenerator only wrote global; now per-user too."""
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    import app.handlers.persona_handler as ph
    from app.services.audit import cost_tracker as ct

    _patch_persona_storage(ph, monkeypatch)

    class _PhotoGen:
        def __init__(self, *a, **k):
            pass

        async def generate_photo(self, persona_id, prompt):
            return {
                "cost_usd": 0.05,
                "image_url": "http://x/p.png",
                "full_prompt": prompt,
            }

    monkeypatch.setattr(
        "app.services.block_m1_persona.photo_generator.PhotoGenerator", _PhotoGen
    )

    ph.handle_persona_photo(555, "p1 нарисуй кота")
    time.sleep(0.5)  # worker thread

    assert ct.get_user_stats(555)["today"] == pytest.approx(0.05)


def test_persona_batch_records_per_user_cost(tmp_path, monkeypatch):
    """Gap: /persona_batch computes total_cost but recorded nothing per-user."""
    monkeypatch.setenv("JARVIS_COST_FILE", str(tmp_path / "cost.json"))
    import app.handlers.persona_handler as ph
    from app.services.audit import cost_tracker as ct

    _patch_persona_storage(ph, monkeypatch)

    class _PhotoGen:
        def __init__(self, *a, **k):
            pass

    class _Batch:
        def __init__(self, *a, **k):
            pass

        async def generate_batch(self, persona_id, prompt, count, progress_cb=None):
            # 3 photos at $0.05 each → $0.15 total
            return [{"cost_usd": 0.05} for _ in range(3)]

    monkeypatch.setattr(
        "app.services.block_m1_persona.photo_generator.PhotoGenerator", _PhotoGen
    )
    monkeypatch.setattr(
        "app.services.block_m23_polish.batch_generator.BatchGenerator", _Batch
    )

    ph.handle_persona_batch(555, "p1 3 нарисуй кота")
    time.sleep(0.5)  # worker thread

    assert ct.get_user_stats(555)["today"] == pytest.approx(0.15)
