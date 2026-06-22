# Two-Engine Video Animate (WaveSpeed + Seedance) — Design

**Date:** 2026-06-22
**Status:** Approved (design); pending spec review → implementation plan
**Supersedes:** `2026-06-22-batch-video-animate-wavespeed-design.md` (WaveSpeed-only FLOW 1).
That spec's WaveSpeed engine + plan remain valid as **PHASE A**; this doc widens scope to
**two engines + engine choice + per-engine quality + standalone `/animate`**.

## Goal

Let the user animate photos → short videos through **two turnkey, managed engines**, choosing
**engine, prompt, length, and resolution** in the UI, with a hard cost-confirmation gate before
any paid call, and **no RunPod**:

1. **WaveSpeed `wan-2.6 spicy`** (DEFAULT) — uncensored, for skin/explicit. 5/10/15s × 720/1080p.
2. **Replicate `bytedance/seedance-1-pro-fast`** (OPTION) — censored/SFW, cheap, for decent
   content. 5/10s × 480/720/1080p.

Two entry points share the same engine seam, runner, cost gate, and delivery:
- **After `/swapbatch`** swap → menu animates all swapped photos.
- **Standalone `/animate`** → animate 1..N (≤20) arbitrary photos.

## Verified facts (live, 2026-06-22 — discipline: test, don't guess)

**WaveSpeed wan-2.6 spicy** (live clip, ~$1.50):
- Uncensored full explicit output; 15s real (830×1110, 30fps, 450 frames, ~90s generation).
- API `POST api.wavespeed.ai/api/v3/alibaba/wan-2.6/image-to-video-spicy`, Bearer auth,
  submit→poll (`data.urls.get`) → `outputs[0]` mp4. base64 data-URI input accepted.
- Schema: `duration` ∈ {5,10,15}, `resolution` ∈ {720p,1080p}, `shot_type` single/multi,
  `negative_prompt`, `enable_prompt_expansion`, `seed`. **No fps param** — fixed native ~30fps.
- 15s is internally multi-shot (mild scene drift near the end; anatomy holds). 5s is one take.

**Seedance `bytedance/seedance-1-pro-fast`** (live clip 5s/1080p, ~$0.25 — Grace Hopper):
- Output **1248×1664, exactly 24.000 fps, 121 frames = 5.04s**, 8.9MB. Identity/motion
  consistency **excellent** (no melting across full clip); natural head-turn + smile as prompted.
