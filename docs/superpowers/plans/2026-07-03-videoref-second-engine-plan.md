# /videoref Second Engine (Movable Engine Toggle) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax
> for tracking. Two hard checkpoints in this plan (end of Task 1, end of Task 9) — STOP
> and wait for user OK before proceeding past either one.

**Goal:** Let `/videoref` users pick the animate engine (WaveSpeed `spicy` / Seedance
`seedance`) via a third toggle row on the existing swap+animate keyboard, reusing the
engine pair `/animate` and `/swapbatch` already expose — without changing the default
behavior for anyone who never taps it.

**Architecture:** `_VIDEOREF_SWAP_PENDING[chat_id]` gains an `"engine_mode"` key
(default `"spicy"`). `_videoref_duration_keyboard` reads it, renders engine-aware
durations/prices, and adds a toggle row. A new `_videoref_engine_toggle` handler
(mirrors `_videoref_smooth_toggle`) flips it, snaps `seconds` to the new engine's
`allowed_durations`, and redraws. `engine_mode` is threaded as a plain parameter
through `_videoref_swapanim_est` → `_videoref_swapanim_run` (money gate) →
`_videoref_swapanim_stages` (billing) → `_videoref_do_animate` (engine selection),
replacing four hardcoded `"spicy"` occurrences one at a time. No new engines, no
core/animate/swapbatch changes.

**Tech Stack:** Python, pytest, unittest.mock (existing project conventions — see
`tests/test_veha_d_swapanim.py` for the established mocking pattern this plan follows
1:1).

**Spec:** `docs/superpowers/specs/2026-07-03-videoref-second-engine-design.md`
**Worktree:** `C:/jw/vref2eng`, branch `vizir-videoref-second-engine` (based on `888f778`,
spec doc committed on top at `7e6341c`).

---

## Scope guard (read before every task)

**venv gotcha:** bare `python` on PATH resolves to an unrelated Hermes-agent venv
(missing `replicate` and other project deps — causes a false `ModuleNotFoundError`
failure in `test_do_animate_reuses_pattern_with_spicy_5s_720p`). Every pytest command
in this plan uses the explicit project venv:
`/c/jarvis/.venv/Scripts/python.exe -m pytest` (Bash) —
confirmed baseline: **63 passed, 0 failed** across
`test_veha_d_swapanim.py`/`test_videoref_limits.py`/`test_videoref_motion_money.py`/
`test_videoref_wiring.py` with this venv. Never swap back to bare `python`.

Only these two files are touched:
- `tools/jarvis_smart_telegram_control.py` — modify
- `tests/test_videoref_second_engine.py` — create

`app/services/block_m2_video/engines/` (router, `capabilities.py`, engine impls),
`/animate` (`anim:` prefix), `/swapbatch` (`sbeng:`/`sbq:`/`sbsmooth:`/`sbward:`
prefixes), and everything under `app/services/vizir/` are **read-only** — never edit
them in this plan. If a task seems to require touching them, stop and flag it instead
of proceeding.

---

## Task 1: Write the full spy-teeth suite (RED) — CHECKPOINT 1

**Files:**
- Create: `tests/test_videoref_second_engine.py`

This task writes **every** test for the whole feature before any implementation
exists. All of them are expected to fail. This is the checkpoint the user asked to
review before implementation starts — the regression tests are the part that matters
most (breaking `/videoref` for existing users is the critical failure mode).

- [ ] **Step 1: Create the test file with shared fixtures (copy the established pattern
1:1 from `tests/test_veha_d_swapanim.py`)**

```python
"""/videoref second engine (movable engine toggle) — TDD spy-teeth.

Second engine choice (spicy/seedance) becomes a third toggle row on the existing
swap+animate keyboard. engine_mode threads through est -> gate -> stages -> do_animate,
replacing four hardcoded "spicy" occurrences. CRITICAL: every read site must default
to "spicy" when pending has no "engine_mode" key (old-shape pending / no tap) so
existing /videoref behavior stays byte-identical.

All heavy boundaries (limit gate, swap engine, animate engine, cost ledger, telegram)
are patched — no paid call runs here. Mirrors tests/test_veha_d_swapanim.py conventions.
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
        "jarvis_tg_ctrl_vref2eng",
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
    """Run a successful motion analysis with a given reference duration stashed
    (identical helper to test_veha_d_swapanim.py, duplicated here so this file is
    independently runnable)."""
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
```

- [ ] **Step 2: Add est() engine_mode tests**

```python
# ── engine_mode threads through _videoref_swapanim_est ────────────────────────


def test_est_default_engine_mode_is_spicy_byte_for_byte():
    """CRITICAL regression: no engine_mode arg -> identical number to today."""
    bot = _get_bot_module()
    from app.services.block_m2_video.engines.capabilities import caps_for
    expected = bot.VIDEOREF_SWAP_USD + caps_for("spicy").cost_for(5, "720p")
    assert bot._videoref_swapanim_est() == expected
    assert bot._videoref_swapanim_est(engine_mode="spicy") == bot._videoref_swapanim_est()


def test_est_engine_mode_seedance_uses_seedance_cost():
    bot = _get_bot_module()
    from app.services.block_m2_video.engines.capabilities import caps_for
    expected = bot.VIDEOREF_SWAP_USD + caps_for("seedance").cost_for(10, "720p")
    assert bot._videoref_swapanim_est(10, engine_mode="seedance") == expected
```

