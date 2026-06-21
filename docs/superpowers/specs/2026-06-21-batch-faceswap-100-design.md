# Batch Face-Swap (up to 100) on a Pluggable Swap Engine — Design

**Date:** 2026-06-21
**Status:** Approved (design); pending spec review → implementation plan
**Scope:** STEP 1 — one source face → swapped into up to 100 distinct target photos,
via the live lucataco/Replicate stopgap. Video/animation is STEP 2 and is OUT of scope.

## Goal

Let a user send one source face plus up to 100 target photos and receive 100
face-swapped results, on the uncensored lucataco path — **without rebuilding the
batch plumbing when we later swap lucataco for our own Path-B ComfyUI model.**

The driving constraint: **the swap engine must be replaceable behind one interface.**
Today = lucataco (Replicate, data-URI). Future = our ComfyUI graph (Path B:
ReActor + GPEN-1024 + occlusion). Switching engines must touch **one config value
and add one class** — never the orchestrator, queue, cost, intake, or delivery.

## Key recon findings (what already exists)

The batch subsystem `M.2.5 /swapbatch_*` is already built and tested:

- `app/services/block_m2_face_swap/batch_orchestrator.py` — state machine
  (`IDLE → EXPECTING_SOURCE → … → SWAP_DONE → … → DONE`), disk persistence at
  `state/face_swap/batches/{chat_id}/session.json`, thread-safe, **per-target
  fault isolation** (`TargetItem.error`, `swap_result_path=None`).
- `app/handlers/face_swap_handler.py` — transport-agnostic handler.
- `app/services/block_m2_face_swap/cost_estimator.py` — cost estimate + billing.
- `tests/test_swapbatch_*.py` — state machine / cost / engine coverage.
- **B-51 already fixed on this path:** albums aggregated by `media_group_id`,
  deduped by `file_unique_id`, filenames `swapbatch_{ts}_{fid[:8]}.jpg`
  (timestamped → collision-free), pop-before-process.

**The abstraction seam already exists.** `orchestrator.confirm_swap(chat_id,
swap_fn, progress_cb)` and `handler.run_swap_phase(chat_id, swap_fn, …)` take
`swap_fn` as a **callback**. The only hard-coding to the (frozen) RunPod engine is
in the bot wiring `tools/jarvis_smart_telegram_control.py:_swapbatch_run_phase`
(`engine = FaceSwapEngine()`). We formalize that seam into an interface + factory.

## Verification done before this spec (risk #2 — data-URI)

Ran two live lucataco predictions with **local files encoded as
`data:image/...;base64,...`** (NOT hosted URLs). Result: **data-URI ACCEPTED.**

- Clean portrait pair → `status=succeeded`, real `output` URL returned
  (`https://replicate.delivery/.../1782059586.jpg`). ~1.8 MB data-URIs accepted.
- → **No image hosting needed. Engine design stands.**

Two behaviors discovered, now baked into the design:

1. **lucataco returns `status=succeeded` with `output=None` when no face is found**
   (logs: "No face found"), instead of `failed`. The engine MUST treat
   `output is None` as a per-target failure, or "no-face" targets become silent
   holes and the "95/100" report lies.
2. **Large photos → large data-URIs** (a 1.8 MB image ≈ 2.5 MB base64). One POST
   per swap, so size is per-request and fine — but the engine will **down-scale
   targets** (e.g. longest side ≤ ~1600px) before encoding to cut bandwidth.

## Architecture

### 1. Pluggable swap engine (the core)

New `app/services/block_m2_face_swap/engines/base.py`:

```python
class SwapEngine(Protocol):
    name: str
    cost_per_swap_usd: float          # engine declares its OWN price
    async def swap_batch(
        self, source: Path, targets: list[Path], *,
        progress_cb, cancel_check,
    ) -> list[Path | None]: ...        # len == len(targets); None = that target failed
```

Implementations:

- **`LucatacoSwapEngine`** (new, today) — async; local files → down-scaled →
  data-URI; `asyncio.Semaphore(5)`; 429 → exponential backoff+jitter (reuse the
  pattern in `faceswap_client.py`); `PredictionFailed` AND `output is None` →
  mark target failed, continue. `cost_per_swap_usd ≈ 0.005`.
- **`RunpodComfySwapEngine`** (thin wrapper over the existing, frozen
  `FaceSwapEngine.swap_batch`) — kept, NOT activated.
- **`ComfyGraphSwapEngine`** (Path B, future) — third impl, added with zero
  changes to orchestrator/handler/cost/intake/delivery.

Selection via factory `get_swap_engine()` reading `SWAP_ENGINE` env
(default `lucataco`). Bot wiring calls the factory instead of constructing
`FaceSwapEngine()` directly. **This is the only edit to the bot wiring.**

Migration to Path B = set `SWAP_ENGINE=comfy` + add one class. Nothing else moves.

### 2. lucataco engine internals

