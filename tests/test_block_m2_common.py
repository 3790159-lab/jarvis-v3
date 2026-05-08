# -*- coding: utf-8 -*-
"""Tests for M.2.0 — VideoStorage, GenerationHistory, VideoClientExtras."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.block_m2_video.generation_history import GenerationHistory, GenerationRecord
from app.services.block_m2_video.video_storage import GeneratedVideo, VideoStorage
from app.services.block_m2_video.video_client_extras import VideoClientExtras


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_video(
    video_id: str = "video_aaa00001",
    persona_id: str = "persona_test",
    parent_video_id: str | None = None,
    engine: str = "kling_v21",
    created_at: datetime | None = None,
) -> GeneratedVideo:
    return GeneratedVideo(
        video_id=video_id,
        persona_id=persona_id,
        source_photo_url="https://example.com/photo.jpg",
        video_url="https://replicate.delivery/out/vid.mp4",
        prompt="walking in the park",
        engine=engine,
        duration_sec=5,
        cost_usd=0.10,
        created_at=created_at or datetime(2026, 5, 6, 12, 0, 0),
        parent_video_id=parent_video_id,
    )


def _make_record(
    record_id: str = "rec_aaa00001",
    persona_id: str = "persona_test",
    kind: str = "photo",
    engine: str = "flux_lora",
    parent_record_id: str | None = None,
    created_at: datetime | None = None,
) -> dict:
    return dict(
        record_id=record_id,
        persona_id=persona_id,
        kind=kind,
        prompt="summer vibe",
        input_url=None,
        output_url="https://replicate.delivery/out/img.jpg",
        engine=engine,
        cost_usd=0.02,
        created_at=created_at or datetime(2026, 5, 6, 12, 0, 0),
        parent_record_id=parent_record_id,
    )


# ── VideoStorage tests ────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_save_and_get_video(tmp_path: Path):
    store = VideoStorage(storage_dir=tmp_path)
    vid = _make_video()
    await store.save(vid)
    result = await store.get(vid.video_id)
    assert result is not None
    assert result.video_id == vid.video_id
    assert result.persona_id == vid.persona_id
    assert result.engine == "kling_v21"


@pytest.mark.anyio
async def test_list_for_persona_returns_only_owner_videos(tmp_path: Path):
    store = VideoStorage(storage_dir=tmp_path)
    vid_a = _make_video(video_id="video_a1", persona_id="persona_alice")
    vid_b = _make_video(video_id="video_b1", persona_id="persona_bob")
    await store.save(vid_a)
    await store.save(vid_b)

    alice_vids = await store.list_for_persona("persona_alice")
    assert len(alice_vids) == 1
    assert alice_vids[0].video_id == "video_a1"

    bob_vids = await store.list_for_persona("persona_bob")
    assert len(bob_vids) == 1
    assert bob_vids[0].video_id == "video_b1"


@pytest.mark.anyio
async def test_list_redos_chains_correctly(tmp_path: Path):
    store = VideoStorage(storage_dir=tmp_path)
    original = _make_video(video_id="video_orig")
    redo1 = _make_video(video_id="video_redo1", parent_video_id="video_orig")
    redo2 = _make_video(video_id="video_redo2", parent_video_id="video_orig")
    unrelated = _make_video(video_id="video_other")

    for v in (original, redo1, redo2, unrelated):
        await store.save(v)

    redos = await store.list_redos("video_orig")
    redo_ids = {r.video_id for r in redos}
    assert redo_ids == {"video_redo1", "video_redo2"}
    assert "video_other" not in redo_ids
    assert "video_orig" not in redo_ids


@pytest.mark.anyio
async def test_delete_removes_video(tmp_path: Path):
    store = VideoStorage(storage_dir=tmp_path)
    vid = _make_video()
    await store.save(vid)
    assert await store.get(vid.video_id) is not None

    await store.delete(vid.video_id)
    assert await store.get(vid.video_id) is None


@pytest.mark.anyio
async def test_concurrent_saves_unique_ids(tmp_path: Path):
    store = VideoStorage(storage_dir=tmp_path)
    videos = [
        _make_video(video_id=f"video_{i:04d}", persona_id="persona_test")
        for i in range(10)
    ]
    await asyncio.gather(*(store.save(v) for v in videos))

    saved = await store.list_for_persona("persona_test")
    assert len(saved) == 10
    saved_ids = {v.video_id for v in saved}
    assert saved_ids == {f"video_{i:04d}" for i in range(10)}


@pytest.mark.anyio
async def test_persistence_survives_reload(tmp_path: Path):
    store1 = VideoStorage(storage_dir=tmp_path)
    vid = _make_video(video_id="video_persist")
    await store1.save(vid)

    store2 = VideoStorage(storage_dir=tmp_path)
    result = await store2.get("video_persist")
    assert result is not None
    assert result.prompt == vid.prompt
    assert result.cost_usd == vid.cost_usd


# ── GenerationHistory tests ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_record_returns_id(tmp_path: Path):
    hist = GenerationHistory(storage_dir=tmp_path)
    rec_id = await hist.record(**_make_record())
    assert rec_id == "rec_aaa00001"


@pytest.mark.anyio
async def test_list_recent_orders_by_created_at_desc(tmp_path: Path):
    hist = GenerationHistory(storage_dir=tmp_path)
    base = datetime(2026, 5, 6, 10, 0, 0)
    for i in range(5):
        await hist.record(**_make_record(
            record_id=f"rec_{i:04d}",
            created_at=base + timedelta(minutes=i),
        ))

    recent = await hist.list_recent("persona_test", limit=5)
    assert len(recent) == 5
    for a, b in zip(recent, recent[1:]):
        assert a.created_at >= b.created_at


@pytest.mark.anyio
async def test_redo_chain_finds_all_descendants(tmp_path: Path):
    hist = GenerationHistory(storage_dir=tmp_path)
    await hist.record(**_make_record(record_id="rec_root"))
    await hist.record(**_make_record(record_id="rec_child1", parent_record_id="rec_root"))
    await hist.record(**_make_record(record_id="rec_child2", parent_record_id="rec_root"))
    await hist.record(**_make_record(record_id="rec_grandchild", parent_record_id="rec_child1"))
    await hist.record(**_make_record(record_id="rec_unrelated"))

    chain = await hist.redo_chain("rec_root")
    chain_ids = {r.record_id for r in chain}
    assert chain_ids == {"rec_child1", "rec_child2", "rec_grandchild"}
    assert "rec_root" not in chain_ids
    assert "rec_unrelated" not in chain_ids


@pytest.mark.anyio
async def test_redo_does_not_delete_parent(tmp_path: Path):
    hist = GenerationHistory(storage_dir=tmp_path)
    await hist.record(**_make_record(record_id="rec_original"))
    await hist.record(**_make_record(record_id="rec_redo", parent_record_id="rec_original"))

    original = await hist.get("rec_original")
    redo = await hist.get("rec_redo")
    assert original is not None, "Original record must still exist after redo"
    assert redo is not None
    assert redo.parent_record_id == "rec_original"


@pytest.mark.anyio
async def test_record_for_unknown_persona_creates_dir(tmp_path: Path):
    hist = GenerationHistory(storage_dir=tmp_path)
    await hist.record(**_make_record(persona_id="persona_brand_new"))

    persona_dir = tmp_path / "persona_brand_new"
    assert persona_dir.exists()
    history_file = persona_dir / "history.jsonl"
    assert history_file.exists()


@pytest.mark.anyio
async def test_get_returns_none_for_unknown_id(tmp_path: Path):
    hist = GenerationHistory(storage_dir=tmp_path)
    result = await hist.get("rec_does_not_exist")
    assert result is None


# ── VideoClientExtras tests ───────────────────────────────────────────────────

def _make_extras(run_prediction_return="https://replicate.delivery/out/wan.mp4"):
    client = MagicMock()
    client._run_prediction = AsyncMock(return_value=run_prediction_return)
    client.generate_kling_v21 = AsyncMock(
        return_value={
            "video_url": "https://replicate.delivery/out/kling.mp4",
            "cost_usd": 0.10,
            "duration_sec": 5,
        }
    )
    return VideoClientExtras(client), client


@pytest.mark.anyio
async def test_wan22_fast_returns_expected_keys():
    extras, _ = _make_extras()
    result = await extras.generate_wan22_fast(
        "https://example.com/img.jpg", "dancing"
    )
    assert "video_url" in result
    assert "cost_usd" in result
    assert "duration_sec" in result
    assert result["video_url"] == "https://replicate.delivery/out/wan.mp4"
    assert result["duration_sec"] == 5


@pytest.mark.anyio
async def test_wan22_fast_cost_scales_with_duration():
    extras, _ = _make_extras()
    result_5 = await extras.generate_wan22_fast("https://example.com/img.jpg", "run", duration=5)
    result_10 = await extras.generate_wan22_fast("https://example.com/img.jpg", "run", duration=10)
    assert result_10["cost_usd"] == pytest.approx(result_5["cost_usd"] * 2)


@pytest.mark.anyio
async def test_video_dispatch_routes_to_kling():
    extras, client = _make_extras()
    result = await extras.generate_video_dispatch(
        "https://example.com/img.jpg", "flying", engine="kling_v21"
    )
    client.generate_kling_v21.assert_called_once()
    assert result["video_url"] == "https://replicate.delivery/out/kling.mp4"


@pytest.mark.anyio
async def test_video_dispatch_routes_to_wan22():
    extras, client = _make_extras()
    result = await extras.generate_video_dispatch(
        "https://example.com/img.jpg", "swimming", engine="wan22_fast"
    )
    client._run_prediction.assert_called_once()
    assert "wan" in result["video_url"]


@pytest.mark.anyio
async def test_video_dispatch_unknown_engine_raises():
    extras, _ = _make_extras()
    with pytest.raises(ValueError, match="Unknown video engine"):
        await extras.generate_video_dispatch(
            "https://example.com/img.jpg", "floating", engine="nonexistent_ai"
        )


@pytest.mark.anyio
async def test_kling_still_works_via_dispatcher():
    extras, client = _make_extras()
    result = await extras.generate_video_dispatch(
        "https://example.com/img.jpg", "posing", engine="kling_v21", duration=10
    )
    client.generate_kling_v21.assert_called_once_with(
        "https://example.com/img.jpg", "posing", 10
    )
    assert result["cost_usd"] == 0.10