- **Cold-start: warm** — ~5s queue → processing, 144s total wall (NOT wan-2.2's ~460s).
- Schema (Replicate metadata, authoritative): `prompt` **required**, `image` (i2v first frame),
  `duration` int **2–12** (we offer 5/10), `resolution` ∈ **{480p,720p,1080p}** (default 1080p),
  `fps` fixed **24**, `aspect_ratio` (**ignored when image supplied** → output adapts to source
  aspect), `camera_fixed`, `seed`. No audio. Managed/SFW (fine for decent content).
- Tarpit defense (carried from Replicate recon): browser **User-Agent** + base64 **data-URI**
  on `replicate.Client` / httpx — bare SDK file-handle gets Cloudflare-tarpitted.

**Why Seedance over wan-2.2 (rejected):** wan-2.2-i2v-fast is frame-capped (`num_frames` max
121) → only ~5s, 480p-only, slow. Seedance-1-pro-fast gives real 5/10s, true 1080p, 24fps,
warm start, better consistency, comparable cost. wan-2.2 is dropped entirely.

## Engine cost tables (single source of truth, baked per engine)

**WaveSpeed** (verified):

| | 5s | 10s | 15s |
|---|---|---|---|
| 720p | $0.50 | $1.00 | $1.50 |
| 1080p | $0.75 | $1.50 | $2.25 |

**Seedance-1-pro-fast** (Replicate, token-based; **approx — refine from first real billing**):

| | 5s | 10s |
|---|---|---|
| 480p | ~$0.05 | ~$0.10 |
| 720p | ~$0.11 | ~$0.22 |
| 1080p | ~$0.25 | ~$0.55 |

Worst-case batch ceilings (100 photos) surfaced in the cost gate:
**WaveSpeed 100×15s×1080p = $225**; **Seedance 100×10s×1080p ≈ $55**.

## Out of scope (later)

- Per-photo **individual** prompts (shared-batch prompt only for now; orchestrator scaffolding
  exists but stays deferred off the managed path).
- Seedance native-audio / reference-image multimodal extras, `camera_fixed`, `shot_type`.
- The frozen RunPod engine (never called) and the deprecated `ReplicateEngine` (Wan-2.5 SDK,
  kept only for `/persona_video`).
- fps control (managed engines don't expose it usefully — see §4).

## Architecture

### 1. Engine seam — two twin engines behind `VideoGenerator`

Both engines implement the existing `VideoGenerator` protocol; `EngineRouter` dispatches by `mode`:

| mode | engine | censorship | quality | status |
|---|---|---|---|---|
| `spicy` | **WaveSpeedSpicyEngine** (DEFAULT) | none | 5/10/15s × 720/1080p, 30fps | PHASE A |
| `seedance` | **ReplicateSeedanceEngine** (NEW) | SFW | 5/10s × 480/720/1080p, 24fps | PHASE B |
| `fast` | `ReplicateEngine` (Wan-2.5 SDK) | — | — | deprecated, untouched (`/persona_video`) |
| `hq`/`auto` | RunPod | — | — | frozen, never called |

New engine files (structural twins — same httpx submit→poll→download skeleton, same
transient/terminal split, same pricing-table source of truth):
- `engines/wavespeed_spicy_engine.py` — `WaveSpeedSpicyEngine` (PHASE A; per the superseded spec).
- `engines/replicate_seedance_engine.py` — `ReplicateSeedanceEngine` (PHASE B):
  - model `bytedance/seedance-1-pro-fast`; httpx + **browser User-Agent** + base64 **data-URI**.
  - `generate(VideoRequest)`: snap `duration`/`resolution` to its own allowed set; POST
    `image`, `prompt`, `duration`, `resolution`, `seed`; poll to completion; download mp4;
    return `VideoResult` (engine, model, `cost_usd` from its table, output_path, …).
  - `ReplicateSeedanceTransientError` / `ReplicateSeedanceEngineError` split (429/network/tarpit
    timeout = transient → retry/sweep; completed-failed / 4xx = terminal, billable → never retry).
  - Reads `REPLICATE_API_TOKEN`; never logs it.

### 2. Per-engine capability descriptor (drives the WHOLE UI)

Each engine exposes a descriptor — **the UI, cost gate, and validation read ONLY from this**, so
the UI offers exactly what the engine can do (Seedance never offers 15s; WaveSpeed never offers
an fps knob or 480p):

```
EngineCapabilities:
  engine_mode:        "spicy" | "seedance"
  display_name:       "WaveSpeed (без цензуры)" | "Seedance (дёшево, 10с/1080p)"
  allowed_durations:  WaveSpeed [5,10,15]   | Seedance [5,10]
  allowed_resolutions:WaveSpeed [720p,1080p]| Seedance [480p,720p,1080p]
  default_duration:   10                    | 5
  default_resolution: 720p                  | 1080p
  native_fps:         30 (info-only)        | 24 (info-only)
  cost_for(seconds, resolution) -> float    # from the engine's table
  gen_seconds(seconds) -> int               # rough wall-clock for time estimate
  censored:           False                 | True
```

`snap_duration` / `snap_resolution` clamp any out-of-range request to the engine's allowed set.

### 3. VideoRequest extensions (shared dataclass)

Add OPTIONAL fields (defaults preserve Replicate/RunPod behavior):
- `resolution: str = "720p"`
- `negative_prompt: str = ""` (WaveSpeed only; Seedance ignores)

`fps` stays for the frozen RunPod path; **both managed engines ignore the user surface**
(WaveSpeed has no fps param; Seedance pins 24). `duration`/`seconds` snapped per engine.

### 4. Settings — prompt, length, resolution (fps info-only)

**These are the mandatory user-facing levers, available in BOTH the post-swap flow and `/animate`:**

1. **PROMPT (mandatory, customizable).** A sane **default motion prompt** is used when none set;
   the user can override with `/swapbatch_set_prompt <text>` (and the prompt step in `/animate`).
   Empty value resets to default. **Shared across the batch** now; per-photo individual prompts
   are deferred. Prompt does not affect cost. Stored on the session as `motion_prompt`.
2. **RESOLUTION (per-engine choice).** `/swapbatch_set_quality resolution=…` — only the selected
   engine's `allowed_resolutions` are accepted/shown (WaveSpeed 720/1080; Seedance 480/720/1080).
3. **LENGTH (per-engine choice).** `/swapbatch_set_quality duration=…` — only the engine's
   `allowed_durations` (WaveSpeed 5/10/15; Seedance 5/10).
4. **fps — info-only.** Never settable. The settings/quality message states the native value:
   *"fps нативный: WaveSpeed 30 / Seedance 24 — не настраивается на managed-движках."*

`/swapbatch_set_quality` validates against the **currently selected engine's** descriptor and
rejects unsupported values with a clear message listing what that engine allows.

### 5. Engine choice (mandatory)

- **Post-swap menu** (state SWAP_DONE) — 3 options:
  - 🎬 **WaveSpeed (без цензуры)** → `video_engine="spicy"` *(default)*
  - 🎬 **Seedance (дёшево, 10с/1080p)** → `video_engine="seedance"`
  - 📷 **без анимации** → `/swapbatch_no`
- **`/animate`** offers the same engine pick before quality.
- `BatchSession` gains `video_engine: str = "spicy"`. Selecting an engine stores it, then shows
  that engine's cost+time estimate, then requires explicit `/swapbatch_animate_go`.
- PHASE A ships only **WaveSpeed + без анимации** (Seedance option appears in PHASE B).

### 6. Cost model + MANDATORY confirmation (money safety)

- The cost gate reads the **selected engine's** `cost_for` + `gen_seconds` (no engine-specific
  imports in the handler). Estimate shown before any paid call:
  `N видео × {seconds}с × {resolution} ({engine}) = ~${total}` + wall-clock estimate; requires
  an EXPLICIT `/swapbatch_animate_go` (or `/animate_go`) to proceed.
- **Hard ceilings surfaced:** WaveSpeed 100×15s×1080p = $225; Seedance 100×10s×1080p ≈ $55.
  Gate is non-negotiable; flag OFF by default.
- **Money invariant (carried from swap, DO NOT break):** a per-video TERMINAL/billable failure
  (completed-but-failed prediction, 4xx) is NEVER retried; only TRANSIENT
  (`*TransientError`: 429/network, no prediction created) is retried/swept. Bill only successful
  videos at the real per-engine rate.

### 7. Concurrency, partial failure, progress (reuse proven swap patterns)

- Generic, **engine-agnostic** `animate_batch(engine, requests, *, concurrency, progress_cb,
  cancel_check)` runner: semaphore cap `SWAPBATCH_ANIMATE_CONCURRENCY` (env, **default 2**) +
  patient 429 backoff (in-engine) + final **transient-only** retry sweep + per-video isolation.
- **Shared transient base class** `TransientVideoError`; `WaveSpeedTransientError` (PHASE A) and
  `ReplicateSeedanceTransientError` (PHASE B) subclass it; the runner catches the **base** →
  PHASE B drops in with zero runner change. (This base-class tweak lands in PHASE A.)
- Per-video fault isolation: one failure doesn't abort the batch; failed targets get
  `animate_result_path=None`. Honest tally: "🎬 N/100 видео готово, M не удалось".
- Progress batched (every ~10): "🎬 Animate N/100…".

### 8. Flag (isolation)

`SWAPBATCH_ANIMATE_ENABLED` gate, **default OFF**. OFF → animate commands inert. ON → animate
routes to the **selected managed engine** (spicy/seedance, never RunPod) through the cost gate.

### 9. Standalone `/animate` (PHASE B) — one + mini-batch (≤20)

`/animate` → upload 1..N photos (cap **20**) → choose engine → choose quality (constrained to
engine caps) → set/confirm prompt → cost gate → **same `animate_batch` runner** → deliver.
A lightweight `AnimateSession` (own small state, independent of swap) feeds the shared runner /
cost-gate / delivery. No dependency on a prior swap.

### 10. Delivery

Videos delivered one-by-one via existing `_send_local_video` (Telegram `sendVideo`, 50MB/clip;
a 10s 1080p Seedance clip ≈ ~9–18MB, a 15s 720p WaveSpeed clip ≈ ~13MB — safe). Throttle
between sends to avoid Telegram rate limits.

## Error handling

- Missing key (`WAVESPEED_API_KEY` / `REPLICATE_API_TOKEN`) → clear error, animate refused.
- Submit 4xx (non-429) → terminal, per-video failure, not retried.
- 429 / network / tarpit-timeout exhausted → transient, eligible for the sweep.
- Poll timeout → per-video failure (terminal for that video).
- Telegram send failure → logged; saved local mp4 not lost.

## Testing (all offline on mocks until the gated live checkpoints)

- Engines (`httpx.MockTransport`): submit/poll/download happy path; `output=None`/failed →
  terminal (not retried); 429 → `*TransientError` then sweep recovers; duration/resolution snap
  to allowed set; cost-table values; base64 data-URI built; Seedance browser-UA header present.
- Runner: alignment/isolation; transient swept; **terminal submit-count == 1** (money invariant).
- Capability descriptor: UI offers only allowed durations/resolutions per engine.
- Cost estimate: per-engine `cost_for` math + confirmation text.
- **Don't break tests silently:** if an existing swap/handler test asserts old behavior, STOP and
  report the exact assertion before changing it.

## Safeties (explicit, non-negotiable)

1. **Cost-gate железно** — every paid run shows per-engine estimate + ceiling
   (WaveSpeed $225 / Seedance $55 worst case) and requires explicit `_go`. Flag OFF by default.
2. **429** — concurrency cap 2 + patient backoff + transient-only sweep.
3. **Small-first** — live-validate 2–3 videos before any 100-scale run.
4. **Live test = STOP** — hand off to the user at each first-spend checkpoint; never auto-scale.
5. **Money invariant** — terminal failures never re-submitted (submit-count test enforces).
6. **Never wake RunPod** — router spicy/seedance paths never construct `RunpodComfyEngine`.

## Implementation order

- **PHASE A (core, skin case — WaveSpeed first):** `WaveSpeedSpicyEngine` + engine-agnostic
  `animate_batch` (with shared `TransientVideoError` base) + cost gate + WaveSpeed quality
  (5/10/15 × 720/1080) + prompt setting + animate-swapped-batch + "без анимации" + flag.
  *(This is the superseded WaveSpeed spec's plan, plus the base-class tweak and the
  capability-descriptor + prompt-setting groundwork.)*
- **PHASE B (Seedance + choice + /animate):** `ReplicateSeedanceEngine` (mode `seedance`) +
  3-way post-swap engine menu + per-engine quality constraints + standalone `/animate` (≤20).
  Live-confirm Seedance cost-table values from real billing.

## Risks

1. **Cost blow-up** — mitigated by the mandatory per-engine gate + ceilings + flag OFF + small-
   first rollout. Highest-priority guard.
2. **Seedance price estimates approximate** — table marked approx; refined from first real
   billing; the gate always shows the estimate so the user decides before spend.
3. **Rate limits** — concurrency cap + backoff + sweep; tune `SWAPBATCH_ANIMATE_CONCURRENCY`.
4. **WaveSpeed 15s multi-shot drift** — accepted/user-chosen; 5s is continuous.
5. **Replicate tarpit** — browser UA + base64 data-URI mandatory in the Seedance engine.
6. **Key security** — engines read keys from `.env`, never log them. (The exposed WaveSpeed test
   key must be rotated.)
7. **Money-invariant regression** — terminal failures must never re-submit; enforced by the
   transient/terminal split + a submit-count test.