- [ ] **Step 3: Add handoff default tests**

```python
# ── handoff stashes engine_mode default ────────────────────────────────────────


def test_handoff_defaults_engine_mode_to_spicy(tmp_path):
    bot = _get_bot_module()
    _run_handoff(bot, tmp_path / "eng_default", 10)
    assert bot._VIDEOREF_SWAP_PENDING[123]["engine_mode"] == "spicy"
```

- [ ] **Step 4: Add keyboard tests (regression + engine-aware + third row)**

```python
# ── _videoref_duration_keyboard becomes engine-aware ───────────────────────────


def test_keyboard_regression_no_tap_identical_durations_and_prices(tmp_path):
    """CRITICAL regression: default engine_mode -> byte-identical to the
    pre-second-engine keyboard (same 3 buttons, same callback_data, same prices)."""
    bot = _get_bot_module()
    _run_handoff(bot, tmp_path / "regress", 10)
    rows = bot._videoref_duration_keyboard(123)
    dur_row = rows[0]
    assert [b["callback_data"] for b in dur_row] == ["vref:sa:5", "vref:sa:10", "vref:sa:15"]
    for sec, btn in zip((5, 10, 15), dur_row):
        assert f"{bot._videoref_swapanim_est(sec):.2f}" in btn["text"]
    assert rows[1][0]["callback_data"] in ("vref:smooth:on", "vref:smooth:off")


def test_keyboard_default_engine_row_offers_seedance(tmp_path):
    bot = _get_bot_module()
    _run_handoff(bot, tmp_path / "eng_row", 10)
    rows = bot._videoref_duration_keyboard(123)
    assert len(rows) == 3
    eng_row = rows[2][0]
    assert "WaveSpeed" in eng_row["text"]
    assert eng_row["callback_data"] == "vref:eng:seedance"


def test_keyboard_seedance_mode_uses_seedance_durations_and_prices(tmp_path):
    bot = _get_bot_module()
    _run_handoff(bot, tmp_path / "eng_sd", 10)
    bot._VIDEOREF_SWAP_PENDING[123]["engine_mode"] = "seedance"
    rows = bot._videoref_duration_keyboard(123)
    dur_row = rows[0]
    assert [b["callback_data"] for b in dur_row] == ["vref:sa:5", "vref:sa:10"]
    for sec, btn in zip((5, 10), dur_row):
        assert f"{bot._videoref_swapanim_est(sec, engine_mode='seedance'):.2f}" in btn["text"]
    eng_row = rows[2][0]
    assert "Seedance" in eng_row["text"]
    assert eng_row["callback_data"] == "vref:eng:spicy"
```

- [ ] **Step 5: Add engine-toggle handler tests (snap + soft-hint)**

```python
# ── _videoref_engine_toggle: switch + snap + redraw ────────────────────────────


def test_engine_toggle_switches_mode_and_snaps_duration():
    """Toggle to seedance while at 15с (WaveSpeed-only length) -> snaps to 10с
    (Seedance's max, allowed_durations=(5, 10))."""
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 15,
        "smooth": False, "engine_mode": "spicy",
    }
    with patch.object(bot, "send_with_keyboard") as skb, patch.object(bot, "send"):
        bot._videoref_engine_toggle("123", "seedance")
    pend = bot._VIDEOREF_SWAP_PENDING[123]
    assert pend["engine_mode"] == "seedance"
    assert pend["seconds"] == 10
    skb.assert_called_once()


def test_engine_toggle_back_to_spicy_does_not_resnap_valid_length():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "seedance",
    }
    with patch.object(bot, "send_with_keyboard"), patch.object(bot, "send"):
        bot._videoref_engine_toggle("123", "spicy")
    pend = bot._VIDEOREF_SWAP_PENDING[123]
    assert pend["engine_mode"] == "spicy"
    assert pend["seconds"] == 10          # 10с valid for WaveSpeed too -> unchanged


def test_engine_toggle_without_pending_is_soft_and_does_not_crash():
    bot = _get_bot_module()
    assert 123 not in bot._VIDEOREF_SWAP_PENDING
    with patch.object(bot, "send") as snd:
        bot._videoref_engine_toggle("123", "seedance")
    assert 123 not in bot._VIDEOREF_SWAP_PENDING
    assert snd.called
```

- [ ] **Step 6: Add dispatch-routing + isolation tests**

