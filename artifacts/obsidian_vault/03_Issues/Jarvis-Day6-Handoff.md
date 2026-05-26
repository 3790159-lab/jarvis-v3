# Jarvis Day 6 Handoff

Created May 26 2026.

## Shipped today — Animation Prompt UX (per-frame custom prompts)

Implemented the three-way branch after `/swapbatch_go` completes, designed with Daniil:

```
/swapbatch_animate_yes      ← all photos, default prompt (UNCHANGED)
/swapbatch_animate_custom   ← per-photo prompts via numbered list (NEW)
/swapbatch_no               ← skip animation, keep swaps only (NEW)
```

All 6 phases done, strict TDD throughout (test → RED → implement → GREEN), conventional commits per phase, no `--no-verify`, no skipped tests.

### Commits (on `phase-3.0-inventory-stop-reliability`, parent `47096d0`)
- `4ea9272` feat(face-swap): add numbered prompt parser for custom animation
- `bd5aee5` feat(face-swap): add custom-prompts state to batch orchestrator
- `1c1fd79` test(video): cover Wan 2.2 i2v positive-prompt injection
- `041f43d` feat(bot): wire custom-prompts flow into swapbatch handler
- `f7f2346` test(face-swap): integration tests for custom-prompts animation flow

### What was built per phase
- **Phase 1 (investigation):** see findings below. Key result — prompt injection already works, so Phase 4 was tests-only.
- **Phase 2:** `app/services/block_m2_face_swap/prompt_parser.py` — pure `parse_numbered_prompts(text, expected_count) -> ParseResult`. Accepts `1.`/`1)`/`1:`; empty or `/skip` → default (None); numbering gaps → default; out-of-range/duplicate/no-lines → `PromptParseError`; too-few/too-many contiguous runs → `mismatch_info`. 14 tests.
- **Phase 3:** `batch_orchestrator.py` — states `AWAITING_CUSTOM_PROMPTS` / `AWAITING_CUSTOM_PROMPTS_CONFIRM`; fields `custom_prompts` (1-based display idx → prompt|None) + `prompt_mismatch_info`; methods `start_custom_prompts`, `submit_custom_prompts`, `confirm_custom_animate`, `retry_custom_prompts`, `cancel_animate`. int-key coercion on JSON reload. 13 new tests (21 existing untouched).
- **Phase 4:** characterization tests only (no prod change — injection already worked). 3 tests in `test_runpod_comfy_engine.py`.
- **Phase 5:** handler methods `handle_animate_custom`, `handle_no`, `consume_custom_prompts_text`, `handle_retry`, `run_custom_animate_phase`, new `HandlerReply.numbered_photos`; bot commands + a text-intercept for numbered messages; per-idx prompt passed to engine with default substitution. 10 new handler tests.
- **Phase 6:** `test_swapbatch_animate_custom_integration.py` — 7 end-to-end smoke tests.

### Test status
- New tests: 14 (parser) + 13 (orchestrator) + 3 (engine) + 10 (handler) + 7 (integration) = **47 new, all green**.
- Full suite: **2374 passed, 8 skipped, 3 failed**. The 3 failures are **pre-existing and unrelated** — verified by re-running the full suite with all my tests removed (identical 3 failures, `3 failed, 2275 passed`):
  - `test_restaurant_mode.py::test_includes_dish_name` — mojibake in the test's own Cyrillic literal (encoding bug in the test).
  - `test_swapbatch_face_swap_engine.py::test_swap_batch_reuses_pod_and_uploads_source_once` and `...stops_pod_even_when_no_targets_succeed` — test-isolation fragility: both pass in isolation (20/20) and right after my integration tests (27/27); they fail only in full-suite ordering due to an unrelated polluting test. `face_swap_engine.py` is byte-identical to base and imports none of my modules.

