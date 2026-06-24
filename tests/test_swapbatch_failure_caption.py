# -*- coding: utf-8 -*-
"""TDD: run_animate_batch_phase must surface the WHY of per-frame failures.

Friend (role=friend) sees a grouped friendly reason ("цензура...", no raw codes);
admin (role=admin) sees concrete per-index raw errors. Money-safe is unchanged:
failed frames are never billed. The reason dict is the same object the bot fills
via animate_batch's errors_out and hands to run_animate_batch_phase.
"""
from pathlib import Path

import pytest
from PIL import Image

from app.handlers import face_swap_handler as fsh
from app.handlers.face_swap_handler import FaceSwapHandler, make_custom_animate_fn
from app.services.audit import cost_tracker as _cost
from app.services.block_m2_face_swap.batch_orchestrator import (
    BatchOrchestrator,
    STATE_SWAP_DONE,
)

_E005 = ("prediction failed: The input or output was flagged as sensitive. "
         "Please try again with different inputs. (E005) (uIJ6l3ruRD)")


class _V:
    def count_faces(self, p):
        return 1


def _jpg(p: Path) -> Path:
    Image.new("RGB", (16, 16), (1, 2, 3)).save(p, "JPEG")
    return p


@pytest.fixture
def recorded(monkeypatch):
    calls = []
    monkeypatch.setattr(_cost, "record_cost",
                        lambda uid, uname, amt: calls.append(amt))
    return calls


@pytest.fixture(autouse=True)
def _allow_limit(monkeypatch):
    monkeypatch.setattr(fsh, "check_limit", lambda uid, *, estimated_usd: (True, ""))


def _seed(tmp_path, *, n=2, engine="seedance"):
    orch = BatchOrchestrator(state_root=tmp_path / "b", validator=_V())
    h = FaceSwapHandler(orchestrator=orch)
    orch.begin_source(42)
    orch.submit_source(42, _jpg(tmp_path / "s.jpg"))
    orch.begin_targets(42)
    orch.add_targets(42, [_jpg(tmp_path / f"t{i}.jpg") for i in range(n)])
    sess = orch.get(42)
    for i, t in enumerate(sess.targets):
        t.swap_result_path = str(_jpg(tmp_path / f"sw{i}.png"))
    sess.duration_sec = 5
    sess.resolution = "720p"
    sess.video_engine = engine
    sess.smooth_enabled = False
    sess.status = STATE_SWAP_DONE
    return h, orch, sess


def _all_fail_fn(n):
    async def fn(photos, cancel_check):
        return [None] * n
    return fn


@pytest.mark.asyncio
async def test_friend_sees_friendly_censorship_reason(tmp_path, recorded, monkeypatch):
    monkeypatch.setattr(fsh, "get_role", lambda uid: "friend")
    h, orch, sess = _seed(tmp_path, n=2, engine="seedance")
    errs = {0: _E005, 1: _E005}
    reply = await h.run_animate_batch_phase(
        42, _all_fail_fn(2), user_id=545893540, username="Artem",
        animate_errors=errs,
    )
    assert "цензур" in reply.text.lower()       # friend learns it was censorship
    assert "E005" not in reply.text             # no raw codes for the friend
    assert recorded == []                        # money-safe: nothing billed


@pytest.mark.asyncio
async def test_admin_sees_concrete_per_index_errors(tmp_path, recorded, monkeypatch):
    monkeypatch.setattr(fsh, "get_role", lambda uid: "admin")
    h, orch, sess = _seed(tmp_path, n=2, engine="seedance")
    errs = {0: _E005, 1: _E005}
    reply = await h.run_animate_batch_phase(
        42, _all_fail_fn(2), user_id=7, username="admin",
        animate_errors=errs,
    )
    assert "#1" in reply.text and "#2" in reply.text   # concrete indices
    assert "E005" in reply.text                         # raw kept for admin
    assert recorded == []


@pytest.mark.asyncio
async def test_partial_failure_friendly_and_still_bills_successes(tmp_path, recorded, monkeypatch):
    monkeypatch.setattr(fsh, "get_role", lambda uid: "friend")
    h, orch, sess = _seed(tmp_path, n=3, engine="seedance")

    async def fn(photos, cancel_check):
        return [Path("vid0.mp4"), None, Path("vid2.mp4")]  # idx1 censored

    errs = {1: _E005}
    reply = await h.run_animate_batch_phase(
        42, fn, user_id=545893540, username="Artem", animate_errors=errs,
    )
    assert len(reply.videos) == 2
    assert "цензур" in reply.text.lower()
    # billed only the 2 successes, never the failed frame
    from app.services.block_m2_video.engines.capabilities import caps_for
    assert recorded == [pytest.approx(round(2 * caps_for("seedance").cost_for(5, "720p"), 2))]


@pytest.mark.asyncio
async def test_no_errors_dict_falls_back_to_bare_count(tmp_path, recorded, monkeypatch):
    monkeypatch.setattr(fsh, "get_role", lambda uid: "friend")
    h, orch, sess = _seed(tmp_path, n=2, engine="seedance")
    reply = await h.run_animate_batch_phase(
        42, _all_fail_fn(2), user_id=545893540, username="Artem",
    )  # animate_errors omitted -> legacy bare line
    assert "не удалось" in reply.text.lower()


# ── make_custom_animate_fn threads the shared errors_out dict ─────────────────

@pytest.mark.asyncio
async def test_make_custom_animate_fn_threads_errors_out():
    errs: dict[int, str] = {}
    captured = {}

    async def fake_batch(engine, reqs, *, concurrency, progress_cb, cancel_check, errors_out):
        captured["errors_out"] = errors_out
        errors_out[0] = "boom (E005)"
        return [None for _ in reqs]

    fn = make_custom_animate_fn(
        engine=object(), custom_prompts={1: "a"}, chat_id=42, seconds=5,
        resolution="720p", wardrobe="safe", add_realism=False, add_negative=False,
        enable_prompt_expansion=True, shot_type=None, concurrency=2,
        animate_batch_fn=fake_batch, errors_out=errs,
    )
    await fn([Path("a.png")], lambda: False)
    assert captured["errors_out"] is errs       # same object threaded down
    assert errs[0] == "boom (E005)"


@pytest.mark.asyncio
async def test_make_custom_animate_fn_omits_errors_out_when_none():
    # back-compat: when no errors_out given, the kwarg is NOT passed, so a fake
    # batch with the OLD signature keeps working.
    async def fake_batch(engine, reqs, *, concurrency, progress_cb, cancel_check):
        return [Path("v.mp4") for _ in reqs]

    fn = make_custom_animate_fn(
        engine=object(), custom_prompts={1: "a"}, chat_id=42, seconds=5,
        resolution="720p", wardrobe="safe", add_realism=False, add_negative=False,
        enable_prompt_expansion=True, shot_type=None, concurrency=2,
        animate_batch_fn=fake_batch,
    )
    out = await fn([Path("a.png")], lambda: False)
    assert out == [Path("v.mp4")]