```python
# ── vref:eng: dispatch — isolated from sbeng:/anim: ────────────────────────────


def test_dispatch_vref_eng_seedance_routes_to_toggle():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 15,
        "engine_mode": "spicy",
    }
    cq = {
        "id": "cqid", "data": "vref:eng:seedance",
        "message": {"chat": {"id": 123}, "message_id": 1},
        "from": {"id": 123},
    }
    with patch.object(bot, "answer_callback_query") as ack, \
         patch.object(bot, "_videoref_engine_toggle") as toggle, \
         patch.object(bot, "_is_admin_id", return_value=True):
        bot.handle_callback_query(cq, {})
    ack.assert_called_once()
    toggle.assert_called_once_with("123", "seedance")


def test_dispatch_sbeng_does_not_touch_videoref_engine_toggle():
    """Isolation teeth: sbeng: (swapbatch) never reaches the videoref toggle."""
    bot = _get_bot_module()
    handler = MagicMock()
    handler.handle_set_engine.return_value = "ok"
    handler.build_quality_keyboard.return_value = {"inline_keyboard": []}
    cq = {
        "id": "cqid", "data": "sbeng:seedance",
        "message": {"chat": {"id": 123}, "message_id": 1},
        "from": {"id": 123},
    }
    with patch.object(bot, "answer_callback_query"), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_swapbatch_apply_reply"), \
         patch.object(bot, "send_with_keyboard"), \
         patch.object(bot, "_videoref_engine_toggle") as veng_toggle, \
         patch.object(bot, "_is_admin_id", return_value=True):
        bot.handle_callback_query(cq, {})
    veng_toggle.assert_not_called()


def test_vref_eng_prefix_covered_by_existing_friend_allowlist():
    """vref: prefix already friend-allowed (unchanged) -> vref:eng: needs no new
    allowlist entry."""
    bot = _get_bot_module()
    assert "vref:" in bot.FRIEND_ALLOWED_CALLBACK_PREFIXES
    assert "sbeng:" in bot.FRIEND_ALLOWED_CALLBACK_PREFIXES
    assert "anim:" in bot.FRIEND_ALLOWED_CALLBACK_PREFIXES
```

- [ ] **Step 7: Add `_videoref_do_animate` engine_mode tests (regression + seedance)**

```python
# ── _videoref_do_animate: engine_mode replaces hardcoded "spicy" ───────────────


def test_do_animate_default_engine_mode_is_spicy_byte_for_byte(monkeypatch):
    """CRITICAL regression: no engine_mode passed -> identical selection to today."""
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

    bot._videoref_do_animate(123, handler, Path("/tmp/s.jpg"), "m")
    assert fake_select.mode == "spicy"
    assert handler.build_single_animate_request.call_args.kwargs["engine_mode"] == "spicy"


def test_do_animate_seedance_engine_mode_threads_through(monkeypatch):
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
        123, handler, Path("/tmp/s.jpg"), "m", seconds=10, engine_mode="seedance"
    )
    assert fake_select.mode == "seedance"
    assert handler.build_single_animate_request.call_args.kwargs["engine_mode"] == "seedance"
```

- [ ] **Step 8: Add money-gate (`_videoref_swapanim_run`) engine_mode tests**

```python
# ── money gate reads engine_mode from pending ──────────────────────────────────


def test_gate_regression_no_engine_mode_defaults_to_spicy_est():
    """CRITICAL regression: pend without 'engine_mode' key -> gate est identical
    to pre-second-engine behavior."""
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
    }
    with patch.object(bot, "_check_limit", return_value=(True, "")) as cl, \
         patch.object(bot, "_videoref_swapanim_stages"), patch.object(bot, "send"):
        bot._videoref_swapanim_run("123", "/tmp/face.jpg")
    assert cl.call_args.kwargs["estimated_usd"] == bot._videoref_swapanim_est(10)


def test_gate_reads_est_with_engine_mode_seedance():
    bot = _get_bot_module()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "seedance",
    }
    with patch.object(bot, "_check_limit", return_value=(True, "")) as cl, \
         patch.object(bot, "_videoref_swapanim_stages"), patch.object(bot, "send"):
        bot._videoref_swapanim_run("123", "/tmp/face.jpg")
    assert cl.call_args.kwargs["estimated_usd"] == bot._videoref_swapanim_est(
        10, engine_mode="seedance"
    )
    assert cl.call_args.kwargs["estimated_usd"] != bot._videoref_swapanim_est(
        10, engine_mode="spicy"
    )
```

- [ ] **Step 9: Add billing (`_videoref_swapanim_stages`) engine_mode tests + quoted==charged**

