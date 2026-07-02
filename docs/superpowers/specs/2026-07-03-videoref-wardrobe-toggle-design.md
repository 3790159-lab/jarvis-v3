# /videoref Wardrobe Toggle ("Не раздевать") — Design

**Date:** 2026-07-03
**Status:** Approved (design); implementation via TDD subagent-driven, spy-teeth first.
**Scope:** `/videoref` swap+animate flow only (`tools/jarvis_smart_telegram_control.py` +
`app/handlers/face_swap_handler.py`). No new engines, no core/animate/swapbatch changes.

## Goal

A live test (Daniil, 2026-07-03) showed `/videoref` exposing/undressing the subject with no
user control. Root cause: `/videoref` never passes a `wardrobe` argument anywhere in its
animate path, so `assemble_animate_prompt` silently falls back to its own internal default
`wardrobe="safe"` (`prompt_assembly.py:44`) — a **negative-only** mode (no positive clothing
anchor) that `negative_prompt` (WaveSpeed-only, `engine_protocol.py:32`) does not reliably
enforce. This predates the second-engine arc (confirmed: it is not a regression from
`vref-eng`, `cbf661e`).

`/swapbatch` already has a working three-mode wardrobe system (`preserve`/`safe`/`spicy`,
button `🩱 Не раздевать`, defaulting to `preserve`) that is *technically* what "не раздевать"
means — `preserve` adds a positive clothing anchor (`"keeping original clothing, same outfit,
fully dressed, clothing unchanged"`) plus a stronger negative, not just `safe`'s bare negative.
This design reuses that existing, already-generic mechanism for `/videoref`, exactly the way
the second-engine arc reused the existing engine-router/capabilities mechanism.

## Why this shape (chosen over alternatives)

`/videoref` already renders one screen with two live toggles (length `vref:sa:<sec>`, RIFE
smooth `vref:smooth:on|off`) that just gained a third (engine `vref:eng:<mode>`,
`cbf661e`). Wardrobe becomes a **fourth toggle row on that same keyboard** — consistent with
the established multi-toggle-single-screen pattern for this flow, not a new screen.

`assemble_animate_prompt` already accepts `wardrobe` generically (used today by `/swapbatch`
via `assemble_custom_animate_prompts`/direct calls) — no engine-side or prompt-assembly change
is needed. This is purely a plumbing gap in `/videoref`'s own call sites.

## Design

### 1. Wardrobe set (reuse, no new modes)

Reuse the existing `WARDROBE_MODES = ("preserve", "safe", "spicy")` (`prompt_assembly.py:14`).
The toggle button only flips between the two the `/swapbatch` button already flips between —
`safe` stays in the enum but is not reachable via the button (mirrors `/swapbatch` exactly).

| mode | meaning | reachable via button? |
|---|---|---|
| `preserve` | positive clothing anchor + strong anti-undress negative | yes (default) |
| `spicy` | no constraints | yes (tap) |
| `safe` | negative-only, weak | no (enum only, not a button target) |

### 2. Default changes: `safe` (accidental) → `preserve` (explicit, safer)

`_VIDEOREF_SWAP_PENDING[chat_id]` gains `"wardrobe_mode"`, set at hand-off alongside
`best_frame`/`motion_prompt`/`seconds`/`smooth`/`engine_mode` (`control.py:~1615-1619`),
**default `"preserve"`** — mirrors `/swapbatch`'s own default (`batch_orchestrator.py:115`).

This is an intentional behavior change to existing `/videoref` (today's accidental `safe`
becomes explicit `preserve`) — a safety improvement, not a regression. Locked in by spy-tooth
#1 below.

### 3. Where it lives — fourth toggle row

`_videoref_duration_keyboard` gains a fourth row:

```
🩱 Не раздевать: ВКЛ   [vref:ward:spicy]   # tap → switch TO spicy (raunchy, off)
```

(label shows current state; tapping flips to the other — same interaction shape as the smooth
and engine toggles.)

New handler `_videoref_wardrobe_toggle(chat_id, mode)`:
- looks up pending (soft "кнопка устарела" hint if missing, mirrors `_videoref_smooth_toggle`
  / `_videoref_engine_toggle`)
- validates `mode` against the two button-reachable values (`preserve`/`spicy`); invalid input
  falls back to `preserve` (mirrors the invalid-mode handling in `_videoref_engine_toggle`,
  which falls back to `spicy` for engine — here the safe fallback is `preserve`)
- sets `pend["wardrobe_mode"] = mode`
- redraws via `_videoref_duration_keyboard` (now wardrobe-aware)

New callback prefix `vref:ward:<mode>`, routed next to the existing `vref:sa:`/`vref:smooth:`/
`vref:eng:` dispatch inside the existing `if data.startswith("vref:")` block
(`control.py:3688`). `FRIEND_ALLOWED_CALLBACK_PREFIXES` already contains the full `"vref:"`
prefix — no allowlist change needed.

### 4. Plumbing — thread `wardrobe` through (mirrors `engine_mode` 1:1)

