# -*- coding: utf-8 -*-
"""Задача 4 (TDD): route the per-photo custom flow into the SAME money-safe
batch runner (run_animate_batch_phase) instead of the stub.

Two testable seams (so the bot's threading shell stays thin glue):
  * build_custom_animate_reqs — zip photos × per-photo (prompt,negative) pairs
    into VideoRequests, order-locked (photo i → reqs[i]).
  * make_custom_animate_fn — an animate_fn(photos, cancel_check) closure that
    builds per-photo reqs and runs them through animate_batch (concurrency +
    429-sweep), the SAME mechanism as the default path.

Integration asserts the inherited money-safe guarantees on the custom path:
caps billing (no double swap), check_limit before spend, RIFE smooth, and
per-photo failure resilience.
"""
from pathlib import Path

import pytest
from PIL import Image

from app.handlers import face_swap_handler as fsh
from app.handlers.face_swap_handler import (
    FaceSwapHandler,
    build_custom_animate_reqs,
    make_custom_animate_fn,
)
from app.services.audit import cost_tracker as _cost
from app.services.block_m2_video.prompt_assembly import DEFAULT_MOTION
from app.services.block_m2_face_swap.batch_orchestrator import (
    BatchOrchestrator,
    STATE_AWAITING_CUSTOM_PROMPTS_CONFIRM,
    STATE_SWAP_DONE,
)


class _V:
    def count_faces(self, p):
        return 1


def _jpg(p: Path) -> Path:
    Image.new("RGB", (16, 16), (1, 2, 3)).save(p, "JPEG")
    return p


def _counter_ids():
    n = {"i": 0}

    def _f():
        v = f"gen_{n['i']}"
        n["i"] += 1
        return v
    return _f


# ── build_custom_animate_reqs (pure but for injected id_factory) ────────────────


def test_reqs_zip_order_and_fields():
    photos = [Path("p0.png"), Path("p1.png"), Path("p2.png")]
    pairs = [("a", "na"), ("b", "nb"), ("c", "nc")]
    reqs = build_custom_animate_reqs(
        photos, pairs, chat_id=42, seconds=7, resolution="1080p",
        enable_prompt_expansion=False, shot_type="single", id_factory=_counter_ids(),
    )
    assert len(reqs) == 3
    for i, r in enumerate(reqs):
        assert r.input_image_path == photos[i]      # photo i → reqs[i]
        assert r.prompt == pairs[i][0]
        assert r.negative_prompt == pairs[i][1]
        assert r.seconds == 7
        assert r.resolution == "1080p"
        assert r.enable_prompt_expansion is False
        assert r.shot_type == "single"
    # unique ids from the injected factory
    assert [r.generation_id for r in reqs] == ["gen_0", "gen_1", "gen_2"]


# ── make_custom_animate_fn (per-photo prompts → animate_batch) ──────────────────


@pytest.mark.asyncio
async def test_animate_fn_builds_per_photo_reqs_via_animate_batch():
    captured = {}

    async def fake_batch(engine, reqs, *, concurrency, progress_cb, cancel_check):
        captured["engine"] = engine
        captured["reqs"] = reqs
        captured["concurrency"] = concurrency
        return [Path(f"v{i}.mp4") for i in range(len(reqs))]

    sentinel_engine = object()
    fn = make_custom_animate_fn(
        engine=sentinel_engine,
        custom_prompts={1: "walk", 2: None, 3: "jump"},
        chat_id=42, seconds=5, resolution="720p", wardrobe="spicy",
        add_realism=False, add_negative=False,
        enable_prompt_expansion=True, shot_type=None,
        concurrency=2, progress_cb=None,
        id_factory=_counter_ids(), animate_batch_fn=fake_batch,
    )
    photos = [Path("a.png"), Path("b.png"), Path("c.png")]
    out = await fn(photos, lambda: False)

    assert captured["engine"] is sentinel_engine
    assert captured["concurrency"] == 2          # concurrency/429-sweep preserved
    reqs = captured["reqs"]
    assert reqs[0].prompt == "walk"
    assert reqs[1].prompt == DEFAULT_MOTION       # photo 2 had no prompt
    assert reqs[2].prompt == "jump"
    assert [r.input_image_path for r in reqs] == photos
    assert len(out) == 3