```python
# ── per-stage billing reads engine_mode from pending ───────────────────────────


def test_stages_regression_no_engine_mode_defaults_spicy_byte_for_byte():
    """CRITICAL: pend without 'engine_mode' key -> do_animate called with
    engine_mode='spicy' and billed at spicy cost, identical to pre-arc behavior."""
    bot = _get_bot_module()
    handler = MagicMock()
    pend = {"best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10}
    with patch.object(bot, "_videoref_do_swap", return_value=Path("/tmp/s.jpg")), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_videoref_do_animate", return_value=Path("/tmp/v.mp4")) as an, \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot, "_send_local_video"), patch.object(bot, "send"):
        bot._videoref_swapanim_stages(
            "123", "/tmp/face.jpg", pend, bot._videoref_swapanim_est(10)
        )
    from app.services.block_m2_video.engines.capabilities import caps_for
    assert an.call_args.kwargs.get("engine_mode", "spicy") == "spicy"
    amounts = [c.args[2] for c in rec.call_args_list]
    assert amounts[1] == caps_for("spicy").cost_for(10, "720p")


def test_stages_seedance_charges_seedance_cost_and_selects_seedance_engine():
    bot = _get_bot_module()
    handler = MagicMock()
    pend = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "seedance",
    }
    with patch.object(bot, "_videoref_do_swap", return_value=Path("/tmp/s.jpg")), \
         patch.object(bot, "_swapbatch_get_handler", return_value=(handler, None)), \
         patch.object(bot, "_videoref_do_animate", return_value=Path("/tmp/v.mp4")) as an, \
         patch.object(bot._cost, "record_cost") as rec, \
         patch.object(bot, "_send_local_video"), patch.object(bot, "send"):
        bot._videoref_swapanim_stages(
            "123", "/tmp/face.jpg", pend,
            bot._videoref_swapanim_est(10, engine_mode="seedance"),
        )
    from app.services.block_m2_video.engines.capabilities import caps_for
    assert an.call_args.kwargs.get("engine_mode") == "seedance"
    amounts = [c.args[2] for c in rec.call_args_list]
    assert amounts[1] == caps_for("seedance").cost_for(10, "720p")


def test_quoted_equals_charged_for_seedance_path():
    """TEETH: seedance gate est == sum of per-stage records — quoted == charged
    holds on the new engine path exactly as it does on spicy."""
    bot = _get_bot_module()
    handler = MagicMock()
    bot._VIDEOREF_SWAP_PENDING[123] = {
        "best_frame": Path("f.jpg"), "motion_prompt": "x", "seconds": 10,
        "engine_mode": "seedance",
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
    assert gate["v"] == bot._videoref_swapanim_est(10, engine_mode="seedance")
    assert gate["v"] == charged
```

- [ ] **Step 10: Run the full new file, confirm every test fails for the right reason**

Run: `cd C:/jw/vref2eng && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_videoref_second_engine.py -v`

Expected: **all ~20 tests FAIL** (nothing implemented yet). Failure reasons should fall
into exactly these buckets — verify no test fails for an unrelated reason (e.g. an import
error, a typo, a wrong fixture):
- `_videoref_swapanim_est(...)` / `_videoref_do_animate(...)` calls with `engine_mode=`
  → `TypeError: ...got an unexpected keyword argument 'engine_mode'`
- `bot._videoref_engine_toggle` → `AttributeError: module ... has no attribute
  '_videoref_engine_toggle'`
- keyboard tests (`len(rows) == 3`, seedance durations) → `AssertionError` (today's
  keyboard has 2 rows, always spicy durations)
- `test_dispatch_vref_eng_seedance_routes_to_toggle` → `toggle.assert_called_once_with`
  fails (today's dispatch answers the callback and returns without calling anything named
  `_videoref_engine_toggle`)