## Phase 1 findings (for the record)
1. **Workflow file:** `app/services/block_m2_video/engines/workflows/wan22_i2v_v20.json` (the plan's candidate paths don't exist).
2. **Positive prompt node:** `"7"` — `CLIPTextEncode`, field `inputs.text`, default `"A cinematic video clip"`. Negative is node `"8"` (Chinese blob) — left untouched.
3. **Injection already works:** `runpod_comfy_engine._build_workflow()` injects `request.prompt` into node 7 with default fallback when falsy → **Phase 4 production change not needed**, only tests added.
4. **Swap result message:** swapped photos sent as a media-group album (first-caption-only). The custom flow re-displays them individually with `📸 N/M` captions via the new `numbered_photos` path; the album path for `_yes`/`_no` is unchanged.
5. **Orchestrator:** UPPERCASE string state constants (not enum), method-per-transition guarded by `_require(allowed)`, persisted to `state/face_swap/batches/{chat_id}/session.json`.

## Design decisions taken (confirmed with Daniil mid-session)
- **Phase 4 = tests only** (injection already works).
- **Default prompt = match `/swapbatch_animate_yes`** (`"a cinematic portrait, soft natural light"`): a None (default) custom-flow photo gets this exact string substituted in the bot wiring, so an all-default custom run is identical to `/swapbatch_animate_yes`. The orchestrator stays prompt-agnostic (passes None through).
- **Command namespacing:** in-confirm commands use the `/swapbatch_` prefix (`/swapbatch_confirm`, `/swapbatch_retry`, `/swapbatch_apply_partial`, `/swapbatch_apply_first`) — NOT bare `/confirm` etc. — because a global `/cancel` already exists. Use `/swapbatch_cancel` to abort the custom flow (it prunes cleanly from the awaiting states).
- **`/swapbatch_animate_no` kept** (maps to existing `skip_animate`); `/swapbatch_no` is the new alias routed through `cancel_animate`.
- **`/apply_first_N`** implemented as a single `/swapbatch_apply_first` (the message shows the actual count, e.g. "взять первые 5") to avoid dynamic-number command parsing.

## E2E flow to test against real Telegram bot (Daniil's next step)
1. `/swapbatch_source` → photo → `/swapbatch_batch` → album → `/swapbatch_go` → wait for swaps.
2. `/swapbatch_animate_custom` → bot re-sends swapped photos with `📸 N/M` + instructions.
3. Send a plain numbered message, e.g. `1. идёт по парку\n2. /skip\n3. танцует`.
4. Bot shows parsed preview with `(по умолчанию)` markers → `/swapbatch_confirm`.
5. Verify per-frame videos + tally (`N видео`, failures reported per idx).
6. Edge checks: bad text (parse error stays awaiting), too-few (`/swapbatch_apply_partial`), too-many (`/swapbatch_apply_first`), `/swapbatch_retry`, `/swapbatch_no`.

## Still open (out of scope today, carried forward)
- **Pre-existing test fragility:** the 2 `test_swapbatch_face_swap_engine` failures under full-suite ordering — find the polluting test (likely a global/singleton leak) and fix isolation. The mojibake `test_restaurant_mode::test_includes_dish_name` — re-save the test file as UTF-8.
- **Face validator InsightFace fix** (Day 5 carryover).
- **B-51 latent webhook bug** (`docs/B-51_INVESTIGATION.md` §6).
- **Untracked file cleanup** (the `)` file, `docs/*20260520*.md`, untracked scripts).
- **Future UX (proposed, not built):** negative-prompt UX; per-frame `/edit_N` after parse; saved custom-prompt templates/presets.

## Status (Day 6 animation prompt UX)
- HEAD after Day 6: `f7f2346` on `phase-3.0-inventory-stop-reliability`, pushed.

---

# Task C — Video quality (duration + fps)

Same session, after Day 6. Architectural; investigated first, design approved by Daniil (Option A + gated fps).

## Shipped (4 TDD sub-phases, all green, conventional commits)
- `3ce59cf` feat(video): quality settings parser + validator (`quality_settings.py`, 15 tests)
- `4e83f16` feat(video): variable duration and fps for animations (engine RIFE injection, 5 tests)
- `1a58eeb` feat(face-swap): per-batch quality settings in orchestrator (5 tests)
- `b7a56fc` feat(bot): wire /swapbatch_set_quality into swapbatch flow (7 handler tests)

