# Batch Video Animate on WaveSpeed (uncensored) — Design

**Date:** 2026-06-22
**Status:** Approved (design); pending spec review → implementation plan
**Scope:** STEP 2, FLOW 1 — after a `/swapbatch` swap, animate ALL swapped photos →
short videos via WaveSpeed `wan-2.6 spicy` (uncensored), behind the existing
pluggable `VideoGenerator` seam, with mandatory cost confirmation. Repoints the
(currently flag-disabled, RunPod-based) batch animate phase onto WaveSpeed.

## Goal

Let the user, after swapping up to 100 photos, animate every swapped result into a
short video on a RELIABLE, UNCENSORED path (WaveSpeed), choosing length (5/10/15s)
and quality (720/1080p), with a hard cost-confirmation gate before any paid call —
without ever invoking the frozen RunPod video engine.

## Out of scope (FLOW 2 / later)

- Standalone "animate any photo" command (`/animate`).
- User-facing ENGINE CHOICE UI (WaveSpeed vs Replicate). The architecture below
  makes this a config/mode switch with no rework, but the selection UX is later.
- Audio, fps control (WaveSpeed outputs native ~30fps; not user-controllable).
- RunPod path (stays frozen, never called).

## Verified before this spec (live test, 2026-06-22, ~$1.50)

One real WaveSpeed clip from the user's content confirmed:
- **Uncensored:** full explicit output, NO censorship (no black frames / blur /
  safety-checker). This is the requirement Replicate/fal cannot meet.
- **15s real:** 830×1110, 30fps, 450 frames, exactly 15.0s; generation ~90s.
- **Input:** local file as `data:image/jpeg;base64,…` accepted (no hosting needed).
- **API:** `POST api.wavespeed.ai/api/v3/alibaba/wan-2.6/image-to-video-spicy`,
  `Authorization: Bearer <WAVESPEED_API_KEY>`, submit→poll
  (`data.urls.get` / `/api/v3/predictions/{id}/result`) → `outputs[0]` mp4 URL.
- **Consistency caveat:** 15s is internally multi-shot — scene/framing drift toward
  the end (anatomy/quality hold, no melting). 5s is a single continuous take.
  Hence the user-chosen length (5/10/15) is a real quality/cost lever.

## Existing infrastructure to reuse (recon)

The pluggable video seam already exists (same shape as our `SwapEngine`):
- `app/services/block_m2_video/engines/engine_protocol.py` — `VideoGenerator`
  Protocol (`engine_name`, `async is_available()`, `async generate(VideoRequest)
  → VideoResult`), plus `VideoRequest` / `VideoResult` dataclasses.
- `EngineRouter` (`router.py`) — factory selecting an engine by `mode`
  (`fast`→Replicate, `hq`→RunPod, `auto`). We add `spicy`→WaveSpeed.
- `ReplicateEngine` (Wan 2.5, censored) — KEPT as the future alternative engine.
- `RunpodComfyEngine` — frozen; not called.
- Batch animate today: `tools/jarvis_smart_telegram_control.py:_swapbatch_run_phase`
  animate branch hard-codes `RunpodComfyEngine()` with `mode="hq"`. This is the
  single repoint target.
- Orchestrator animate transitions (`confirm_animate`) + `generation_lock` exist.

## Architecture

### 1. New engine in the existing seam

`app/services/block_m2_video/engines/wavespeed_spicy_engine.py` — new
`WaveSpeedSpicyEngine` implementing `VideoGenerator`:
- `engine_name = "wavespeed_spicy"`, model `alibaba/wan-2.6/image-to-video-spicy`.
- `generate(VideoRequest)`: base64 the `input_image_path` → data-URI; POST with
  `image`, `prompt`, `duration` (∈{5,10,15}), `resolution` (∈{720p,1080p}),
  `negative_prompt`, `seed`, `enable_prompt_expansion`; poll to completion;
  download mp4 to a local Path; return `VideoResult` (engine, model, `cost_usd`
  from the pricing table, output_path, …).
- Reads `WAVESPEED_API_KEY` from env (never logged).
- 429 / network resilience: patient backoff (mirror the lucataco client pattern),
  raising a `WaveSpeedTransientError` (subclass of RuntimeError) on exhausted
  transient retries so the animate runner can distinguish retryable from terminal.

Selection: register it in `EngineRouter` under `mode="spicy"` and make the batch
animate phase request `mode="spicy"`. (FLOW 2 later lets the user pick the mode →
WaveSpeed vs Replicate — no rework, the router already dispatches by mode.)

### 2. VideoRequest extensions (shared dataclass)

Add two OPTIONAL fields (defaults keep Replicate/RunPod behavior unchanged):
- `resolution: str = "720p"`
- `negative_prompt: str = ""`

`duration`/`seconds` is validated/snapped to `{5,10,15}` for the WaveSpeed engine.
`fps` stays (RunPod uses it); WaveSpeed ignores it.

### 3. Cost model + MANDATORY confirmation (money safety)

WaveSpeed pricing table (baked into the engine as the single source of truth):

| | 5s | 10s | 15s |
|---|---|---|---|
| 720p | $0.50 | $1.00 | $1.50 |
| 1080p | $0.75 | $1.50 | $2.25 |