@pytest.mark.asyncio
async def test_animate_fn_apply_first_ignores_extra_prompts():
    captured = {}

    async def fake_batch(engine, reqs, *, concurrency, progress_cb, cancel_check):
        captured["reqs"] = reqs
        return [Path(f"v{i}.mp4") for i in range(len(reqs))]

    fn = make_custom_animate_fn(
        engine=object(), custom_prompts={1: "a", 2: "b", 3: "c", 4: "d"},
        chat_id=42, seconds=5, resolution="720p", wardrobe="spicy",
        add_realism=False, add_negative=False, enable_prompt_expansion=True,
        shot_type=None, concurrency=2, id_factory=_counter_ids(),
        animate_batch_fn=fake_batch,
    )
    await fn([Path("a.png"), Path("b.png")], lambda: False)  # only 2 photos
    reqs = captured["reqs"]
    assert len(reqs) == 2                          # extra prompts 3,4 ignored
    assert [r.prompt for r in reqs] == ["a", "b"]


@pytest.mark.asyncio
async def test_animate_fn_apply_partial_defaults_missing():
    captured = {}

    async def fake_batch(engine, reqs, *, concurrency, progress_cb, cancel_check):
        captured["reqs"] = reqs
        return [Path(f"v{i}.mp4") for i in range(len(reqs))]

    fn = make_custom_animate_fn(
        engine=object(), custom_prompts={1: "a"},   # too few
        chat_id=42, seconds=5, resolution="720p", wardrobe="spicy",
        add_realism=False, add_negative=False, enable_prompt_expansion=True,
        shot_type=None, concurrency=2, id_factory=_counter_ids(),
        animate_batch_fn=fake_batch,
    )
    await fn([Path(f"{i}.png") for i in range(3)], lambda: False)
    reqs = captured["reqs"]
    assert reqs[0].prompt == "a"
    assert reqs[1].prompt == DEFAULT_MOTION
    assert reqs[2].prompt == DEFAULT_MOTION


# ── integration: custom flow through run_animate_batch_phase ────────────────────


@pytest.fixture
def recorded(monkeypatch):
    calls = []
    monkeypatch.setattr(_cost, "record_cost",
                        lambda uid, uname, amt: calls.append(amt))
    return calls


@pytest.fixture(autouse=True)
def _allow_limit(monkeypatch):
    monkeypatch.setattr(fsh, "check_limit", lambda uid, *, estimated_usd: (True, ""))


def _seed_confirm(tmp_path, *, n=3, engine="spicy", duration=5, res="720p",
                  smooth=False, custom=None):
    orch = BatchOrchestrator(state_root=tmp_path / "b", validator=_V())
    h = FaceSwapHandler(orchestrator=orch)
    orch.begin_source(42)
    orch.submit_source(42, _jpg(tmp_path / "s.jpg"))
    orch.begin_targets(42)
    orch.add_targets(42, [_jpg(tmp_path / f"t{i}.jpg") for i in range(n)])
    sess = orch.get(42)
    sess.status = STATE_SWAP_DONE
    for i, t in enumerate(sess.targets):
        t.swap_result_path = str(_jpg(tmp_path / f"sw{i}.png"))
    sess.duration_sec = duration
    sess.resolution = res
    sess.video_engine = engine
    sess.smooth_enabled = smooth
    sess.custom_prompts = custom
    sess.status = STATE_AWAITING_CUSTOM_PROMPTS_CONFIRM
    return h, orch, sess


def _custom_fn(sess, *, captured=None, fail_idx=(), n_make=None):
    async def fake_batch(engine, reqs, *, concurrency, progress_cb, cancel_check):
        if captured is not None:
            captured["reqs"] = reqs
        return [
            None if i in fail_idx else Path(f"vid_{i}.mp4")
            for i in range(len(reqs))
        ]
    return make_custom_animate_fn(
        engine=object(), custom_prompts=sess.custom_prompts, chat_id=42,
        seconds=sess.duration_sec, resolution=sess.resolution, wardrobe="safe",
        add_realism=True, add_negative=True, enable_prompt_expansion=True,
        shot_type=None, concurrency=2, id_factory=_counter_ids(),
        animate_batch_fn=fake_batch,
    )