**Test status:** +32 new tests, all green. Full suite **2406 passed, 8 skipped, 3 failed** — the same 3 pre-existing, unrelated failures as Day 6 (restaurant mojibake + 2 engine test-isolation cases); re-confirmed unchanged.

## Investigation findings (the important ones)
- **Frame source:** `bot _animate_fn → VideoRequest(seconds) → _build_workflow → node 15 (PainterI2VAdvanced) length`. `length = clamp(seconds×21, [21,315])`. NOT hardcoded to 53.
- **fps:** VHS_VideoCombine = node `21`, `frame_rate` was hardcoded 21, never injected. Native gen fps = length/seconds = **21**.
- **No interpolation nodes in the workflow.** BUT `ComfyUI-Frame-Interpolation` (Fannovel16, provides RIFE/FILM/GMFSS) **IS cloned by `runpod/bootstrap.sh:47`** — best-effort, unverified, not in the bootstrap verify section.
- **Architectural split:** duration is nearly free (already flows via `seconds`); **fps is the real new work** (RIFE node + frame_rate). 15s@42fps needs interp (630 played > 315 native ceiling).

## Design (approved)
- **UX = Option A:** `/swapbatch_set_quality duration=10 fps=42`, then `/swapbatch_animate_yes` or `_custom` use the set values. Unprovided keys keep current; no-args shows current.
- **Ranges:** duration **3–15s**; fps restricted to **{21, 42, 63, 84}** (RIFE needs integer multiplier = fps/21).
- **fps GATED** behind `ENABLE_FPS_INTERPOLATION` (default off). When off, `/swapbatch_set_quality` with fps>21 is rejected: *"FPS boost not yet verified on this pod — pending Day 7+ smoke test"*; duration still works; estimators never see fps (satisfied by construction — estimate runs at submit_targets before quality is set).
- **fps code path fully implemented + unit-tested** against the **assumed** RIFE schema (class `"RIFE VFI"`, inputs `ckpt_name=rife47.pth, frames, multiplier, clear_cache_after_n_frames, fast_mode, ensemble, scale_factor`). Engine is flag-agnostic; the gate lives in validation.

## ⚠️ Day 7 followup — REQUIRED before enabling fps>21
1. SSH to a running pod, query ComfyUI `/object_info`, capture the **real** RIFE VFI node class name + input schema as ground truth.
2. Diff against the assumed schema in `runpod_comfy_engine.py` (`_RIFE_*` constants + `_apply_fps`). If it matches → set `ENABLE_FPS_INTERPOLATION=1` and ship. If it differs → fix `_apply_fps` + re-run engine tests before flipping.
3. First production batch with fps>21 should be **1–2 photos** to catch runtime surprises (RIFE auto-downloads `rife4x.pth` on first use — watch for download/egress failures) before scaling.
4. Also verify `ComfyUI-VFI` actually installed (`runpod_inventory.py` can `ls custom_nodes` over SSH; the clone is best-effort).

## E2E to test (duration is live now; fps after flag flip)
- `/swapbatch_set_quality duration=10` → confirm "10 сек, 21 fps" → `/swapbatch_animate_yes` → ~10s videos.
- `/swapbatch_set_quality fps=42` (flag off) → rejection message, fps stays 21.
- `/swapbatch_set_quality` (no args) → shows current.
- After flag on: `/swapbatch_set_quality duration=8 fps=42` → RIFE ×2, 42fps output.

## Task C deferred / future
- **Duration-aware cost/time estimate:** the submit_targets estimate still assumes the default 5s (quality is set after the estimate). Re-estimate after `/swapbatch_set_quality`, or scale by duration, is a future refinement.
- fps=63 (3×) is allowed but untested on a pod; 42/84 are the natural 2×/4×.

## Status (after Task C)
- HEAD: `b7a56fc` on `phase-3.0-inventory-stop-reliability`. Push pending below.