- `test_handoff_defaults_engine_mode_to_spicy` → `KeyError: 'engine_mode'`
- gate/stages regression tests (`test_gate_regression_...`, `test_stages_regression_...`)
  → these use ONLY parameters that exist today, so they should actually **PASS already**
  — that is fine and expected (they encode current behavior; they must STAY green
  through every later task, that's the regression net)

- [ ] **Step 11: Commit the RED test file**

```bash
git add tests/test_videoref_second_engine.py
git commit -m "test(vref-eng): RED spy-teeth for /videoref second-engine toggle"
```

---

**🛑 CHECKPOINT 1 — STOP HERE.** Report back:
- full pytest output from Step 10 (pass/fail counts + failure reasons per bucket)
- confirm the regression tests (`test_gate_regression_...`, `test_stages_regression_...`)
  are the ones already green, and every other test fails for the expected reason above
- wait for explicit OK before starting Task 2

---

## Task 2: `_videoref_swapanim_est` gains `engine_mode`

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py:1396-1413`

- [ ] **Step 1: Replace the function**

Current (lines 1396-1413):
```python
def _videoref_swapanim_est(seconds: int = VIDEOREF_ANIM_SECONDS,
                           resolution: str = VIDEOREF_ANIM_RESOLUTION,
                           smooth: bool = False) -> float:
    """Single source for the Веха D quote == charge — parameterized by length AND
    smooth.

    swap ($0.02, length-independent) + spicy animate caps.cost_for(seconds, res)
    + (RIFE rife_surcharge_usd(1, seconds) when ``smooth``). EVERYTHING reads the
    price through here: the button labels, the check_limit gate, and the per-stage
    record_cost — so quoted == charged holds for ANY chosen duration and smooth
    choice (e.g. 10с=$1.02, 10с+smooth≈$1.12).
    """
    from app.services.block_m2_video.engines.capabilities import caps_for
    total = VIDEOREF_SWAP_USD + caps_for("spicy").cost_for(seconds, resolution)
    if smooth:
        from app.handlers.face_swap_handler import rife_surcharge_usd
        total += rife_surcharge_usd(1, seconds)
    return total
```

New:
```python
def _videoref_swapanim_est(seconds: int = VIDEOREF_ANIM_SECONDS,
                           resolution: str = VIDEOREF_ANIM_RESOLUTION,
                           smooth: bool = False,
                           engine_mode: str = "spicy") -> float:
    """Single source for the Веха D quote == charge — parameterized by length,
    smooth, AND engine (second-engine arc).

    swap ($0.02, length-independent) + caps_for(engine_mode).cost_for(seconds, res)
    + (RIFE rife_surcharge_usd(1, seconds) when ``smooth``). EVERYTHING reads the
    price through here: the button labels, the check_limit gate, and the per-stage
    record_cost — so quoted == charged holds for ANY chosen duration, smooth choice,
    AND engine (e.g. spicy 10с=$1.02, seedance 10с=<seedance cost>).
    engine_mode defaults to "spicy" — unchanged for any caller that doesn't pass it.
    """
    from app.services.block_m2_video.engines.capabilities import caps_for
    total = VIDEOREF_SWAP_USD + caps_for(engine_mode).cost_for(seconds, resolution)
    if smooth:
        from app.handlers.face_swap_handler import rife_surcharge_usd
        total += rife_surcharge_usd(1, seconds)
    return total
```

- [ ] **Step 2: Run the targeted tests**

Run: `cd C:/jw/vref2eng && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_videoref_second_engine.py -k "est_" -v tests/test_veha_d_swapanim.py`

Expected: `test_est_default_engine_mode_is_spicy_byte_for_byte` and
`test_est_engine_mode_seedance_uses_seedance_cost` PASS; every test in
`test_veha_d_swapanim.py` still PASSES unmodified (regression).

- [ ] **Step 3: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py
git commit -m "feat(vref-eng): _videoref_swapanim_est gains engine_mode param"
```

---

## Task 3: Handoff pending defaults `engine_mode: "spicy"`

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py:1604-1607`

- [ ] **Step 1: Add the default key to the handoff dict**

Current (lines 1604-1607):
```python
    _VIDEOREF_SWAP_PENDING[chat_id_int] = {
        "best_frame": best, "motion_prompt": result.prompt,
        "seconds": _proposed, "smooth": False,   # smooth default OFF (money-safe)
    }
```

New:
```python
    _VIDEOREF_SWAP_PENDING[chat_id_int] = {
        "best_frame": best, "motion_prompt": result.prompt,
        "seconds": _proposed, "smooth": False,   # smooth default OFF (money-safe)
        "engine_mode": "spicy",                  # default engine unchanged (money-safe)
    }
```

- [ ] **Step 2: Run the targeted test**

Run: `cd C:/jw/vref2eng && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_videoref_second_engine.py -k "handoff_defaults_engine_mode" -v tests/test_veha_d_swapanim.py`

Expected: `test_handoff_defaults_engine_mode_to_spicy` PASSES; all
`test_veha_d_swapanim.py` tests still PASS (they don't assert dict equality on
pending, only specific keys, so the new key doesn't break them).

- [ ] **Step 3: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py
git commit -m "feat(vref-eng): handoff stashes engine_mode default (spicy)"
```

---

## Task 4: `_videoref_duration_keyboard` becomes engine-aware + third row

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py:1416-1442`

- [ ] **Step 1: Replace the function**

Current (lines 1416-1442):
```python
def _videoref_duration_keyboard(chat_id_int: int) -> list:
    """Build the swap+animate keyboard for a chat's pending choice (I2.2 + I3.1).

    Row 1: one button per allowed length, each labelled with est(THAT length,
    current smooth) — the proposed length starred. Row 2: the RIFE smooth toggle.
    A single builder feeds both the initial handoff and the toggle redraw, so
    every price on screen is the single-source est for the current (seconds,
    smooth) — quoted stays == charged.
    """
    from app.services.block_m2_video.engines.capabilities import caps_for
    pend = _VIDEOREF_SWAP_PENDING.get(chat_id_int, {})
    proposed = pend.get("seconds", VIDEOREF_ANIM_SECONDS)
    smooth = pend.get("smooth", False)

    def _dur_btn(sec: int) -> dict:
        mark = "⭐ " if sec == proposed else "🎬 "
        return {
            "text": f"{mark}{sec}с ~${_videoref_swapanim_est(sec, smooth=smooth):.2f}",
            "callback_data": f"vref:sa:{sec}",
        }

    smooth_state = "ВКЛ" if smooth else "ВЫКЛ"
    smooth_cb = "vref:smooth:off" if smooth else "vref:smooth:on"
    return [
        [_dur_btn(s) for s in caps_for("spicy").allowed_durations],
        [{"text": f"🪶 Плавность 48fps: {smooth_state}", "callback_data": smooth_cb}],
    ]
```

New:
```python
def _videoref_duration_keyboard(chat_id_int: int) -> list:
    """Build the swap+animate keyboard for a chat's pending choice (I2.2 + I3.1 +
    second-engine).

    Row 1: one button per allowed length for the CURRENT engine, each labelled
    with est(THAT length, current smooth, current engine) — the proposed length
    starred. Row 2: the RIFE smooth toggle. Row 3: the engine toggle (label shows
    the current engine, tap switches to the other one). A single builder feeds
    every redraw (initial handoff, duration pick, smooth toggle, engine toggle),
    so every price on screen is the single-source est for the current (seconds,
    smooth, engine_mode) — quoted stays == charged.
    """
    from app.services.block_m2_video.engines.capabilities import caps_for
    pend = _VIDEOREF_SWAP_PENDING.get(chat_id_int, {})
    proposed = pend.get("seconds", VIDEOREF_ANIM_SECONDS)
    smooth = pend.get("smooth", False)
    engine_mode = pend.get("engine_mode", "spicy")
    caps = caps_for(engine_mode)

    def _dur_btn(sec: int) -> dict:
        mark = "⭐ " if sec == proposed else "🎬 "
        est = _videoref_swapanim_est(sec, smooth=smooth, engine_mode=engine_mode)
        return {
            "text": f"{mark}{sec}с ~${est:.2f}",
            "callback_data": f"vref:sa:{sec}",
        }

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

- [ ] **Step 2: Run the targeted tests**

Run: `cd C:/jw/vref2eng && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_videoref_second_engine.py -k "keyboard" -v tests/test_veha_d_swapanim.py`

Expected: all 3 keyboard tests in the new file PASS; every keyboard-related test in
`test_veha_d_swapanim.py` (`test_handoff_shows_three_duration_buttons_...`,
`test_handoff_default_smooth_off_with_toggle_button`,
`test_toggle_redraws_duration_buttons_with_smooth_price`, etc.) still PASSES — they
only ever read `rows[0]`/`rows[1]`, and the new engine row is appended as `rows[2]`.

- [ ] **Step 3: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py
git commit -m "feat(vref-eng): duration keyboard becomes engine-aware + third toggle row"
```

---

## Task 5: `_videoref_engine_toggle` handler + `vref:eng:` dispatch

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py` — new function after
  `_videoref_smooth_toggle` (currently ends line 1657, blank line 1658, next def at 1660)
- Modify: `tools/jarvis_smart_telegram_control.py:3662-3667` (the `vref:` dispatch block,
  inside `handle_callback_query`)

- [ ] **Step 1: Add the new handler function**

Insert immediately after `_videoref_smooth_toggle` (after line 1657, before the blank
line that precedes `def _videoref_face_intercept` at line 1660):

```python


def _videoref_engine_toggle(chat_id, mode: str) -> None:
    """Second-engine toggle (vref:eng:<mode>): switch engine + snap length + redraw.

    Mirrors _videoref_smooth_toggle. Snaps pend["seconds"] to the NEW engine's
    allowed_durations via caps_for(mode).snap_duration(...) so the redrawn keyboard
    never proposes/prices a length the new engine can't do (e.g. 15с WaveSpeed ->
    10с Seedance, its max). No spend here — same money-safe shape as the smooth
    toggle. Without pending → soft hint (mirrors the stale-button handling used by
    every other Веха D button).
    """
    from app.services.block_m2_video.engines.capabilities import caps_for
    chat_id_int = int(chat_id)
    pend = _VIDEOREF_SWAP_PENDING.get(chat_id_int)
    if pend is None:
        send(chat_id, "⚠️ Кнопка устарела — пришли видео заново: /videoref")
        return
    pend["engine_mode"] = mode
    pend["seconds"] = caps_for(mode).snap_duration(
        pend.get("seconds", VIDEOREF_ANIM_SECONDS)
    )
    send_with_keyboard(
        chat_id,
        f"🎬 Движок: {caps_for(mode).display_name}. Выбери длину:",
        _videoref_duration_keyboard(chat_id_int),
    )
```

- [ ] **Step 2: Run the handler tests (dispatch not wired yet, so skip those for now)**

Run: `cd C:/jw/vref2eng && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_videoref_second_engine.py -k "engine_toggle_switches or engine_toggle_back or engine_toggle_without_pending" -v`

Expected: all 3 PASS.

- [ ] **Step 3: Wire the dispatch — add the `eng` branch in the `vref:` block**

Current (lines 3662-3667):
```python
        if action == "smooth":
            # Веха D / I3.1: flip RIFE smooth + redraw prices (no spend, no arm).
            answer_callback_query(cq_id)
            _on = (len(parts) > 2 and parts[2] == "on")
            _videoref_smooth_toggle(chat_id, _on)
            return
```

New (insert the `eng` branch immediately after, before `answer_callback_query(cq_id);
return` fallback):
```python
        if action == "smooth":
            # Веха D / I3.1: flip RIFE smooth + redraw prices (no spend, no arm).
            answer_callback_query(cq_id)
            _on = (len(parts) > 2 and parts[2] == "on")
            _videoref_smooth_toggle(chat_id, _on)
            return
        if action == "eng":
            # Second-engine toggle: switch engine + snap length + redraw (no spend).
            answer_callback_query(cq_id)
            _mode = parts[2] if len(parts) > 2 else "spicy"
            _videoref_engine_toggle(chat_id, _mode)
            return
```

- [ ] **Step 4: Run the full new test file + isolation regression**

Run: `cd C:/jw/vref2eng && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_videoref_second_engine.py -v`

Expected: every test up through Task 5's scope now PASSES (est, handoff, keyboard,
toggle, dispatch, isolation, allowlist). The `_videoref_do_animate`/gate/stages tests
(Steps 7-9 from Task 1) are still expected RED — that's Tasks 6-8.

