from pathlib import Path

import pytest
from PIL import Image

from app.services.block_m2_face_swap.batch_orchestrator import (
    BatchOrchestrator, STATE_DONE,
)


class _V:
    def count_faces(self, p): return 1


def _jpg(p: Path) -> Path:
    Image.new("RGB", (16, 16), (1, 2, 3)).save(p, "JPEG"); return p


def _orch(tmp_path):
    return BatchOrchestrator(state_root=tmp_path / "b", validator=_V())


async def _seed_swapped(orch, chat, tmp_path):
    orch.begin_source(chat); orch.submit_source(chat, _jpg(tmp_path / "s.jpg"))
    orch.begin_targets(chat)
    orch.add_targets(chat, [_jpg(tmp_path / "t0.jpg"), _jpg(tmp_path / "t1.jpg")])
    async def _swap(src, tgts, cc): return [_jpg(tmp_path / "r0.jpg"), _jpg(tmp_path / "r1.jpg")]
    await orch.confirm_swap(chat, swap_fn=_swap)


@pytest.mark.asyncio
async def test_session_video_defaults(tmp_path):
    orch = _orch(tmp_path); chat = 1
    await _seed_swapped(orch, chat, tmp_path)
    sess = orch.get(chat)
    assert sess.video_engine == "spicy"
    assert sess.resolution == "720p"
    assert sess.motion_prompt == ""


@pytest.mark.asyncio
async def test_confirm_animate_batch_records_videos(tmp_path):
    orch = _orch(tmp_path); chat = 1
    await _seed_swapped(orch, chat, tmp_path)

    async def _animate_fn(photos, cancel_check):
        return [tmp_path / "v0.mp4", None]   # one ok, one failed

    out = await orch.confirm_animate_batch(chat, animate_fn=_animate_fn)
    sess = orch.get(chat)
    assert sess.status == STATE_DONE
    swapped = [t for t in sess.targets if t.swap_result_path]
    assert swapped[0].animate_result_path == str(tmp_path / "v0.mp4")
    assert swapped[1].animate_result_path is None


def test_parse_animate_quality_spicy_ok():
    from app.services.block_m2_face_swap.quality_settings import parse_animate_quality
    out = parse_animate_quality("duration=15 resolution=1080p", engine_mode="spicy")
    assert out == {"duration": 15, "resolution": "1080p"}


def test_parse_animate_quality_rejects_unsupported_for_engine():
    from app.services.block_m2_face_swap.quality_settings import (
        parse_animate_quality, QualityError,
    )
    # seedance has no 15s
    with pytest.raises(QualityError):
        parse_animate_quality("duration=15", engine_mode="seedance")
    # wavespeed has no 480p
    with pytest.raises(QualityError):
        parse_animate_quality("resolution=480p", engine_mode="spicy")


def test_parse_animate_quality_empty_returns_empty():
    from app.services.block_m2_face_swap.quality_settings import parse_animate_quality
    assert parse_animate_quality("", engine_mode="spicy") == {}


def test_animate_cost_estimate_math():
    from app.handlers.face_swap_handler import animate_cost_estimate
    # 2 photos, spicy, 10s, 720p -> 2 * 1.00 = $2.00
    est = animate_cost_estimate(swapped_count=2, seconds=10, resolution="720p", engine_mode="spicy")
    assert est["total_usd"] == pytest.approx(2.00)
    assert est["per_usd"] == pytest.approx(1.00)
    assert est["count"] == 2
    assert est["minutes"] > 0


@pytest.mark.asyncio
async def test_handle_animate_yes_shows_cost_does_not_run(tmp_path):
    from app.handlers.face_swap_handler import FaceSwapHandler
    orch = _orch(tmp_path); chat = 7
    await _seed_swapped(orch, chat, tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    reply = h.handle_animate_yes(chat)
    assert reply.text is not None
    assert "$" in reply.text
    assert "/swapbatch_animate_go" in reply.text
    assert orch.status(chat) == "SWAP_DONE"   # NOT run


@pytest.mark.asyncio
async def test_handle_set_prompt_stores_shared_prompt(tmp_path):
    from app.handlers.face_swap_handler import FaceSwapHandler
    orch = _orch(tmp_path); chat = 8
    await _seed_swapped(orch, chat, tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    reply = h.handle_set_prompt(chat, "slow dance, neon light")
    assert "slow dance" in (orch.get(chat).motion_prompt)
    assert reply.text is not None


@pytest.mark.asyncio
async def test_handle_set_animate_quality_engine_constrained(tmp_path):
    from app.handlers.face_swap_handler import FaceSwapHandler
    orch = _orch(tmp_path); chat = 11
    await _seed_swapped(orch, chat, tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    # spicy (default engine): 15s + 1080p both allowed
    h.handle_set_animate_quality(chat, "duration=15 resolution=1080p")
    assert orch.get(chat).duration_sec == 15
    assert orch.get(chat).resolution == "1080p"
    # spicy rejects 480p -> warning, resolution unchanged
    r2 = h.handle_set_animate_quality(chat, "resolution=480p")
    assert "⚠️" in r2.text
    assert orch.get(chat).resolution == "1080p"


@pytest.mark.asyncio
async def test_handle_set_animate_quality_no_args_shows_current(tmp_path):
    from app.handlers.face_swap_handler import FaceSwapHandler
    orch = _orch(tmp_path); chat = 12
    await _seed_swapped(orch, chat, tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    r = h.handle_set_animate_quality(chat, "")
    assert r.text is not None
    assert "fps" in r.text.lower()   # shows native fps as info-only
