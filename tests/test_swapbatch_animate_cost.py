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