Also run: `cd C:/jw/vref2eng && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_veha_d_swapanim.py -v`

Expected: all still PASS (no regression).

- [ ] **Step 5: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py
git commit -m "feat(vref-eng): _videoref_engine_toggle handler + vref:eng: dispatch"
```

---

## Task 6: Thread `engine_mode` through `_videoref_do_animate`

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py:1763-1782`

- [ ] **Step 1: Replace the function**

Current (lines 1763-1782):
```python
def _videoref_do_animate(chat_id_int, handler, swapped, motion_prompt,
                         seconds=VIDEOREF_ANIM_SECONDS):
    """Stage-2 raw call (reuse 1:1, the _animate_run_single pattern): animate the
    swapped frame on WaveSpeed spicy (``seconds``/720p) by the motion prompt.

    engine_mode is explicitly "spicy" (uncensored — the point of the arc).
    Returns the video Path or None.
    """
    import asyncio as _aio
    from app.services.block_m2_video.engines.router import EngineRouter
    from app.services.block_m2_video.batch_animate import animate_batch

    req = handler.build_single_animate_request(
        chat_id_int, image_path=swapped, motion=motion_prompt,
        engine_mode="spicy",
        seconds=seconds, resolution=VIDEOREF_ANIM_RESOLUTION,
    )
    engine = _aio.run(EngineRouter().select("spicy"))
    results = _aio.run(animate_batch(engine, [req], concurrency=1))
    return results[0] if results else None
```