- Engine exposes `cost_for(seconds, resolution) -> float` and per-result `cost_usd`.
- **Before any WaveSpeed call**, the animate phase shows an estimate:
  `N videos × {seconds}s × {resolution} = ~${total}` plus a wall-clock estimate,
  and requires an EXPLICIT confirm command to proceed. 100×15s×720p = **$150** —
  this gate is non-negotiable. (Same pattern as swap's `/swapbatch_go`.)
- **Money invariant** (carried from swap): a per-video TERMINAL/billable failure
  (a completed-but-failed prediction) is NEVER retried; only TRANSIENT
  (`WaveSpeedTransientError`: 429/network, no prediction created) is retried.

### 4. Concurrency, partial failure, progress (reuse swap patterns)

- Animate runs videos with a concurrency cap `SWAPBATCH_ANIMATE_CONCURRENCY`
  (env, default **2**) + patient 429 backoff + a final transient-only retry sweep
  — mirroring the proven swap engine. (100×~90s sequential ≈ 2.5h; cap=2 ≈ ~75min;
  the cost-confirmation message states the time estimate so the user isn't
  surprised.)
- Per-video fault isolation: one failed video doesn't abort the batch; failed
  targets get `animate_result_path=None`. Honest tally: "🎬 N/100 видео готово,
  M не удалось".
- Progress batched (every ~10): "🎬 Animate N/100…".

### 5. Settings UX

- `/swapbatch_set_quality duration=5|10|15 resolution=720p|1080p` (extend the
  existing command; `fps` dropped from the user surface for the WaveSpeed path).
  Defaults: **duration 10s, resolution 720p**.
- Default motion prompt used when none supplied; `negative_prompt` has a sane
  default. (Per-photo custom prompts already exist in the orchestrator flow and
  continue to work.)

### 6. Flag (isolation)

`SWAPBATCH_ANIMATE_ENABLED` stays the gate, **default OFF**. When OFF: animate
commands are inert (as today). When ON: animate routes to **WaveSpeed via
`mode="spicy"`** (never RunPod), and still passes through the cost-confirmation
gate. RunPod engine remains a separate frozen implementation.

### 7. Delivery

Swapped videos delivered one-by-one via the existing `_send_local_video`
(Telegram `sendVideo`, 50MB/clip cap — a 15s 720p clip ≈ ~13MB, safe). Throttle
between sends to avoid Telegram rate limits.

## Repoint summary (the core change)

`_swapbatch_run_phase` animate branch: replace `RunpodComfyEngine()` /
`mode="hq"` with the router selecting `mode="spicy"` (WaveSpeed). The animate
`VideoRequest` carries the session's duration/resolution/prompt. Everything
downstream (`confirm_animate`, lock, delivery, tally) is reused.

## Error handling

- Missing `WAVESPEED_API_KEY` → clear error, animate refused (no crash).
- Submit 4xx (non-429) → terminal, per-video failure, not retried.
- 429/network exhausted → transient, eligible for the retry sweep.
- Poll timeout → per-video failure.
- Telegram send failure → logged, doesn't lose the saved local mp4.

## Testing

All offline on mocks (`httpx.MockTransport`) until a live ~$1–2 checkpoint:
- Engine: submit/poll/download happy path; `output=None`/failed → terminal (not
  retried); 429 → `WaveSpeedTransientError` then sweep recovers; duration snap to
  {5,10,15}; cost table values; base64 data-URI built.
- Cost estimate: N×per-video math; confirmation text.
- Money test: a terminal per-video failure is submitted exactly once.

## Scope estimate (~1 working day, FLOW 1)

| Block | What | Size |
|---|---|---|
| Engine | `WaveSpeedSpicyEngine` + `WaveSpeedTransientError` + pricing table | M |
| Router | register `mode="spicy"` → WaveSpeed | S |
| VideoRequest | add `resolution`, `negative_prompt` | S |
| Repoint | batch animate branch RunPod→WaveSpeed (`mode="spicy"`) | S |
| Cost gate | animate cost+time estimate + explicit confirm step | M |
| Concurrency | cap + 429 backoff + transient retry sweep + per-video isolation | M |
| Settings | `/swapbatch_set_quality` duration{5/10/15}+resolution{720/1080} | S |
| Flag | `SWAPBATCH_ANIMATE_ENABLED` routes to WaveSpeed when ON | S |
| Tests | engine (mocked), cost, money-invariant | M |

## Risks

1. **Cost blow-up** — 100×15s = $150. Mitigated by the mandatory cost+time
   confirmation gate + flag OFF by default. Highest-priority guard.
2. **Time** — 100 videos is long even at cap=2 (~75min). Surfaced in the
   confirmation. User often animates a subset.
3. **WaveSpeed rate limits** — concurrency cap + patient backoff + transient sweep.
   Tier-dependent; tune `SWAPBATCH_ANIMATE_CONCURRENCY` from logs.
4. **15s multi-shot drift** — accepted/user-chosen; 5s is continuous.
5. **Key security** — the test key was exposed in chat; must be rotated. Engine
   reads from `.env`, never logs the key.
6. **Money invariant regression** — terminal failures must never re-submit;
   enforced by `WaveSpeedTransientError` classification + a submit-count test.
7. **Don't wake RunPod** — router `spicy` path never constructs RunpodComfyEngine.

## Forward-compat (FLOW 2)

Because selection is by `EngineRouter` mode and `ReplicateEngine` is retained, the
future engine-choice feature = expose mode selection (e.g. `spicy` vs `fast`) in
the UI + a per-request/engine default. No change to the engine, orchestrator, or
delivery. Standalone `/animate` = a new entry point that builds a `VideoRequest`
and calls the same router.