@pytest.mark.asyncio
async def test_custom_confirm_delivers_videos_each_own_prompt(tmp_path, recorded):
    h, orch, sess = _seed_confirm(
        tmp_path, n=3, custom={1: "walk", 2: "dance", 3: "jump"})
    captured = {}
    fn = _custom_fn(sess, captured=captured)
    reply = await h.run_animate_batch_phase(42, fn, user_id=7, username="u")

    assert len(reply.videos) == 3
    reqs = captured["reqs"]
    # add_realism=True here → realism suffix appended; the motion still leads
    assert reqs[0].prompt.startswith("walk")
    assert reqs[1].prompt.startswith("dance")
    assert reqs[2].prompt.startswith("jump")


@pytest.mark.asyncio
async def test_custom_billing_caps_no_double_swap(tmp_path, recorded, monkeypatch):
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    h, orch, sess = _seed_confirm(
        tmp_path, n=2, custom={1: "a", 2: "b"}, duration=5, res="720p")
    fn = _custom_fn(sess)
    await h.run_animate_batch_phase(42, fn, user_id=7, username="u")

    from app.services.block_m2_video.engines.capabilities import caps_for
    caps_amount = round(2 * caps_for("spicy").cost_for(5, "720p"), 2)
    # billed ONLY the caps animate cost — NOT swap again, NOT flat 0.27
    assert recorded == [pytest.approx(caps_amount)]
    assert recorded[0] != pytest.approx(2 * (0.02 + 0.27))


@pytest.mark.asyncio
async def test_custom_check_limit_blocks_before_spend(tmp_path, monkeypatch):
    monkeypatch.setattr(
        fsh, "check_limit", lambda uid, *, estimated_usd: (False, "лимит исчерпан"))
    h, orch, sess = _seed_confirm(tmp_path, n=2, custom={1: "a", 2: "b"})
    called = {"gen": False}

    async def fake_batch(engine, reqs, *, concurrency, progress_cb, cancel_check):
        called["gen"] = True
        return [Path("v.mp4")]
    fn = make_custom_animate_fn(
        engine=object(), custom_prompts=sess.custom_prompts, chat_id=42,
        seconds=5, resolution="720p", wardrobe="safe", add_realism=True,
        add_negative=True, enable_prompt_expansion=True, shot_type=None,
        concurrency=2, id_factory=_counter_ids(), animate_batch_fn=fake_batch,
    )
    reply = await h.run_animate_batch_phase(42, fn, user_id=555, username="petya")
    assert called["gen"] is False              # generation never started
    assert "лимит" in reply.text.lower()


@pytest.mark.asyncio
async def test_custom_smooth_on_delivers_smooth_and_bills_rife(tmp_path, recorded, monkeypatch):
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    h, orch, sess = _seed_confirm(
        tmp_path, n=2, custom={1: "a", 2: "b"}, duration=10, smooth=True)

    class _FakeRife:
        async def interpolate(self, video, *, num_frames=1):
            assert num_frames == 1
            return Path(video).with_name(f"{Path(video).stem}.smooth.mp4")
    monkeypatch.setattr(h, "_make_rife_client", lambda: _FakeRife())

    fn = _custom_fn(sess)
    reply = await h.run_animate_batch_phase(42, fn, user_id=7, username="u")

    assert reply.videos
    assert all(v.name.endswith(".smooth.mp4") for v in reply.videos)
    # caps animate + RIFE surcharge both billed
    assert len(recorded) == 2
    assert recorded[1] == pytest.approx(2 * 10 * 0.01)


@pytest.mark.asyncio
async def test_custom_per_photo_failure_money_safe(tmp_path, recorded, monkeypatch):
    monkeypatch.delenv("SWAPBATCH_RIFE_USD_PER_SEC", raising=False)
    h, orch, sess = _seed_confirm(
        tmp_path, n=3, custom={1: "a", 2: "b", 3: "c"}, duration=5, res="720p")
    fn = _custom_fn(sess, fail_idx={1})        # middle video fails in the engine
    reply = await h.run_animate_batch_phase(42, fn, user_id=7, username="u")

    # 2 of 3 delivered, batch did not crash
    assert len(reply.videos) == 2
    from app.services.block_m2_video.engines.capabilities import caps_for
    assert recorded == [pytest.approx(round(2 * caps_for("spicy").cost_for(5, "720p"), 2))]