New:
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

- [ ] **Step 2: Run the targeted tests**

Run: `cd C:/jw/vref2eng && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_videoref_second_engine.py -k "do_animate" -v tests/test_veha_d_swapanim.py -k "do_animate"`

Expected: `test_do_animate_default_engine_mode_is_spicy_byte_for_byte` and
`test_do_animate_seedance_engine_mode_threads_through` PASS;
`test_do_animate_reuses_pattern_with_spicy_5s_720p` (existing, `test_veha_d_swapanim.py`)
still PASSES unmodified (default kwarg preserves it byte-for-byte).

- [ ] **Step 3: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py
git commit -m "feat(vref-eng): _videoref_do_animate selects engine via engine_mode param"
```

---

## Task 7: Thread `engine_mode` through the money gate (`_videoref_swapanim_run`)

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py:1727-1730`

- [ ] **Step 1: Pass engine_mode into the gate's est() call**

Current (lines 1727-1730):
```python
    est = _videoref_swapanim_est(
        pend.get("seconds", VIDEOREF_ANIM_SECONDS),
        smooth=pend.get("smooth", False),
    )
```

New:
```python
    est = _videoref_swapanim_est(
        pend.get("seconds", VIDEOREF_ANIM_SECONDS),
        smooth=pend.get("smooth", False),
        engine_mode=pend.get("engine_mode", "spicy"),
    )
```

- [ ] **Step 2: Run the targeted tests**

Run: `cd C:/jw/vref2eng && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_videoref_second_engine.py -k "gate_" -v tests/test_veha_d_swapanim.py`

Expected: `test_gate_regression_no_engine_mode_defaults_to_spicy_est` and
`test_gate_reads_est_with_engine_mode_seedance` PASS; every gate test in
`test_veha_d_swapanim.py` (`test_gate_est_is_single_source_...`,
`test_gate_reads_est_of_pending_seconds_not_hardcoded`,
`test_gate_reads_est_with_both_seconds_and_smooth`, etc.) still PASSES unmodified.

- [ ] **Step 3: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py
git commit -m "feat(vref-eng): money gate reads engine_mode from pending"
```

---

## Task 8: Thread `engine_mode` through billing (`_videoref_swapanim_stages`)

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py:1815, 1838-1849`

- [ ] **Step 1: Read engine_mode alongside seconds, pass it into do_animate + cost_for**

Current (line 1815):
```python
    seconds = pend.get("seconds", VIDEOREF_ANIM_SECONDS)   # chosen clip length (I2)
```

New (add the line right after):
```python
    seconds = pend.get("seconds", VIDEOREF_ANIM_SECONDS)   # chosen clip length (I2)
    engine_mode = pend.get("engine_mode", "spicy")         # chosen engine (second-engine arc)
```

