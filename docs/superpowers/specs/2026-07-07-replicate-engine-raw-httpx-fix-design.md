# ReplicateEngine → raw httpx (kill `import replicate` on the animation path)

**Date:** 2026-07-07
**Status:** approved, autonomous execution to pre-merge STOP
**Branch/worktree:** `replicate-httpx-fix` (from `phase-4.0-unified-jarvis` @ 84f1823)

## Problem (root cause, confirmed by reading the code)

Under Python 3.14 the face **swap** path works (it already uses raw
`httpx` against `api.replicate.com`), but **animation** crashes at
`import replicate` — the Replicate SDK pulls pydantic-v1, which raises
`ConfigError` on 3.14.

The crash is not confined to the wan engine. The import chain is:

```
router.py:8   from .replicate_engine import ReplicateEngine   # top-level
replicate_engine.py:14   import replicate                     # top-level  ← explodes
```

Because `EngineRouter` imports `ReplicateEngine` at module top level, and
`ReplicateEngine` imports the SDK at module top level, **any** import of
the router explodes — so the WaveSpeed **and** Seedance animation paths
die too, even though neither uses the SDK. They just pass through the
router. Killing the one top-level `import replicate` fixes every
animation path at once.

## The template already exists in-tree

`app/services/block_m2_video/engines/replicate_seedance_engine.py`
(`ReplicateSeedanceEngine`) is the exact pattern to copy: same directory,
same `VideoGenerator` protocol (`generate`/`is_available` → `VideoResult`),
raw `httpx` submit→poll→download, injectable `httpx.MockTransport` for
tests, browser User-Agent (anti-tarpit, cf. [[jarvis-replicate-urllib-ua-ban]]),
and a transient/terminal error split for money-safety. The proven
`ReplicateVideoClient._run_prediction` confirms wan models submit to the
official-model endpoint `/v1/models/{owner}/{name}/predictions` (same as
Seedance) — not the version-based `/v1/predictions` used for community
models.

## Design — rewrite `replicate_engine.py` to raw httpx (Seedance-parity)

Drop `import replicate` and `replicate.run()`. Mirror
`ReplicateSeedanceEngine` method-for-method.

### Constructor
```python
def __init__(self, api_token=None, model_id=None, *,
             transport=None, download_transport=None,
             max_retries=8, backoff_base=1.0):
```
- Token: `api_token or REPLICATE_API_TOKEN or REPLICATE_API_KEY` (keep both
  env names — existing behavior). Raise `ReplicateEngineError` if unset.
- **Remove** the `os.environ["REPLICATE_API_TOKEN"] = ...` line — there is
  no SDK left to configure.
- Headers: `Authorization: Token <t>`, `Content-Type: application/json`,
  browser `User-Agent` (anti-tarpit).
- `model_id` default = `REPLICATE_MODELS[0]["id"]`; `cost_per_sec` looked
  up from `REPLICATE_MODELS` (fallback 0.04).
- Store `transport` / `download_transport` (test injection) + retry knobs.

### Endpoint
Per-instance (model_id varies):
`_submit_url = f"https://api.replicate.com/v1/models/{self.model_id}/predictions"`

### `generate(request)` — submit → poll → download
- Timer, `generation_id`, `seed = request.seed if not None else int(time.time())`.
- Guard: input image exists → else `ReplicateEngineError`.
- Payload: `{"input": {"image": <data-URI base64>, "prompt": ..., "duration": seconds, "seed": seed}}`.
- `pred_id = await _submit_with_retry(payload)` — 429 → backoff+retry;
  4xx (≠429) → terminal (billable, never retry); network → retry.
- `video_url = await _poll(pred_id)` — `succeeded` → normalize output
  (str, or first of list); `failed`/`canceled` → terminal.
- `out_path = await _download(...)` → `state/personas/videos/<persona>/<gen>/output.mp4`.
- Return `VideoResult` (unchanged fields): `engine="replicate"`,
  `model=self.model_id`, `seed`, `cost_usd=cost_per_sec*seconds`,
  `duration_sec`, `extra={"video_url": ...}`.

### Error taxonomy (money-safety parity with Seedance)
- `ReplicateEngineError(TerminalVideoError)` — failed/canceled prediction,
  4xx≠429, missing input, missing token, bad output shape. **Billable /
  deterministic → never retried.** (Still an `Exception` subclass, so the
  existing tests that only assert `pytest.raises(ReplicateEngineError)`
  keep passing.)
- `ReplicateEngineTransientError(TransientVideoError)` — 429/network
  exhausted, no prediction succeeded → safe to retry upstream.

### Output normalization
Raw JSON output is a URL `str` or a `list[str]`. Keep a small normalizer
for both; **drop** the SDK-only `FileOutput.url` branch.