`build_single_animate_request` (`face_swap_handler.py:669-697`) gains a
`wardrobe: str | None = None` kwarg, passed into
`assemble_animate_prompt(..., wardrobe=wardrobe or "preserve")` — replacing the current call
that omits `wardrobe` entirely (today's `assemble_animate_prompt(...)` call at line 688-691
has no `wardrobe=` at all, so it silently uses the function's own `"safe"` default; this
design makes `/videoref`'s intended default explicit at the call site instead of relying on
an unrelated function's fallback).

`_videoref_do_animate` (`control.py:1804-1824`) gains a `wardrobe_mode="preserve"` parameter,
passed into `build_single_animate_request(..., wardrobe=wardrobe_mode)`.

`_videoref_swapanim_stages` (`control.py:1844`) reads
`wardrobe_mode = pend.get("wardrobe_mode", "preserve")` (mirrors the `engine_mode` read at
line 1858) and passes it into the `_videoref_do_animate(...)` call (mirrors line 1882-1885).

No pricing change: `_videoref_swapanim_est` is untouched — wardrobe affects only the prompt
text sent to the engine, not `caps_for(...).cost_for(...)`. `quoted == charged` is unaffected
because wardrobe never enters the cost formula.

### 5. Not touched

- `_videoref_swapanim_est`, `_videoref_swap_do` (face-swap stage, wardrobe-agnostic already).
- `assemble_animate_prompt` / `_WARDROBE_TABLE` / `WARDROBE_MODES` (`prompt_assembly.py`) —
  already fully generic, read-only from this feature's perspective.
- `/animate` (`anim:` prefix), `/swapbatch` (`sbward:`/`sbeng:`/`sbq:` prefixes) and
  `batch_orchestrator.set_wardrobe`/`handle_wardrobe_button` — untouched; `vref:ward:` is a
  new, isolated prefix and pending key, independent of the batch-swap session's own
  `wardrobe_mode` field.
- `engine_mode` toggle/plumbing (`vref-eng` arc, `cbf661e`) — untouched; the two toggles are
  independent axes that both thread into the same `_videoref_do_animate` call without
  interfering with each other.
- Core (`app/services/vizir/`, coordinator, acceptance) — untouched, out of scope entirely.

## Testing (spy-teeth, TDD)

1. **Default integrity (CRITICAL — this IS the fix):** a chat that never taps the wardrobe
   row ends up with `pend.get("wardrobe_mode", "preserve") == "preserve"` at every read site,
   and the assembled prompt for that default run contains the `preserve` positive anchor
   (`"keeping original clothing"`) — i.e. today's `/videoref` no longer undresses by default.
2. **Toggle flips + prompt changes:** tapping `vref:ward:spicy` sets `wardrobe_mode="spicy"`
   and the next assembled prompt drops the positive anchor / negative (`spicy` = no
   constraints); tapping back to `vref:ward:preserve` restores it.
3. **`preserve` adds a positive anchor, not just a negative:** direct assertion that the
   `preserve`-mode prompt sent to `build_single_animate_request`/`assemble_animate_prompt`
   contains `"keeping original clothing"` (positive), distinguishing it from the old
   accidental `safe` fallback (negative-only).
4. **Independence:** `engine_mode` and `wardrobe_mode` toggle and thread through
   independently — switching one does not reset or read the other; a single run with both
   set (e.g. `seedance` + `spicy`) passes both correctly to `_videoref_do_animate`.
5. **`/swapbatch` isolation:** `sbward:`/`handle_wardrobe_button`/`set_wardrobe` tests still
   pass unmodified; `vref:ward:` never collides with `sbward:`.
6. **Money-neutral:** `_videoref_swapanim_est(...)` output is byte-identical regardless of
   `wardrobe_mode` — wardrobe never enters the cost formula, `quoted == charged` unaffected.

## Files touched

- `tools/jarvis_smart_telegram_control.py`:
  - `_videoref_duration_keyboard` — add wardrobe row, wardrobe-aware label
  - new `_videoref_wardrobe_toggle` handler + `vref:ward:` callback routing
  - `_videoref_swapanim_stages` / `_videoref_do_animate` call site — thread `wardrobe_mode`
  - pending dict hand-off — add `"wardrobe_mode": "preserve"` default
- `app/handlers/face_swap_handler.py`:
  - `build_single_animate_request` — add `wardrobe` param, pass through to
    `assemble_animate_prompt`

No other file changes. `app/services/block_m2_video/prompt_assembly.py` (wardrobe table,
`assemble_animate_prompt`) already supports this — read-only from this feature's perspective.

## Risks

1. **Default-behavior change is intentional but real** — existing `/videoref` output will
   change (less exposure) for anyone who never taps the wardrobe button. This is the explicit
   goal, not a side effect, but flagged because it changes byte-for-byte output of an existing
   flow (unlike the second-engine arc, which was byte-identical by default).
2. **`negative_prompt` is WaveSpeed-only** (`engine_protocol.py:32`) — `preserve`'s positive
   anchor lives in the main `prompt`, which IS engine-agnostic, so Seedance/other engines still
   benefit from the positive anchor even where the negative is ignored.
