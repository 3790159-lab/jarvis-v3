# /videoref Second Engine (Movable Engine Toggle) — Design

**Date:** 2026-07-03
**Status:** Approved (design); pending spec self-review → implementation plan
**Scope:** `/videoref` swap+animate flow only (`tools/jarvis_smart_telegram_control.py`).
No new engines, no core/animate/swapbatch changes.

## Goal

`/videoref` (reference-video → best-frame → face-swap → animate) currently hardcodes
`EngineRouter().select("spicy")` end-to-end — the user has no choice. Daniil asked for a
second engine, chosen per-run by the user (price/quality/content tradeoff), reusing the
existing `spicy` (WaveSpeed) / `seedance` (Seedance) engine pair that `/animate` and
`/swapbatch` already expose. No new engines are built — `WAVESPEED_CAPS`/`SEEDANCE_CAPS`
(`app/services/block_m2_video/engines/capabilities.py`) already describe both.

## Why this shape (chosen over alternatives)

`/videoref` already renders ONE screen with two live toggles — clip length (`vref:sa:<sec>`)
and RIFE smoothness (`vref:smooth:on|off`) — that redraw the same keyboard with recalculated
prices (`_videoref_duration_keyboard`, `control.py:1416-1442`). Engine choice becomes a
**third toggle row on that same keyboard**, not a separate screen.

Rejected alternative: a standalone engine-picker screen before the length/smooth keyboard,
mirroring `_animate_photo_intercept`'s separate step. Rejected because `/videoref` already
established the multi-toggle-single-screen pattern for this exact flow — adding a second
screen would be an extra tap and a second, inconsistent UI shape for no benefit.

## Design

### 1. Engine set (reuse, no new engines)

Exactly the two engines already shown everywhere else in the app (`build_engine_keyboard`,
`face_swap_handler.py:604-608`):

| mode | display | censorship | default? |
|---|---|---|---|
| `spicy` | WaveSpeed (без цензуры) | none | **yes** (unchanged default) |
| `seedance` | Seedance (дёшево, 10с/1080p) | SFW/censored | no |

Replicate/RunPod stay out of any user-facing engine picker (project-wide convention already
in place — the router supports 4 modes but the UI has only ever offered these 2).

### 2. Where it lives — third toggle row

`_VIDEOREF_SWAP_PENDING[chat_id]` gains `"engine_mode"` (default `"spicy"`, set at hand-off
alongside `best_frame`/`motion_prompt`/`seconds`/`smooth` — `control.py:~1604-1607`).

`_videoref_duration_keyboard` gains a third row:

```
🎬 Движок: WaveSpeed (без цензуры)   [vref:eng:seedance]   # tap → switch TO seedance
```

(label always shows the CURRENT engine; tapping switches to the OTHER one and redraws —
same interaction shape as the smooth toggle, one button that flips state.)

New handler `_videoref_engine_toggle(chat_id, mode)`:
- looks up pending (soft "кнопка устарела" hint if missing, mirrors `_videoref_smooth_toggle`)
- sets `pend["engine_mode"] = mode`
- **snaps the current `pend["seconds"]`** to the new engine's `allowed_durations` via
  `caps_for(mode).snap_duration(pend["seconds"])` — e.g. 15с (WaveSpeed) → 10с (Seedance,
  its max) — so the redrawn keyboard never proposes/prices a length the new engine can't do
- redraws via the existing `_videoref_duration_keyboard` (now engine-aware, see below)

New callback prefix `vref:eng:<mode>`, routed next to the existing `vref:sa:`/`vref:smooth:`
dispatch. `FRIEND_ALLOWED_CALLBACK_PREFIXES` already contains the full `"vref:"` prefix
(`control.py:6654`) — no allowlist change needed.

### 3. Engine-aware pricing (quoted == charged, unchanged principle)

`_videoref_swapanim_est` gains an `engine_mode: str = "spicy"` parameter, replacing the
hardcoded `caps_for("spicy")` internally with `caps_for(engine_mode)`. Default preserves
every existing call site's behavior byte-for-byte.