Current (lines 1836-1849):
```python
    # ── Stage 2: animate (spicy <seconds>/720p). Record caps ONLY on success. ──
    try:
        video = _videoref_do_animate(
            chat_id_int, handler, swapped, motion_prompt, seconds
        )
    except Exception as exc:  # noqa: BLE001
        video = None
        print(f"[videoref] animate failed chat={chat_id_int}: {exc}", flush=True)
    if video is None:
        # Swap already charged ($0.02); we do NOT charge for the failed animation.
        send(chat_id, "❌ Анимация не удалась (свап готов, видео нет).")
        return
    from app.services.block_m2_video.engines.capabilities import caps_for
    anim_cost = caps_for("spicy").cost_for(seconds, VIDEOREF_ANIM_RESOLUTION)
```

New:
```python
    # ── Stage 2: animate (chosen engine, <seconds>/720p). Record caps ONLY on
    #    success. ──
    try:
        video = _videoref_do_animate(
            chat_id_int, handler, swapped, motion_prompt, seconds,
            engine_mode=engine_mode,
        )
    except Exception as exc:  # noqa: BLE001
        video = None
        print(f"[videoref] animate failed chat={chat_id_int}: {exc}", flush=True)
    if video is None:
        # Swap already charged ($0.02); we do NOT charge for the failed animation.
        send(chat_id, "❌ Анимация не удалась (свап готов, видео нет).")
        return
    from app.services.block_m2_video.engines.capabilities import caps_for
    anim_cost = caps_for(engine_mode).cost_for(seconds, VIDEOREF_ANIM_RESOLUTION)
```

- [ ] **Step 2: Run the targeted tests**

Run: `cd C:/jw/vref2eng && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_videoref_second_engine.py -k "stages_ or quoted_equals_charged" -v tests/test_veha_d_swapanim.py`

Expected: `test_stages_regression_no_engine_mode_defaults_spicy_byte_for_byte`,
`test_stages_seedance_charges_seedance_cost_and_selects_seedance_engine`, and
`test_quoted_equals_charged_for_seedance_path` PASS; every stages/billing test in
`test_veha_d_swapanim.py` (`test_both_stages_succeed_...`,
`test_animate_record_is_caps_cost_for_pending_seconds`,
`test_quoted_equals_charged_for_seconds_10`, RIFE smooth tests, etc.) still PASSES
unmodified.

- [ ] **Step 3: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py
git commit -m "feat(vref-eng): per-stage billing charges caps_for(engine_mode)"
```

---

## Task 9: Full regression + isolation sweep — CHECKPOINT 2 (final)

**Files:** none (verification only)

- [ ] **Step 1: Run the entire new spy-teeth file**

Run: `cd C:/jw/vref2eng && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_videoref_second_engine.py -v`

Expected: **all tests PASS** (0 failures).

- [ ] **Step 2: Run the full pre-existing videoref regression suite**

Run: `cd C:/jw/vref2eng && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/test_veha_d_swapanim.py tests/test_videoref_limits.py tests/test_videoref_motion_money.py tests/test_videoref_wiring.py -v`

Expected: **all tests PASS** — this is the baseline-green confirmation the user asked
for (confirmed baseline: 63 passed, 0 failed before this arc started; must still be fully green, byte-identical
behavior for the no-tap path).

- [ ] **Step 3: Run the isolation regression for `/animate` and `/swapbatch` engine
keyboards (untouched files, confirm no collateral breakage)**

Run: `cd C:/jw/vref2eng && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/ -k "animate or swapbatch or sbeng" -v`

Expected: **all PASS**, identical result to before this arc (these files were never
edited — this step exists to catch an accidental import-time side effect, not a
logic change).

- [ ] **Step 4: Run the full project test suite for a final sanity check**

Run: `cd C:/jw/vref2eng && /c/jarvis/.venv/Scripts/python.exe -m pytest tests/ -q`

Expected: same pass/fail counts as the pre-arc baseline (any pre-existing failures —
see `jarvis-preexisting-test-failures-techdebt` memory — are unrelated env/config
issues, not from this arc; zero NEW failures).

- [ ] **Step 5: Commit a summary (no code changes, just confirms the branch is green)**

```bash
git log --oneline -10
git status
```

(Nothing to commit here — this step is a verification checkpoint, not a code step.)

---

**🛑 CHECKPOINT 2 — STOP HERE (final).** Report back:
- full pass/fail counts from Steps 1-4
- explicit confirmation: default `spicy` path is byte-identical (regression tests
  green), `vref:eng:` isolated from `sbeng:`/`anim:` (isolation tests green)
- flag the two spec-documented risks as **live-checkpoint items, not blockers**:
  1. Seedance pricing is approximate (carried from the existing two-engine-animate
     design) — refine from real billing once used live.
  2. RIFE-on-Seedance-output is unconfirmed (low risk — RIFE operates on the output
     file, not engine-specific) — needs a live confirmation, not assumed.
- do NOT merge to `phase-4.0-unified-jarvis`, do NOT restart the bot, do NOT run a
  live video test — wait for explicit OK (live test budget to be discussed separately,
  costs cents not $0)
