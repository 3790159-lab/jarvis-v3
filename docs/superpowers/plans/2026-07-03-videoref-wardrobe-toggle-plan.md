# /videoref Wardrobe Toggle ("Не раздевать") — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> to implement this plan task-by-task, two-stage review (spec compliance, then code
> quality) after each task. Two hard checkpoints — end of Task 1 (RED), end of Task 5
> (final regression sweep). STOP and wait for explicit user OK before crossing either one.

**Goal:** `/videoref` currently exposes/undresses the subject by default (accidental
`wardrobe="safe"` fallback — negative-only, unreliably enforced). Add a fourth toggle row
("🩱 Не раздевать") mirroring the existing `engine_mode`/`vref:eng:` pattern, default the
pending state to `wardrobe_mode="preserve"` (positive clothing anchor + strong negative,
same as `/swapbatch`'s own default), and thread it through to the real prompt assembly —
without touching `/swapbatch`, `/animate`, `engine_mode`, or any engine/core code.

**Architecture:** `_VIDEOREF_SWAP_PENDING[chat_id]` gains a `"wardrobe_mode"` key (default
`"preserve"`). `_videoref_duration_keyboard` reads it and adds a fourth toggle row. A new
`_videoref_wardrobe_toggle` handler (mirrors `_videoref_engine_toggle`) flips it between
`preserve`/`spicy` and redraws. `wardrobe_mode` threads as a plain parameter through
`_videoref_swapanim_stages` → `_videoref_do_animate` → `build_single_animate_request`
(`face_swap_handler.py`) → `assemble_animate_prompt` (already generic, untouched). No
pricing change (`_videoref_swapanim_est` untouched — wardrobe is prompt-only).

**Tech Stack:** Python, pytest, unittest.mock — mirrors `tests/test_videoref_second_engine.py`
conventions 1:1 (same `_get_bot_module()`/`_run_handoff()` fixtures).

**Spec:** `docs/superpowers/specs/2026-07-03-videoref-wardrobe-toggle-design.md`
**Worktree:** `C:/jarvis_worktrees/vref-wardrobe`, branch `vref-wardrobe` (based on `cbf661e`,
spec doc committed on top).

---

## Scope guard (read before every task)

**venv:** always `/c/jarvis/.venv/Scripts/python.exe -m pytest` — bare `python` resolves to
an unrelated venv missing project deps. Confirmed baseline in this worktree: **178 passed,
0 failed** across `test_videoref_second_engine.py` (20) +
`test_swapbatch_wardrobe_routing.py` + `test_swapbatch_wardrobe_ux.py` +
`test_prompt_assembly.py` + `test_swapbatch_handler.py` + `test_swapbatch_orchestrator.py`
+ `test_swapbatch_custom_pipeline.py` + `test_animate_batch_integration.py` +
`test_animate_batch_orchestrator.py` (158).

Only these files are touched:
- `tools/jarvis_smart_telegram_control.py` — modify
- `app/handlers/face_swap_handler.py` — modify (`build_single_animate_request` only)
- `tests/test_videoref_wardrobe_toggle.py` — create

`app/services/block_m2_video/prompt_assembly.py` (wardrobe table, `assemble_animate_prompt`),
`/animate` (`anim:` prefix), `/swapbatch` (`sbward:`/`sbeng:`/`sbq:` prefixes),
`batch_orchestrator.py` (`set_wardrobe`/`handle_wardrobe_button`), the `engine_mode`
toggle/plumbing from the `vref-eng` arc, and everything under `app/services/vizir/` are
**read-only** — never edit them in this plan. If a task seems to require touching them,
stop and flag it instead of proceeding.

---

## Task 1: Write the full spy-teeth suite (RED) — CHECKPOINT 1

**Files:**
- Create: `tests/test_videoref_wardrobe_toggle.py`

Write every test for the whole feature before any implementation exists. All expected to
fail (or, for regression teeth that only assert TODAY's behavior, to already pass — that's
fine, they're the safety net). This is the checkpoint the user asked to review before
implementation starts.

- [ ] **Step 1: Create the test file with shared fixtures (copy `_get_bot_module`/
`_run_handoff` 1:1 from `tests/test_videoref_second_engine.py`)**

```python
"""/videoref wardrobe toggle ("Не раздевать") — TDD spy-teeth.

Root cause of the live-test exposure bug: /videoref never passed a `wardrobe` arg
anywhere, so assemble_animate_prompt silently fell back to its own internal default
wardrobe="safe" (negative-only, unreliably enforced). Fix: default the pending state to
wardrobe_mode="preserve" (positive clothing anchor + strong negative — /swapbatch's own
default) and thread it through as a fourth toggle row (vref:ward:<mode>), mirroring the
existing engine_mode/vref:eng: pattern exactly.

CRITICAL teeth: (1) default-without-tap now assembles a preserve-mode prompt (the fix
itself), (2) preserve adds a POSITIVE anchor, not just a negative (distinguishes it from
the old accidental safe fallback), (3) engine_mode and wardrobe_mode are independent axes.

All heavy boundaries (limit gate, swap engine, animate engine, cost ledger, telegram) are
patched except where a test explicitly needs the REAL assemble_animate_prompt output
(prompt-content teeth use a real FaceSwapHandler + BatchOrchestrator, no engine call).
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.block_m2_video.motion_prompt_ai import VideoMotionResult  # noqa: E402


def _get_bot_module():
    spec = importlib.util.spec_from_file_location(
        "jarvis_tg_ctrl_vrefward",
        ROOT / "tools" / "jarvis_smart_telegram_control.py",
    )
    mod = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test",
        "TELEGRAM_ALLOWED_CHAT_ID": "123",
    }):
        spec.loader.exec_module(mod)
    return mod


def _run_handoff(bot, frames_root, duration):
    """Identical helper to test_videoref_second_engine.py, duplicated so this file is
    independently runnable."""
    frames_dir = frames_root / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    (frames_dir / "frame_000.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    pend = {"frames_dir": frames_dir}
    if duration is not None:
        pend["duration"] = duration
    bot._VIDEOREF_PENDING[123] = pend
    ok = VideoMotionResult(prompt="turns head left", cost_usd=0.31)
    with patch.object(bot, "_check_limit", return_value=(True, "")), \
         patch.object(bot, "select_best_frame", return_value=Path("frame_001.jpg")), \
         patch.object(bot, "generate_video_motion_prompt", return_value=ok), \
         patch.object(bot._cost, "record_cost"), \
         patch.object(bot, "_send_local_photo"), \
         patch.object(bot, "send_with_keyboard") as skb, \
         patch.object(bot, "send"):
        bot._videoref_motion_run("123")
    return skb


def _make_real_handler(tmp_path):
    """Real FaceSwapHandler + BatchOrchestrator (no session for the chat used) — for
    teeth that must assert on the ACTUAL assembled prompt text, not a mocked call."""
    from app.handlers.face_swap_handler import FaceSwapHandler
    from app.services.block_m2_face_swap.batch_orchestrator import BatchOrchestrator
    orch = BatchOrchestrator(state_root=tmp_path / "batches", validator=MagicMock())
    return FaceSwapHandler(orchestrator=orch)
```

- [ ] **Step 2: Tooth 1 — default integrity (pending-level + prompt-level, THE fix)**

```python
# ── Tooth 1: default without any tap -> preserve, not the old accidental safe ──


def test_handoff_defaults_wardrobe_mode_to_preserve(tmp_path):
    bot = _get_bot_module()
    _run_handoff(bot, tmp_path / "ward_default", 10)
    assert bot._VIDEOREF_SWAP_PENDING[123]["wardrobe_mode"] == "preserve"


def test_build_single_animate_request_default_wardrobe_assembles_preserve_prompt(tmp_path):
    """THE FIX: no wardrobe passed -> real assembled prompt carries the preserve
    positive anchor, NOT the old accidental 'safe' (negative-only, unreliable) fallback."""
    handler = _make_real_handler(tmp_path)
    req = handler.build_single_animate_request(
        999, image_path="x.jpg", motion="turns head",
    )
    assert "keeping original clothing" in req.prompt
```

- [ ] **Step 3: Tooth 2 — toggle flips + real prompt changes**

```python
# ── Tooth 2: tap vref:ward:spicy flips state and the assembled prompt changes ──


def test_wardrobe_toggle_switches_mode_and_redraws():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "spicy", "wardrobe_mode": "preserve",
    }
    with patch.object(bot, "send_with_keyboard") as skb, patch.object(bot, "send"):
        bot._videoref_wardrobe_toggle("123", "spicy")
    pend = bot._VIDEOREF_SWAP_PENDING[123]
    assert pend["wardrobe_mode"] == "spicy"
    skb.assert_called_once()


def test_wardrobe_toggle_back_to_preserve():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "wardrobe_mode": "spicy",
    }
    with patch.object(bot, "send_with_keyboard"), patch.object(bot, "send"):
        bot._videoref_wardrobe_toggle("123", "preserve")
    assert bot._VIDEOREF_SWAP_PENDING[123]["wardrobe_mode"] == "preserve"


def test_wardrobe_toggle_without_pending_is_soft_and_does_not_crash():
    bot = _get_bot_module()
    assert 123 not in bot._VIDEOREF_SWAP_PENDING
    with patch.object(bot, "send") as snd:
        bot._videoref_wardrobe_toggle("123", "spicy")
    assert 123 not in bot._VIDEOREF_SWAP_PENDING
    assert snd.called


def test_wardrobe_toggle_invalid_mode_falls_back_to_preserve_and_does_not_raise():
    """Invalid input never corrupts pending -> safe fallback is preserve (NOT spicy —
    the safe direction for an unrecognized value is the protective one)."""
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "wardrobe_mode": "spicy",
    }
    with patch.object(bot, "send_with_keyboard") as skb, patch.object(bot, "send"):
        bot._videoref_wardrobe_toggle("123", "not_a_real_mode")
    assert bot._VIDEOREF_SWAP_PENDING[123]["wardrobe_mode"] == "preserve"
    skb.assert_called_once()


def test_dispatch_vref_ward_routes_to_toggle():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "wardrobe_mode": "preserve",
    }
    cq = {
        "id": "cqid", "data": "vref:ward:spicy",
        "message": {"chat": {"id": 123}, "message_id": 1},
        "from": {"id": 123},
    }
    with patch.object(bot, "answer_callback_query") as ack, \
         patch.object(bot, "_videoref_wardrobe_toggle") as toggle, \
         patch.object(bot, "_is_admin_id", return_value=True):
        bot.handle_callback_query(cq, {})
    ack.assert_called_once()
    toggle.assert_called_once_with("123", "spicy")


def test_keyboard_gains_fourth_wardrobe_row(tmp_path):
    bot = _get_bot_module()
    _run_handoff(bot, tmp_path / "ward_row", 10)
    rows = bot._videoref_duration_keyboard(123)
    assert len(rows) == 4
    ward_row = rows[3][0]
    assert "раздевать" in ward_row["text"].lower()
    assert ward_row["callback_data"] == "vref:ward:spicy"


def test_keyboard_spicy_wardrobe_row_offers_preserve_back():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "spicy", "wardrobe_mode": "spicy",
    }
    rows = bot._videoref_duration_keyboard(123)
    ward_row = rows[3][0]
    assert ward_row["callback_data"] == "vref:ward:preserve"
```

- [ ] **Step 4: Tooth 3 — preserve adds a POSITIVE anchor, not just a negative**

```python
# ── Tooth 3: preserve = positive anchor + negative, spicy = neither ────────────


def test_preserve_wardrobe_prompt_has_positive_anchor_not_just_negative(tmp_path):
    handler = _make_real_handler(tmp_path)
    req = handler.build_single_animate_request(
        999, image_path="x.jpg", motion="turns head", wardrobe="preserve",
    )
    assert "keeping original clothing" in req.prompt
    assert "exposed chest" in req.negative_prompt


def test_spicy_wardrobe_prompt_has_no_clothing_anchor_or_negative(tmp_path):
    handler = _make_real_handler(tmp_path)
    req = handler.build_single_animate_request(
        999, image_path="x.jpg", motion="turns head", wardrobe="spicy",
    )
    assert "keeping original clothing" not in req.prompt
    assert "undressing" not in req.negative_prompt
```

- [ ] **Step 5: Tooth 4 — engine_mode and wardrobe_mode are independent**

```python
# ── Tooth 4: engine_mode + wardrobe_mode independent, both thread through ──────


def test_wardrobe_toggle_does_not_touch_engine_mode():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "seedance", "wardrobe_mode": "preserve",
    }
    with patch.object(bot, "send_with_keyboard"), patch.object(bot, "send"):
        bot._videoref_wardrobe_toggle("123", "spicy")
    pend = bot._VIDEOREF_SWAP_PENDING[123]
    assert pend["engine_mode"] == "seedance"      # untouched by wardrobe toggle
    assert pend["wardrobe_mode"] == "spicy"


def test_engine_toggle_does_not_touch_wardrobe_mode():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "spicy", "wardrobe_mode": "spicy",
    }
    with patch.object(bot, "send_with_keyboard"), patch.object(bot, "send"):
        bot._videoref_engine_toggle("123", "seedance")
    pend = bot._VIDEOREF_SWAP_PENDING[123]
    assert pend["wardrobe_mode"] == "spicy"        # untouched by engine toggle
    assert pend["engine_mode"] == "seedance"


def test_do_animate_threads_both_engine_mode_and_wardrobe_mode(monkeypatch):
    bot = _get_bot_module()
    import app.services.block_m2_video.engines.router as router_mod
    import app.services.block_m2_video.batch_animate as ba_mod
    handler = MagicMock()
    handler.build_single_animate_request.return_value = "REQ"

    async def fake_select(mode):
        fake_select.mode = mode
        return "ENGINE"

    async def fake_animate(engine, reqs, concurrency=1):
        return [Path("/tmp/video.mp4")]

    class FakeRouter:
        def select(self, mode):
            return fake_select(mode)

    monkeypatch.setattr(router_mod, "EngineRouter", FakeRouter)
    monkeypatch.setattr(ba_mod, "animate_batch", fake_animate)

    bot._videoref_do_animate(
        123, handler, Path("/tmp/s.jpg"), "m",
        seconds=10, engine_mode="seedance", wardrobe_mode="spicy",
    )
    assert fake_select.mode == "seedance"
    kw = handler.build_single_animate_request.call_args.kwargs
    assert kw["engine_mode"] == "seedance"
    assert kw["wardrobe"] == "spicy"


def test_do_animate_default_wardrobe_mode_is_preserve_when_omitted(monkeypatch):
    """CRITICAL: no wardrobe_mode passed to _videoref_do_animate -> still 'preserve'
    (the function's own default), not the old accidental 'safe'."""
    bot = _get_bot_module()
    import app.services.block_m2_video.engines.router as router_mod
    import app.services.block_m2_video.batch_animate as ba_mod
    handler = MagicMock()
    handler.build_single_animate_request.return_value = "REQ"

    async def fake_select(mode):
        return "ENGINE"

    async def fake_animate(engine, reqs, concurrency=1):
        return [Path("/tmp/video.mp4")]

    class FakeRouter:
        def select(self, mode):
            return fake_select(mode)

    monkeypatch.setattr(router_mod, "EngineRouter", FakeRouter)
    monkeypatch.setattr(ba_mod, "animate_batch", fake_animate)

    bot._videoref_do_animate(123, handler, Path("/tmp/s.jpg"), "m")
    kw = handler.build_single_animate_request.call_args.kwargs
    assert kw["wardrobe"] == "preserve"


def test_stages_reads_wardrobe_mode_from_pending_and_threads_to_do_animate():
    bot = _get_bot_module()
    handler = MagicMock()
    pend = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "spicy", "wardrobe_mode": "spicy",
    }
    with patch.object(bot, "_videoref_do_swap", return_value=Path("/tmp/s.jpg")), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_videoref_do_animate", return_value=Path("/tmp/v.mp4")) as an, \
         patch.object(bot._cost, "record_cost"), \
         patch.object(bot, "_send_local_video"), patch.object(bot, "send"):
        bot._videoref_swapanim_stages(
            "123", "/tmp/face.jpg", pend, bot._videoref_swapanim_est(10)
        )
    assert an.call_args.kwargs.get("wardrobe_mode") == "spicy"


def test_stages_regression_no_wardrobe_mode_key_defaults_to_preserve():
    """Old-shape pending (no wardrobe_mode key at all) -> stages still passes
    'preserve' explicitly to do_animate (fix applies even to stale pending dicts)."""
    bot = _get_bot_module()
    handler = MagicMock()
    pend = {"best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10}
    with patch.object(bot, "_videoref_do_swap", return_value=Path("/tmp/s.jpg")), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_videoref_do_animate", return_value=Path("/tmp/v.mp4")) as an, \
         patch.object(bot._cost, "record_cost"), \
         patch.object(bot, "_send_local_video"), patch.object(bot, "send"):
        bot._videoref_swapanim_stages(
            "123", "/tmp/face.jpg", pend, bot._videoref_swapanim_est(10)
        )
    assert an.call_args.kwargs.get("wardrobe_mode", "preserve") == "preserve"
```

- [ ] **Step 6: Tooth 5 — `/swapbatch` wardrobe isolation**

```python
# ── Tooth 5: vref:ward: never collides with sbward:, /swapbatch untouched ──────


def test_dispatch_sbward_does_not_touch_videoref_wardrobe_toggle():
    bot = _get_bot_module()
    handler = MagicMock()
    handler.handle_wardrobe_button.return_value = "ok"
    cq = {
        "id": "cqid", "data": "sbward:on",
        "message": {"chat": {"id": 123}, "message_id": 1},
        "from": {"id": 123},
    }
    with patch.object(bot, "answer_callback_query"), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_swapbatch_apply_reply"), \
         patch.object(bot, "send_with_keyboard"), \
         patch.object(bot, "_videoref_wardrobe_toggle") as vward_toggle, \
         patch.object(bot, "_is_admin_id", return_value=True):
        bot.handle_callback_query(cq, {})
    vward_toggle.assert_not_called()


def test_vref_ward_prefix_covered_by_existing_friend_allowlist():
    bot = _get_bot_module()
    assert "vref:" in bot.FRIEND_ALLOWED_CALLBACK_PREFIXES
    assert "sbward:" in bot.FRIEND_ALLOWED_CALLBACK_PREFIXES
```

- [ ] **Step 7: Tooth 6 — money-neutral (est unaffected by wardrobe)**

```python
# ── Tooth 6: wardrobe never enters the cost formula ─────────────────────────────


def test_est_signature_unaffected_by_wardrobe_no_param_exists():
    """_videoref_swapanim_est has no wardrobe parameter at all — wardrobe is
    prompt-only, never priced."""
    bot = _get_bot_module()
    import inspect
    sig = inspect.signature(bot._videoref_swapanim_est)
    assert "wardrobe" not in sig.parameters
    assert "wardrobe_mode" not in sig.parameters


def test_quoted_equals_charged_unaffected_by_wardrobe_mode():
    """TEETH: gate est == sum of per-stage records regardless of wardrobe_mode —
    quoted == charged holds independent of the wardrobe axis."""
    bot = _get_bot_module()
    handler = MagicMock()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "spicy", "wardrobe_mode": "spicy",
    }
    gate = {}

    def _cl(uid, *, estimated_usd):
        gate["v"] = estimated_usd
        return (True, "")

    with patch.object(bot, "_check_limit", side_effect=_cl), \
         patch.object(bot, "_videoref_do_swap", return_value=Path("/tmp/s.jpg")), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_videoref_do_animate", return_value=Path("/tmp/v.mp4")), \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot, "_send_local_video"), patch.object(bot, "send"):
        bot._videoref_swapanim_run("123", "/tmp/face.jpg")

    charged = sum(c.args[2] for c in rec.call_args_list)
    assert gate["v"] == bot._videoref_swapanim_est(10, engine_mode="spicy")
    assert gate["v"] == charged
```

- [ ] **Step 8: Run the full new file, confirm every test fails for the right reason**

Run: `cd C:/jarvis_worktrees/vref-wardrobe && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_videoref_wardrobe_toggle.py -v`

Expected buckets:
- `wardrobe`/`wardrobe_mode` kwarg tests → `TypeError: ...unexpected keyword argument`
- `bot._videoref_wardrobe_toggle` tests → `AttributeError: ...has no attribute
  '_videoref_wardrobe_toggle'`
- `test_handoff_defaults_wardrobe_mode_to_preserve` → `KeyError: 'wardrobe_mode'`
- `test_build_single_animate_request_default_wardrobe_assembles_preserve_prompt` /
  `test_preserve_wardrobe_prompt_has_positive_anchor...` → `AssertionError` (today's call
  omits `wardrobe=` entirely, so the real function falls back to its OWN `"safe"` default —
  `"keeping original clothing"` will NOT be in `req.prompt` yet)
- `test_spicy_wardrobe_prompt_has_no_clothing_anchor...` → may already PASS (safe's
  negative differs from spicy's empty one, but check — if it fails, note why)
- `test_keyboard_gains_fourth_wardrobe_row` → `AssertionError: len(rows) == 3` (today)
- `test_est_signature_unaffected_by_wardrobe_no_param_exists` → should already PASS
  (no wardrobe param exists today — this locks in the "never touch est" invariant)
- `test_quoted_equals_charged_unaffected_by_wardrobe_mode` → should already PASS (today's
  code ignores the `wardrobe_mode` key entirely, so pricing is already unaffected by it)
- `test_dispatch_sbward_does_not_touch_videoref_wardrobe_toggle` → should already PASS
  (today there's no `_videoref_wardrobe_toggle` to call)
- `test_vref_ward_prefix_covered_by_existing_friend_allowlist` → should already PASS

Verify no test fails for an unrelated reason (import error, typo, wrong fixture).

- [ ] **Step 9: Commit the RED test file**

```bash
git add tests/test_videoref_wardrobe_toggle.py
git commit -m "test(vref-ward): RED spy-teeth for /videoref wardrobe toggle"
```

---

**🛑 CHECKPOINT 1 — STOP HERE.** Report back:
- full pytest output from Step 8 (pass/fail counts + failure reasons per bucket)
- confirm which teeth are already-green (the safety-net/invariant ones) vs RED (the
  actual gap) — RED must include, at minimum: both prompt-content teeth (Step 4), the
  keyboard row tooth, all `_videoref_wardrobe_toggle`-attribute teeth, the handoff-default
  tooth, and the `do_animate`/`stages` wardrobe-threading teeth
- wait for explicit OK before starting Task 2

---

## Task 2: `build_single_animate_request` gains `wardrobe` param (face_swap_handler.py)

**Files:**
- Modify: `app/handlers/face_swap_handler.py:669-697`

- [ ] **Step 1: Replace the function**

Current (lines 669-697):
```python
    def build_single_animate_request(
        self, chat_id: int, *, image_path, motion: str,
        engine_mode: str | None = None, seconds: int | None = None,
        resolution: str | None = None,
    ):
        """Собрать один VideoRequest для standalone /animate.

        Engine/quality читаются из активной сессии, если есть; для чисто
        standalone-потока (без батча) их можно передать явно через оверрайды.
        """
        from pathlib import Path
        from app.services.block_m2_video.engines.engine_protocol import (
            VideoRequest, new_generation_id,
        )
        from app.services.block_m2_video.prompt_assembly import assemble_animate_prompt
        sess = self.orchestrator.get(chat_id)
        engine_mode = engine_mode or (sess.video_engine if sess else "spicy")
        seconds = seconds or (sess.duration_sec if sess else 5)
        resolution = resolution or (sess.resolution if sess else "720p")
        prompt, negative = assemble_animate_prompt(
            (sess.motion_prompt if sess else "") or motion,
            add_realism=True, add_negative=True,
        )
        return VideoRequest(
            persona_id=f"animate_{chat_id}", persona_name="animate",
            input_image_path=Path(image_path), prompt=prompt, seconds=seconds,
            resolution=resolution, negative_prompt=negative, mode=engine_mode,
            generation_id=new_generation_id(),
        )
```

New:
```python
    def build_single_animate_request(
        self, chat_id: int, *, image_path, motion: str,
        engine_mode: str | None = None, seconds: int | None = None,
        resolution: str | None = None, wardrobe: str | None = None,
    ):
        """Собрать один VideoRequest для standalone /animate.

        Engine/quality читаются из активной сессии, если есть; для чисто
        standalone-потока (без батча) их можно передать явно через оверрайды.
        ``wardrobe`` defaults to "preserve" (safer than assemble_animate_prompt's own
        internal "safe" default) when the caller (e.g. /videoref) doesn't pass one.
        """
        from pathlib import Path
        from app.services.block_m2_video.engines.engine_protocol import (
            VideoRequest, new_generation_id,
        )
        from app.services.block_m2_video.prompt_assembly import assemble_animate_prompt
        sess = self.orchestrator.get(chat_id)
        engine_mode = engine_mode or (sess.video_engine if sess else "spicy")
        seconds = seconds or (sess.duration_sec if sess else 5)
        resolution = resolution or (sess.resolution if sess else "720p")
        wardrobe = wardrobe or "preserve"
        prompt, negative = assemble_animate_prompt(
            (sess.motion_prompt if sess else "") or motion,
            add_realism=True, add_negative=True, wardrobe=wardrobe,
        )
        return VideoRequest(
            persona_id=f"animate_{chat_id}", persona_name="animate",
            input_image_path=Path(image_path), prompt=prompt, seconds=seconds,
            resolution=resolution, negative_prompt=negative, mode=engine_mode,
            generation_id=new_generation_id(),
        )
```

- [ ] **Step 2: Run the targeted tests**

Run: `cd C:/jarvis_worktrees/vref-wardrobe && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_videoref_wardrobe_toggle.py -k "build_single_animate_request or preserve_wardrobe_prompt or spicy_wardrobe_prompt" -v tests/test_swapbatch_handler.py -k "animate_request"`

Expected: the two Task 1/Step 2+4 prompt-content teeth PASS; existing
`test_swapbatch_handler.py` `build_single_animate_request` test (line ~604-611, no
`wardrobe=` passed) still PASSES — it asserts `req.mode`/`req.prompt.startswith(...)`
only, unaffected by the new default.

- [ ] **Step 3: Commit**

```bash
git add app/handlers/face_swap_handler.py
git commit -m "feat(vref-ward): build_single_animate_request gains wardrobe param, default preserve"
```

---

## Task 3: Handoff pending defaults `wardrobe_mode: "preserve"`

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py:1615-1619`

- [ ] **Step 1: Add the default key to the handoff dict**

Current (lines 1615-1619):
```python
    _VIDEOREF_SWAP_PENDING[chat_id_int] = {
        "best_frame": best, "motion_prompt": result.prompt,
        "seconds": _proposed, "smooth": False,   # smooth default OFF (money-safe)
        "engine_mode": "spicy",                  # default engine unchanged (money-safe)
    }
```

New:
```python
    _VIDEOREF_SWAP_PENDING[chat_id_int] = {
        "best_frame": best, "motion_prompt": result.prompt,
        "seconds": _proposed, "smooth": False,   # smooth default OFF (money-safe)
        "engine_mode": "spicy",                  # default engine unchanged (money-safe)
        "wardrobe_mode": "preserve",             # default safe (fixes accidental exposure)
    }
```

- [ ] **Step 2: Run the targeted test**

Run: `cd C:/jarvis_worktrees/vref-wardrobe && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_videoref_wardrobe_toggle.py -k "handoff_defaults_wardrobe" -v tests/test_videoref_second_engine.py`

Expected: `test_handoff_defaults_wardrobe_mode_to_preserve` PASSES; all
`test_videoref_second_engine.py` tests still PASS (new key doesn't break dict-key
assertions that only check specific keys).

- [ ] **Step 3: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py
git commit -m "feat(vref-ward): handoff stashes wardrobe_mode default (preserve)"
```

---

## Task 4: Fourth toggle row + `_videoref_wardrobe_toggle` handler + `vref:ward:` dispatch

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py:1418-1453` (`_videoref_duration_keyboard`)
- Modify: `tools/jarvis_smart_telegram_control.py` — new function after
  `_videoref_engine_toggle` (currently ends line 1697)
- Modify: `tools/jarvis_smart_telegram_control.py:3713-3718` (the `vref:` dispatch block)

- [ ] **Step 1: Add the fourth row to `_videoref_duration_keyboard`**

Current tail (lines 1445-1453):
```python
    smooth_state = "ВКЛ" if smooth else "ВЫКЛ"
    smooth_cb = "vref:smooth:off" if smooth else "vref:smooth:on"
    other_engine = "seedance" if engine_mode == "spicy" else "spicy"
    return [
        [_dur_btn(s) for s in caps.allowed_durations],
        [{"text": f"🪶 Плавность 48fps: {smooth_state}", "callback_data": smooth_cb}],
        [{"text": f"🎬 Движок: {caps.display_name}",
          "callback_data": f"vref:eng:{other_engine}"}],
    ]
```

New:
```python
    smooth_state = "ВКЛ" if smooth else "ВЫКЛ"
    smooth_cb = "vref:smooth:off" if smooth else "vref:smooth:on"
    other_engine = "seedance" if engine_mode == "spicy" else "spicy"
    wardrobe_mode = pend.get("wardrobe_mode", "preserve")
    ward_state = "ВКЛ" if wardrobe_mode == "preserve" else "ВЫКЛ"
    other_wardrobe = "spicy" if wardrobe_mode == "preserve" else "preserve"
    return [
        [_dur_btn(s) for s in caps.allowed_durations],
        [{"text": f"🪶 Плавность 48fps: {smooth_state}", "callback_data": smooth_cb}],
        [{"text": f"🎬 Движок: {caps.display_name}",
          "callback_data": f"vref:eng:{other_engine}"}],
        [{"text": f"🩱 Не раздевать: {ward_state}",
          "callback_data": f"vref:ward:{other_wardrobe}"}],
    ]
```

Also add the read near the top of the function (alongside `engine_mode = pend.get(...)`
at line 1434) — not strictly required since `wardrobe_mode` is only read at the bottom,
but keep it adjacent to the other `pend.get(...)` reads for readability (implementer's
call; either placement passes the tests).

- [ ] **Step 2: Add the new handler function**

Insert immediately after `_videoref_engine_toggle` (after line 1697, before the blank
line that precedes `def _videoref_face_intercept`):

```python


def _videoref_wardrobe_toggle(chat_id, mode: str) -> None:
    """Wardrobe toggle (vref:ward:<mode>): flip preserve/spicy + redraw.

    Mirrors _videoref_engine_toggle / _videoref_smooth_toggle. Only "preserve" and
    "spicy" are reachable via this button (mirrors the /swapbatch 🩱 button, which also
    never targets "safe" — that mode stays in WARDROBE_MODES but is not a button state).
    Invalid input falls back to "preserve" (the SAFE direction), not "spicy" — unlike
    the engine toggle's fallback, an unrecognized wardrobe value must never widen
    exposure. No spend here — same money-safe shape as every other Веха D toggle.
    Without pending → soft hint (mirrors the stale-button handling used everywhere else).
    """
    chat_id_int = int(chat_id)
    pend = _VIDEOREF_SWAP_PENDING.get(chat_id_int)
    if pend is None:
        send(chat_id, "⚠️ Кнопка устарела — пришли видео заново: /videoref")
        return
    if mode not in ("preserve", "spicy"):
        mode = "preserve"
    pend["wardrobe_mode"] = mode
    state = "ВКЛ" if mode == "preserve" else "ВЫКЛ"
    send_with_keyboard(
        chat_id,
        f"🩱 Не раздевать: {state}. Выбери длину:",
        _videoref_duration_keyboard(chat_id_int),
    )
```

- [ ] **Step 3: Wire the dispatch — add the `ward` branch in the `vref:` block**

Current (lines 3713-3718):
```python
        if action == "eng":
            # Second-engine toggle: switch engine + snap length + redraw (no spend).
            answer_callback_query(cq_id)
            _mode = parts[2] if len(parts) > 2 else "spicy"
            _videoref_engine_toggle(chat_id, _mode)
            return
```

New (insert the `ward` branch immediately after, before the `answer_callback_query(cq_id);
return` fallback at line 3719-3720):
```python
        if action == "eng":
            # Second-engine toggle: switch engine + snap length + redraw (no spend).
            answer_callback_query(cq_id)
            _mode = parts[2] if len(parts) > 2 else "spicy"
            _videoref_engine_toggle(chat_id, _mode)
            return
        if action == "ward":
            # Wardrobe toggle: flip preserve/spicy + redraw (no spend).
            answer_callback_query(cq_id)
            _mode = parts[2] if len(parts) > 2 else "preserve"
            _videoref_wardrobe_toggle(chat_id, _mode)
            return
```

- [ ] **Step 4: Run the targeted + isolation tests**

Run: `cd C:/jarvis_worktrees/vref-wardrobe && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_videoref_wardrobe_toggle.py -v`

Expected: every toggle/keyboard/dispatch/isolation tooth from Task 1 Steps 3, 5, 6 now
PASSES. Prompt-content and do_animate/stages teeth (Steps 4, 5) still RED — that's Task 5.

Also run: `cd C:/jarvis_worktrees/vref-wardrobe && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_videoref_second_engine.py tests/test_swapbatch_wardrobe_routing.py tests/test_swapbatch_wardrobe_ux.py -v`

Expected: all still PASS (no regression — `vref:ward:` is additive, `sbward:` untouched).

- [ ] **Step 5: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py
git commit -m "feat(vref-ward): fourth toggle row + _videoref_wardrobe_toggle + vref:ward: dispatch"
```

---

## Task 5: Thread `wardrobe_mode` through `_videoref_do_animate` + stages — CHECKPOINT 2 (final)

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py:1804-1824` (`_videoref_do_animate`)
- Modify: `tools/jarvis_smart_telegram_control.py:1857-1858, 1882-1885`
  (`_videoref_swapanim_stages`)

- [ ] **Step 1: `_videoref_do_animate` gains `wardrobe_mode` param**

Current (lines 1804-1824):
```python
def _videoref_do_animate(chat_id_int, handler, swapped, motion_prompt,
                         seconds=VIDEOREF_ANIM_SECONDS, engine_mode="spicy"):
    """Stage-2 raw call (reuse 1:1, the _animate_run_single pattern): animate the
    swapped frame on the chosen engine (``seconds``/720p) by the motion prompt.

    engine_mode defaults to "spicy" (uncensored — the original point of the arc)
    but is now selectable per-run (second-engine arc, Seedance). Returns the video
    Path or None.
    """
    import asyncio as _aio
    from app.services.block_m2_video.engines.router import EngineRouter
    from app.services.block_m2_video.batch_animate import animate_batch

    req = handler.build_single_animate_request(
        chat_id_int, image_path=swapped, motion=motion_prompt,
        engine_mode=engine_mode,
        seconds=seconds, resolution=VIDEOREF_ANIM_RESOLUTION,
    )
    engine = _aio.run(EngineRouter().select(engine_mode))
    results = _aio.run(animate_batch(engine, [req], concurrency=1))
    return results[0] if results else None
```

New:
```python
def _videoref_do_animate(chat_id_int, handler, swapped, motion_prompt,
                         seconds=VIDEOREF_ANIM_SECONDS, engine_mode="spicy",
                         wardrobe_mode="preserve"):
    """Stage-2 raw call (reuse 1:1, the _animate_run_single pattern): animate the
    swapped frame on the chosen engine (``seconds``/720p) by the motion prompt.

    engine_mode defaults to "spicy" (uncensored — the original point of the arc)
    but is now selectable per-run (second-engine arc, Seedance). wardrobe_mode
    defaults to "preserve" (safety fix — was accidentally "safe" via omission before
    this arc). The two are independent axes. Returns the video Path or None.
    """
    import asyncio as _aio
    from app.services.block_m2_video.engines.router import EngineRouter
    from app.services.block_m2_video.batch_animate import animate_batch

    req = handler.build_single_animate_request(
        chat_id_int, image_path=swapped, motion=motion_prompt,
        engine_mode=engine_mode, wardrobe=wardrobe_mode,
        seconds=seconds, resolution=VIDEOREF_ANIM_RESOLUTION,
    )
    engine = _aio.run(EngineRouter().select(engine_mode))
    results = _aio.run(animate_batch(engine, [req], concurrency=1))
    return results[0] if results else None
```

- [ ] **Step 2: `_videoref_swapanim_stages` reads and threads `wardrobe_mode`**

Current (line 1858, and the `_videoref_do_animate` call at 1882-1885):
```python
    engine_mode = pend.get("engine_mode", "spicy")         # chosen engine (second-engine arc)
```
```python
        video = _videoref_do_animate(
            chat_id_int, handler, swapped, motion_prompt, seconds,
            engine_mode=engine_mode,
        )
```

New:
```python
    engine_mode = pend.get("engine_mode", "spicy")         # chosen engine (second-engine arc)
    wardrobe_mode = pend.get("wardrobe_mode", "preserve")  # chosen wardrobe (safety fix)
```
```python
        video = _videoref_do_animate(
            chat_id_int, handler, swapped, motion_prompt, seconds,
            engine_mode=engine_mode, wardrobe_mode=wardrobe_mode,
        )
```

- [ ] **Step 3: Run the full new file + full regression sweep**

Run: `cd C:/jarvis_worktrees/vref-wardrobe && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_videoref_wardrobe_toggle.py -v`

Expected: **all tests PASS** (0 failures).

Run: `cd C:/jarvis_worktrees/vref-wardrobe && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_videoref_second_engine.py tests/test_swapbatch_wardrobe_routing.py tests/test_swapbatch_wardrobe_ux.py tests/test_prompt_assembly.py tests/test_swapbatch_handler.py tests/test_swapbatch_orchestrator.py tests/test_swapbatch_custom_pipeline.py tests/test_animate_batch_integration.py tests/test_animate_batch_orchestrator.py -v`

Expected: **all 178 baseline tests still PASS** — byte-identical `engine_mode` behavior,
`/swapbatch` wardrobe untouched, `/animate` untouched.

Run: `cd C:/jarvis_worktrees/vref-wardrobe && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_veha_d_swapanim.py tests/test_videoref_limits.py tests/test_videoref_motion_money.py tests/test_videoref_wiring.py -v`

Expected: **all PASS** — the pre-existing `/videoref` regression suite, confirming
`quoted == charged` and every other pre-arc invariant still holds.

Run: `cd C:/jarvis_worktrees/vref-wardrobe && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/ -q`

Expected: same pass/fail counts as the pre-arc baseline (pre-existing unrelated
env/config failures — see `jarvis-preexisting-test-failures-techdebt` memory — are not
from this arc; zero NEW failures).

- [ ] **Step 4: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py
git commit -m "feat(vref-ward): thread wardrobe_mode through do_animate + stages"
```

---

**🛑 CHECKPOINT 2 — STOP HERE (final).** Report back:
- full pass/fail counts from Step 3 (new file, regression sweep, full suite)
- explicit confirmation: default `preserve` path is the intentional behavior CHANGE
  (unlike the second-engine arc, this one is NOT byte-identical by design — flag this
  clearly, it's the whole point), `engine_mode` byte-identical (regression tests green),
  `vref:ward:` isolated from `sbward:`/`sbeng:`/`anim:` (isolation tests green)
- do NOT merge to `phase-4.0-unified-jarvis`, do NOT restart the bot, do NOT run a live
  test — wait for explicit OK (live test = Seedance + preserve + 5s ≈ $0.18, discussed
  separately, tests both the wardrobe fix AND the deferred second-engine arc in one run)