New `app/services/block_m_common/lucataco_client.py` (async, httpx, mirrors the
existing `faceswap_client.py` patterns: browser UA, submit, poll, 429 backoff):

- Each target: down-scale → `data:image/jpeg;base64,…` for `target_image`;
  source encoded once, reused for all targets as `swap_image`.
- `asyncio.gather` over targets, gated by `asyncio.Semaphore(5)` (concurrency
  conservative; tune from logs against the unknown Replicate account limit).
- Per-target outcome handling:
  - `succeeded` + output URL → download & save.
  - `succeeded` + `output is None` (no-face) → **billable** (`predict_time>0`,
    Replicate bills succeeded compute) → record failure, **do NOT retry**.
  - `PredictionFailed` (failed/canceled) → billable → record failure, **do NOT retry**.
  - **Retry rule:** retry ONLY on 429 / network errors at submit/poll transport
    (no prediction created yet → not billable). NEVER retry any *completed*
    outcome (succeeded-no-face or failed) — both are billed and deterministic.

### 3. Photo intake — multi-album accumulation

Telegram caps an album at ~10 photos, so 100 arrives as ≥10 update-albums.
Current `consume_targets_album` handles one album. Change to **accumulate**:
while the session is `EXPECTING_TARGETS`, each flushed album **appends** to
`session.targets` (existing `file_unique_id` dedup applies) until `/swapbatch_go`.
Raise `MAX_TARGETS` 20 → 100. After each album, bot replies "accepted N, total M/100".

### 4. Concurrency & partial failure

- Parallel, semaphore=5 (~100 s vs ~8 min sequential); tune later.
- Per-target fault isolation is already in the orchestrator. Engine maps both
  failure modes (`output=None`, `PredictionFailed`) to `None`.
- Final summary text added to the `run_swap_phase` reply:
  "✅ 95/100 done · 5 had no face: #12, #34, …".

### 5. Cost & UX

- **Cost from the engine:** cost_estimator reads `engine.cost_per_swap_usd`
  instead of a hard-coded env rate → honest ~$0.50 for 100 on lucataco.
- **Confirmation before run:** estimate shown; user runs `/swapbatch_go`.
- **Progress:** existing callbacks, batched every ~10 ("🔄 30/100…") to respect
  Telegram rate limits.
- **Delivery: albums + zip.** Results chunked into `sendMediaGroup` of 10 (×10)
  for in-chat preview, then zip(s) via `sendDocument` for download.
  **Zip size:** Telegram bots cap `sendDocument` at ~50 MB. After down-scale
  (≤1600px) lucataco JPG outputs are ~300–600 KB each → 100 ≈ ~45 MB, dangerously
  close to the cap. So **split the zip by size** (cap ~45 MB/part → usually 1
  part, occasionally 2: `results_1of2.zip`, `results_2of2.zip`). Never assume one
  zip fits. Throttle between sends to avoid Telegram 429.

## Rollout (small-batch first)

**Test the batch on 3–5 photos through lucataco end-to-end first, then scale the
infrastructure to 100.** A working small batch (correct intake, engine, cost,
delivery, summary) precedes any 100-photo concurrency/bandwidth tuning.

## Out of scope

- Video/animation phase (STEP 2; RunPod engine stays frozen and untouched).
- Activating any RunPod path. Factory defaults to lucataco.
- Path B engine implementation (interface is designed to fit it; impl is later).

## Scope estimate (~1 working day)

| Block | What | Size |
|---|---|---|
| Interface + factory | `SwapEngine` protocol, `get_swap_engine()` | S |
| lucataco engine | new async client + `LucatacoSwapEngine` (data-URI, downscale, semaphore, null-output handling) | M |
| Bot wiring | factory instead of hard-coded `FaceSwapEngine()` | S |
| Intake | multi-album accumulation, cap → 100 | M |
| Cost | rate sourced from engine | S |
| Delivery | zip + album chunking | S–M |
| Summary | "95/100" reply text | S |
| Tests | engine (mocked Replicate), accumulation, delivery, null-output failure | M |

## Risks

1. **Unknown Replicate account concurrency limit** → semaphore=5 conservative +
   429 backoff; tune from logs. (Low risk with backoff.)
2. ~~data-URI acceptance~~ — **RESOLVED** by live test above. No hosting needed.
3. **Shared video-lock** — swap phase holds `_get_video_lock`, blocking the
   user's other generations for ~100 s. Acceptable; documented.
4. **Telegram rate-limit** on 10 albums + zip in quick succession → throttle delivery.
   **Zip 50 MB cap:** 100 down-scaled results ≈ ~45 MB, near the limit → split zip
   by size (~45 MB/part). Never assume one zip fits.
5. **Don't activate the frozen RunPod path** — factory defaults to lucataco;
   RunPod engine remains a separate, untouched implementation.
6. **lucataco null-output-on-no-face** — engine treats `output is None` as failure
   (see verification). Without this, the "95/100" report is wrong.