`_videoref_duration_keyboard` reads `pend.get("engine_mode", "spicy")` and uses
`caps_for(engine_mode).allowed_durations` for the length row and passes `engine_mode` into
every `_videoref_swapanim_est(...)` price label — so length buttons, prices, and the engine
row are always consistent with each other after any toggle (length, smooth, or engine).

`_videoref_swapanim_run` (money gate) and `_videoref_swapanim_stages` (per-stage billing)
both read `engine_mode = pend.get("engine_mode", "spicy")` and thread it into:
- the `check_limit` gate estimate (`_videoref_swapanim_est(..., engine_mode=engine_mode)`)
- `_videoref_do_animate(..., engine_mode=engine_mode)` — replaces the hardcoded
  `engine_mode="spicy"` at `control.py:1777`; `EngineRouter().select(engine_mode)` instead of
  `select("spicy")`
- the post-success `record_cost` amount — `caps_for(engine_mode).cost_for(seconds, ...)`
  instead of `caps_for("spicy").cost_for(...)`

Resolution stays fixed at `VIDEOREF_ANIM_RESOLUTION = "720p"` for both engines (unchanged —
both engines support 720p; per-engine resolution choice is out of scope, that's the separate
`/swapbatch_set_quality` axis already covering batch flows).

### 4. Not touched

- `_videoref_do_swap` (face-swap stage) — engine-agnostic already, no change.
- RIFE smooth stage (`_videoref_do_smooth`) — post-processes the resulting video file via
  WaveSpeed's RIFE API regardless of which engine generated it; no engine-lock in that call.
  Assumption carried into implementation for a live-checkpoint confirmation, not a design risk.
- `/animate` (`anim:` prefix), `/swapbatch` (`sbeng:`/`sbq:`/`sbward:` prefixes) — untouched;
  `vref:eng:` is a new, isolated prefix.
- Core (`app/services/vizir/`, coordinator, acceptance) — untouched, out of scope entirely.

## Testing (spy-teeth, TDD)

1. **Regression — no engine tap:** full `/videoref` swap+animate path produces byte-identical
   behavior to today (engine stays `spicy`, same prices, same `EngineRouter().select("spicy")`
   call, same cost recorded).
2. **Toggle changes caps + price:** tapping `vref:eng:seedance` redraws the keyboard with
   Seedance's `allowed_durations`/prices; tapping back to `vref:eng:spicy` restores WaveSpeed's.
3. **Duration snap on engine switch:** pending at 15с (WaveSpeed) + switch to Seedance →
   `pend["seconds"]` snaps to 10с (Seedance's max), keyboard shows 10с proposed, not 15с.
4. **Quoted == charged on the Seedance path:** `est(seconds, smooth, engine_mode="seedance")`
   equals the sum of per-stage `record_cost` calls when the run is accepted end-to-end.
5. **Isolation:** `/animate` and `/swapbatch` engine-keyboard tests still pass unmodified;
   `vref:eng:` never collides with `sbeng:`/`anim:` prefixes.
6. **Default integrity:** a chat that never taps the engine row ends up with
   `pend.get("engine_mode", "spicy") == "spicy"` at every read site.

## Files touched

Only `tools/jarvis_smart_telegram_control.py`:
- `_videoref_swapanim_est` — add `engine_mode` param
- `_videoref_duration_keyboard` — add engine row, engine-aware duration list
- new `_videoref_engine_toggle` handler + `vref:eng:` callback routing
- `_videoref_swapanim_run` / `_videoref_swapanim_stages` / `_videoref_do_animate` — thread
  `engine_mode` through instead of the hardcoded `"spicy"`
- pending dict hand-off — add `"engine_mode": "spicy"` default

No other file changes. `app/services/block_m2_video/engines/` (router, capabilities, both
engine implementations) already support this — read-only from this feature's perspective.

## Risks

1. **Seedance pricing is approximate** (carried from the existing two-engine-animate design,
   `2026-06-22-two-engine-video-animate-design.md`) — same caveat applies here, refine from
   real billing if/when this path is used live.
2. **RIFE-on-Seedance-output unconfirmed** — low risk (RIFE operates on the output video file,
   not engine-specific), but flagged for a live-checkpoint confirmation rather than assumed.
