# -*- coding: utf-8 -*-
"""Tests for M.2.3 — Polish: BatchGenerator, SeedCache, Analytics, and handlers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.block_m_common.cost_tracker import DailyLimitExceeded
from app.services.block_m23_polish.analytics import Analytics
from app.services.block_m23_polish.batch_generator import BatchGenerator
from app.services.block_m23_polish.seed_cache import SeedCache


# ── BatchGenerator tests ──────────────────────────────────────────────────────

def _make_photo_gen(
    image_url: str = "https://replicate.delivery/out/photo.jpg",
    cost_usd: float = 0.02,
    side_effect=None,
):
    photo_gen = MagicMock()
    if side_effect:
        photo_gen.generate_photo = AsyncMock(side_effect=side_effect)
    else:
        photo_gen.generate_photo = AsyncMock(
            return_value={
                "image_url": image_url,
                "cost_usd": cost_usd,
                "full_prompt": "sks_test prompt",
            }
        )
    return photo_gen


@pytest.mark.anyio
async def test_batch_generator_generates_n_photos():
    photo_gen = _make_photo_gen()
    batch = BatchGenerator(photo_gen)
    results = await batch.generate_batch("persona_test", "smiling", count=3)
    assert len(results) == 3
    assert photo_gen.generate_photo.await_count == 3


@pytest.mark.anyio
async def test_batch_generator_stops_on_daily_limit():
    calls = [
        {"image_url": "url1", "cost_usd": 0.02, "full_prompt": "p"},
        {"image_url": "url2", "cost_usd": 0.02, "full_prompt": "p"},
        DailyLimitExceeded("limit"),
    ]
    photo_gen = _make_photo_gen(side_effect=calls)
    batch = BatchGenerator(photo_gen)
    results = await batch.generate_batch("persona_test", "smiling", count=5)
    assert len(results) == 2


@pytest.mark.anyio
async def test_batch_generator_partial_result():
    photo_gen = _make_photo_gen(side_effect=DailyLimitExceeded("limit"))
    batch = BatchGenerator(photo_gen)
    results = await batch.generate_batch("persona_test", "smiling", count=3)
    assert results == []


@pytest.mark.anyio
async def test_batch_generator_progress_callback():
    photo_gen = _make_photo_gen()
    batch = BatchGenerator(photo_gen)
    cb = MagicMock()
    await batch.generate_batch("persona_test", "smiling", count=2, progress_cb=cb)
    assert cb.call_count == 2
    first_call = cb.call_args_list[0][0]
    assert first_call[0] == 1
    assert first_call[1] == 2


@pytest.mark.anyio
async def test_batch_generator_total_cost():
    photo_gen = _make_photo_gen(cost_usd=0.02)
    batch = BatchGenerator(photo_gen)
    results = await batch.generate_batch("persona_test", "smiling", count=3)
    total = sum(r["cost_usd"] for r in results)
    assert total == pytest.approx(0.06)


# ── SeedCache tests ───────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_seed_cache_cache_seed(tmp_path):
    cache = SeedCache(storage_dir=tmp_path)
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.content = b"fake_image_data"
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
            get=AsyncMock(return_value=mock_resp)
        ))
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        path = await cache.cache_seed("persona_test", "https://example.com/photo.jpg")

    assert Path(path).exists()
    assert Path(path).read_bytes() == b"fake_image_data"


def test_seed_cache_get_cached_seeds(tmp_path):
    cache = SeedCache(storage_dir=tmp_path)
    cache_dir = tmp_path / "persona_test" / "seeds_cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "abc123.jpg").write_bytes(b"img1")
    (cache_dir / "def456.jpg").write_bytes(b"img2")

    seeds = cache.get_cached_seeds("persona_test")
    assert len(seeds) == 2
    for s in seeds:
        assert Path(s).exists()


def test_seed_cache_is_cache_valid_missing_sentinel(tmp_path):
    cache = SeedCache(storage_dir=tmp_path)
    assert not cache.is_cache_valid("persona_test")


def test_seed_cache_is_cache_valid_fresh(tmp_path):
    cache = SeedCache(storage_dir=tmp_path)
    cache.touch_sentinel("persona_test")
    assert cache.is_cache_valid("persona_test", max_age_hours=24)


def test_seed_cache_invalidate(tmp_path):
    cache = SeedCache(storage_dir=tmp_path)
    cache.touch_sentinel("persona_test")
    assert cache.is_cache_valid("persona_test")
    cache.invalidate_cache("persona_test")
    assert not cache.is_cache_valid("persona_test")


# ── Analytics tests ───────────────────────────────────────────────────────────

def _make_analytics():
    tracker = MagicMock()
    history = MagicMock()

    tracker.get_stats = AsyncMock(return_value={
        "daily": 1.50,
        "weekly": 5.00,
        "monthly": 12.00,
        "total": 20.00,
        "by_operation": {"flux_lora_inference": 0.50, "kling_v21_video": 1.00},
    })
    tracker.check_limit = AsyncMock(return_value=(True, 8.50))
    tracker.get_total_for_persona = AsyncMock(return_value=2.50)
    history.list_recent = AsyncMock(return_value=[
        MagicMock(
            engine="kling_v21",
            cost_usd=0.12,
            created_at=datetime.now(timezone.utc),
        ),
        MagicMock(
            engine="wan22_fast",
            cost_usd=0.10,
            created_at=datetime.now(timezone.utc),
        ),
    ])
    return Analytics(tracker, history), tracker, history


@pytest.mark.anyio
async def test_analytics_daily_summary():
    analytics, tracker, _ = _make_analytics()
    with patch.object(analytics, "_count_today_records", AsyncMock(return_value=5)):
        summary = await analytics.daily_summary()
    assert summary["today_cost_usd"] == pytest.approx(1.50)
    assert summary["today_count"] == 5
    assert summary["remaining_budget_usd"] == pytest.approx(8.50)
    assert "by_operation" in summary
    assert "date" in summary


@pytest.mark.anyio
async def test_analytics_persona_summary():
    analytics, _, history = _make_analytics()
    summary = await analytics.persona_summary("persona_test")
    assert summary["persona_id"] == "persona_test"
    assert summary["generation_count"] == 2
    assert "kling_v21" in summary["by_engine"]
    assert "wan22_fast" in summary["by_engine"]


@pytest.mark.anyio
async def test_analytics_usage_trend_returns_n_days():
    analytics, _, _ = _make_analytics()
    trend = await analytics.usage_trend(days=7)
    assert len(trend) == 7
    for entry in trend:
        assert "date" in entry
        assert "cost_usd" in entry


# ── Handler integration tests ─────────────────────────────────────────────────

def _init_handler_m23():
    import app.handlers.persona_handler as ph
    send = MagicMock()
    send_photo = MagicMock()
    ph.init_bot(send, send_photo)
    return send, send_photo


def test_handle_costs_shows_summary():
    import app.handlers.persona_handler as ph
    send, _ = _init_handler_m23()

    with patch("app.handlers.persona_handler._run_async") as mock_run:
        mock_run.return_value = {
            "today_cost_usd": 1.50,
            "today_count": 5,
            "remaining_budget_usd": 8.50,
            "by_operation": {"flux": 0.50},
            "date": "2026-05-06",
        }
        ph.handle_costs(42)

    send.assert_called_once()
    msg = send.call_args[0][1]
    assert "1.5" in msg or "1,5" in msg or "$1.5000" in msg
    assert "8.5" in msg or "$8.5000" in msg


def test_handle_history_shows_records():
    import app.handlers.persona_handler as ph
    send, _ = _init_handler_m23()

    mock_records = [
        MagicMock(
            record_id="rec_001",
            kind="video",
            engine="kling_v21",
            cost_usd=0.12,
            prompt="walking",
            created_at=datetime(2026, 5, 6, 12, 0, 0),
        ),
    ]
    with patch("app.handlers.persona_handler._run_async", return_value=mock_records):
        ph.handle_history(42, "persona_test")

    send.assert_called_once()
    msg = send.call_args[0][1]
    assert "rec_001" in msg


def test_handle_history_no_persona_id():
    import app.handlers.persona_handler as ph
    send, _ = _init_handler_m23()
    ph.handle_history(42, "")
    send.assert_called_once()
    assert "persona_id" in send.call_args[0][1].lower() or "укажите" in send.call_args[0][1].lower()


def test_handle_persona_batch_invalid_count():
    import app.handlers.persona_handler as ph
    send, _ = _init_handler_m23()
    ph.handle_persona_batch(42, "persona_test abc smiling")
    send.assert_called_once()
    assert "числом" in send.call_args[0][1].lower()