## Default model: wan-2.5 → wan-2.2

`REPLICATE_MODELS[0]` is currently `wan-video/wan-2.5-i2v-fast`
($0.020/s). Recon memory [[jarvis-replicate-wan-i2v-recon]] says 2.5 fails
`E002` on benign input while `wan-video/wan-2.2-i2v-fast` ($0.060/s) works.

**Fact-check (per instruction):** I searched the repo — there is **no
captured `E002` log or code artifact**; the claim rests solely on recon
memory, not on evidence I can point to. So 2.5 may in fact be working.
Per the decision, the default flips to **wan-2.2** regardless: it is the
safer choice by the data we have, and it is fully reversible (2.5 stays in
`REPLICATE_MODELS`, selectable via `model_id=...`). Reorder the list so
2.2 is `[0]`; both entries and their per-second costs are kept. Cost per
5s run rises $0.10 → $0.30 — the upstream money-gate quotes from
`cost_per_sec`, so quotes stay accurate.

**Out of scope (noted, not touched):** `video_client_extras.py` lists
wan-2.2 at `$0.10/5s` ($0.02/s), disagreeing with this engine's
`$0.30/5s`. Recon backs $0.30–0.45/5s, so this engine's rate is kept;
the extras discrepancy is a separate cleanup.

## Router
No code change needed in `router.py` beyond what falls out of the engine
rewrite: once `replicate_engine.py` stops importing the SDK at top level,
`from .replicate_engine import ReplicateEngine` no longer drags it in.
`replicate_engine.py` is the **only** module in `block_m2_video` that
imports the SDK (verified by grep), so the whole router import path goes
clean.

## Testing — TDD, teeth with mutations both directions

Rewrite `tests/test_replicate_engine.py` to the `httpx.MockTransport`
pattern (the current tests patch `replicate.run`, which will no longer
exist). New tests:

**A. Import teeth (the missing check that let the pydantic downgrade slip through)**
1. **Static/no-SDK tooth** — AST-parse `replicate_engine.py`; assert no
   `import replicate` and no `from replicate import` (match the exact
   top-level module `replicate`, not the substring in
   `replicate_engine`/`replicate_seedance_engine`). Mutation: re-adding
   `import replicate` → fails.
2. **Router-import-without-SDK tooth (reproduces the prod crash)** — block
   `replicate` via a `sys.meta_path` finder that raises `ImportError`,
   evict cached `replicate*`/`router`/engine modules from `sys.modules`,
   re-import `...engines.router`, assert it succeeds and `ReplicateEngine`
   instantiates. Mutation: re-adding the SDK import → `ImportError` →
   fails.
3. **Live smoke under 3.14, no mocks** — plain `import` of the router +
   `replicate_engine` modules under the running interpreter (3.14) and
   construct `ReplicateEngine()` (token via env); assert no exception.
   This is the always-on real-interpreter check the pydantic downgrade
   lacked.

**B. Behavior parity (MockTransport, mirrors Seedance tests)**
- success: POST→id, GET→`succeeded`+output url, download → `output.mp4`
  exists; `cost_usd == cost_per_sec*seconds`; `engine=="replicate"`;
  `model` default == wan-2.2; `extra["video_url"]` set.
- payload: image is a `data:` URI, `prompt`/`duration`/`seed` passed
  through; `result.seed` preserved.
- output normalization: bare-string output and list output both accepted.
- 429 exhausted → `ReplicateEngineTransientError`.
- 4xx (422) terminal, POST issued exactly once (money) → `ReplicateEngineError`.
- `failed` status → `ReplicateEngineError`.
- missing input image → `ReplicateEngineError`.
- missing token → `ReplicateEngineError`.
- `is_available()` true when token set.
- output path under `state/personas/videos/<persona>/<gen>/output.mp4`.

**C. Default-model tooth (mutation both directions)**
- `REPLICATE_MODELS[0]["id"] == "wan-video/wan-2.2-i2v-fast"` and a fresh
  `ReplicateEngine().model_id` == wan-2.2 (mutation: reorder back → fails).
- `ReplicateEngine(model_id="wan-video/wan-2.5-i2v-fast").cost_per_sec ==
  0.020` (2.5 stays selectable; guards the reorder didn't drop it).

## Regression & stop
- Run the full `tests/` suite; compare NEW failures against the known
  flak-band baseline (0 new expected in touched area).
- **STOP before merge.** Send a Telegram notification at the STOP.
  Merge only on the user's explicit OK (FF into `phase-4.0-unified-jarvis`).

## Non-goals
- No change to `router.py` logic, `video_client_extras.py`,
  `ReplicateVideoClient`, WaveSpeed, or Seedance.
- No live paid Replicate run in this arc (separate step after merge OK).
- No removal of wan-2.5 (kept, reversible).
