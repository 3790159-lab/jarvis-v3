# Batch Video Animate on WaveSpeed (uncensored) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a `/swapbatch` swap, animate all swapped photos → short videos via WaveSpeed `wan-2.6 spicy` (uncensored), through the existing pluggable `VideoGenerator` seam, with a mandatory cost-confirmation gate and money-safe (transient-only) retries.

**Architecture:** New `WaveSpeedSpicyEngine` (a `VideoGenerator`) registered in `EngineRouter` under `mode="spicy"`. A generic, engine-agnostic `animate_batch` runner adds concurrency (semaphore=2) + patient 429 backoff + transient-only retry sweep + per-video isolation — mirroring the proven swap engine. A new orchestrator `confirm_animate_batch` transitions SWAP_DONE→ANIMATING→DONE in parallel. The batch animate phase is repointed off the frozen RunPod engine onto WaveSpeed; `ReplicateEngine` is kept untouched as the future alternative (FLOW 2); RunPod stays frozen.

**Tech Stack:** Python 3, `httpx` (async WaveSpeed REST), `cv2`/Pillow not needed here, `pytest`+`pytest-asyncio`. WaveSpeed `alibaba/wan-2.6/image-to-video-spicy`, Bearer auth, submit→poll→mp4.

**Spec:** `docs/superpowers/specs/2026-06-22-batch-video-animate-wavespeed-design.md`

**Verified live (2026-06-22):** WaveSpeed wan-2.6-spicy returns a real 15s/30fps mp4, uncensored, from a base64 data-URI input; submit→poll on `data.urls.get`; output at `data.outputs[0]`. 720p 15s = $1.50.

**Money invariant (carried from swap, DO NOT break):** a per-video TERMINAL/billable failure (completed-but-failed prediction, 4xx) is NEVER retried; only TRANSIENT failures (`WaveSpeedTransientError`: 429/network, no prediction created) are retried/swept. Tests assert submit-count == 1 for terminal failures.

---

## File Structure

**New:**
- `app/services/block_m2_video/engines/wavespeed_spicy_engine.py` — `WaveSpeedSpicyEngine` (VideoGenerator) + `WaveSpeedTransientError` + `WaveSpeedEngineError` + pricing table + `cost_for(seconds, resolution)`.
- `app/services/block_m2_video/batch_animate.py` — generic `animate_batch(engine, requests, *, concurrency, progress_cb, cancel_check)` runner (semaphore + transient sweep + isolation). Engine-agnostic → reusable for FLOW 2.
- Tests: `tests/test_wavespeed_spicy_engine.py`, `tests/test_video_batch_animate.py`, `tests/test_swapbatch_animate_cost.py`.

**Modified:**
- `app/services/block_m2_video/engines/engine_protocol.py` — `VideoRequest` gains `resolution`, `negative_prompt`; `GenerationMode` gains `"spicy"`.
- `app/services/block_m2_video/engines/router.py` — `_get_wavespeed` + `select("spicy")`.
- `app/services/block_m2_face_swap/batch_orchestrator.py` — `BatchSession.resolution`; `confirm_animate_batch`.
- `app/services/block_m2_face_swap/quality_settings.py` — accept `duration∈{5,10,15}` + `resolution∈{720p,1080p}` for the spicy path.
- `app/handlers/face_swap_handler.py` — animate cost estimate; `handle_animate_yes` shows cost (no run); `run_animate_batch_phase`; `_format_quality` shows resolution.
- `tools/jarvis_smart_telegram_control.py` — repoint animate to WaveSpeed via router `spicy`; `/swapbatch_animate_go` paid trigger; cost gate; progress every 10; flag routes to WaveSpeed; `/swapbatch_set_quality` resolution.

---

# PHASE 0 — Engine + runner + wiring (ALL on mocks, NO network/spend)

---

### Task 1: VideoRequest fields + spicy mode

**Files:**
- Modify: `app/services/block_m2_video/engines/engine_protocol.py`
- Test: `tests/test_wavespeed_spicy_engine.py` (create, first test only)

- [ ] **Step 1: Write the failing test**

Create `tests/test_wavespeed_spicy_engine.py`:

```python
from pathlib import Path

from app.services.block_m2_video.engines.engine_protocol import VideoRequest


def test_video_request_has_resolution_and_negative_prompt_defaults():
    r = VideoRequest(
        persona_id="p", persona_name="n",
        input_image_path=Path("x.jpg"), prompt="move",
    )
    assert r.resolution == "720p"
    assert r.negative_prompt == ""
    assert r.seconds == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_wavespeed_spicy_engine.py::test_video_request_has_resolution_and_negative_prompt_defaults -v`
Expected: FAIL (`unexpected keyword`/`no attribute resolution`).

- [ ] **Step 3: Add the fields + mode**

In `engine_protocol.py`, change the `GenerationMode` line (line 11):

```python
GenerationMode = Literal["fast", "hq", "auto", "spicy"]
```

In the `VideoRequest` dataclass, after the `generation_id` field (line 29), add:

```python
    # WaveSpeed spicy path: output resolution tier and negative prompt.
    # Ignored by Replicate/RunPod engines (kept default).
    resolution: str = "720p"
    negative_prompt: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_wavespeed_spicy_engine.py -v`
Expected: PASS.

- [ ] **Step 5: Verify nothing else broke**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_replicate_engine.py tests/test_persona_video_handler.py -q`
Expected: PASS (new fields are optional; existing engines unaffected). If anything fails, STOP and report.

- [ ] **Step 6: Commit**

```bash
git add app/services/block_m2_video/engines/engine_protocol.py tests/test_wavespeed_spicy_engine.py
git -c commit.gpgsign=false commit -m "feat(video): VideoRequest gains resolution/negative_prompt + spicy mode"
```

---

### Task 2: WaveSpeedSpicyEngine

**Files:**
- Create: `app/services/block_m2_video/engines/wavespeed_spicy_engine.py`
- Test: `tests/test_wavespeed_spicy_engine.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wavespeed_spicy_engine.py`:

```python
import httpx
import pytest

from app.services.block_m2_video.engines.wavespeed_spicy_engine import (
    WaveSpeedSpicyEngine,
    WaveSpeedTransientError,
    cost_for,
)


def _img(tmp_path) -> Path:
    p = tmp_path / "src.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0FAKEJPEG")
    return p


def _engine(handler, dl_handler=None, **kw) -> WaveSpeedSpicyEngine:
    return WaveSpeedSpicyEngine(
        api_key="t",
        transport=httpx.MockTransport(handler),
        download_transport=httpx.MockTransport(dl_handler or (lambda req: httpx.Response(200, content=b"MP4DATA"))),
        **kw,
    )


def test_cost_for_table():
    assert cost_for(5, "720p") == pytest.approx(0.50)
    assert cost_for(15, "720p") == pytest.approx(1.50)
    assert cost_for(15, "1080p") == pytest.approx(2.25)


@pytest.mark.asyncio
async def test_generate_success(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(200, json={"code": 200, "data": {"id": "v1", "status": "created", "urls": {"get": "https://api.wavespeed.ai/api/v3/predictions/v1/result"}}})
        return httpx.Response(200, json={"data": {"status": "completed", "outputs": ["https://cdn/x.mp4"]}})

    from app.services.block_m2_video.engines.engine_protocol import VideoRequest
    eng = _engine(handler, concurrency=2)
    res = await eng.generate(VideoRequest(
        persona_id="swapbatch_1", persona_name="b",
        input_image_path=_img(tmp_path), prompt="move",
        seconds=15, resolution="720p",
    ))
    assert res.output_path.exists()
    assert res.cost_usd == pytest.approx(1.50)
    assert res.engine == "wavespeed_spicy"


@pytest.mark.asyncio
async def test_generate_429_exhausted_raises_transient(tmp_path):
    posts = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            posts["n"] += 1
            return httpx.Response(429, json={"message": "rate limited"})
        return httpx.Response(200, json={"data": {"status": "completed", "outputs": ["x"]}})

    from app.services.block_m2_video.engines.engine_protocol import VideoRequest
    eng = _engine(handler, max_retries=2)
    with pytest.raises(WaveSpeedTransientError):
        await eng.generate(VideoRequest(
            persona_id="p", persona_name="b",
            input_image_path=_img(tmp_path), prompt="m", seconds=10,
        ))


@pytest.mark.asyncio
async def test_generate_failed_status_is_terminal(tmp_path):
    from app.services.block_m2_video.engines.wavespeed_spicy_engine import WaveSpeedEngineError
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(200, json={"data": {"id": "v", "status": "created", "urls": {"get": "https://api.wavespeed.ai/api/v3/predictions/v/result"}}})
        return httpx.Response(200, json={"data": {"status": "failed", "error": "boom"}})

    from app.services.block_m2_video.engines.engine_protocol import VideoRequest
    eng = _engine(handler)
    with pytest.raises(WaveSpeedEngineError):
        await eng.generate(VideoRequest(
            persona_id="p", persona_name="b",
            input_image_path=_img(tmp_path), prompt="m", seconds=5,
        ))


def test_duration_snaps_to_allowed():
    from app.services.block_m2_video.engines.wavespeed_spicy_engine import snap_duration
    assert snap_duration(5) == 5
    assert snap_duration(7) == 5
    assert snap_duration(8) == 10
    assert snap_duration(13) == 15
    assert snap_duration(99) == 15
    assert snap_duration(1) == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_wavespeed_spicy_engine.py -q`
Expected: FAIL (module `wavespeed_spicy_engine` not found).

- [ ] **Step 3: Write the engine**

Create `app/services/block_m2_video/engines/wavespeed_spicy_engine.py`:

```python
# -*- coding: utf-8 -*-
"""WaveSpeed wan-2.6 spicy image-to-video engine (uncensored, managed).

A VideoGenerator implementation for the STEP-2 batch animate path. Mirrors the
lucataco swap client's resilience: patient 429 backoff, and a distinct
WaveSpeedTransientError (429/network, no prediction created -> safe to retry)
vs WaveSpeedEngineError (completed-but-failed / 4xx -> terminal, billable, never
retry). Local image is sent as a base64 data-URI (no hosting; verified live).
Never logs the API key.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .engine_protocol import VideoRequest, VideoResult, new_generation_id

logger = logging.getLogger(__name__)

_BASE = "https://api.wavespeed.ai"
_MODEL = "alibaba/wan-2.6/image-to-video-spicy"
_SUBMIT = f"{_BASE}/api/v3/{_MODEL}"

_ALLOWED_DURATIONS = (5, 10, 15)
_ALLOWED_RESOLUTIONS = ("720p", "1080p")

# Verified WaveSpeed pricing (per clip). Single source of truth for cost.
_PRICING = {
    ("720p", 5): 0.50, ("720p", 10): 1.00, ("720p", 15): 1.50,
    ("1080p", 5): 0.75, ("1080p", 10): 1.50, ("1080p", 15): 2.25,
}
# Rough wall-clock per clip (s) for the time estimate shown to the user.
_GEN_SECONDS = {5: 40, 10: 60, 15: 90}


class WaveSpeedEngineError(RuntimeError):
    """Terminal failure: prediction completed-but-failed, or a 4xx. Billable
    if it ran; never retry."""


class WaveSpeedTransientError(RuntimeError):
    """429/network retries exhausted; NO prediction was created -> safe to retry."""


def snap_duration(seconds: int) -> int:
    """Snap an arbitrary seconds value to the nearest allowed {5,10,15}."""
    return min(_ALLOWED_DURATIONS, key=lambda d: (abs(d - seconds), d))


def cost_for(seconds: int, resolution: str) -> float:
    res = resolution if resolution in _ALLOWED_RESOLUTIONS else "720p"
    return _PRICING[(snap_duration(seconds), res)]


def gen_seconds(seconds: int) -> int:
    return _GEN_SECONDS[snap_duration(seconds)]


class WaveSpeedSpicyEngine:
    engine_name = "wavespeed_spicy"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        download_transport: httpx.BaseTransport | None = None,
        concurrency: int = 1,          # single-video engine; batch runner caps fan-out
        max_retries: int = 8,
    ) -> None:
        self._key = (api_key or os.getenv("WAVESPEED_API_KEY", "")).strip()
        if not self._key:
            raise WaveSpeedEngineError("WAVESPEED_API_KEY not set")
        self._headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        self._transport = transport
        self._dl_transport = download_transport
        self._max_retries = max_retries

    async def is_available(self) -> bool:
        return bool(self._key)

    def cost_usd(self, seconds: int, resolution: str) -> float:
        return cost_for(seconds, resolution)

    async def generate(self, request: VideoRequest) -> VideoResult:
        start = time.monotonic()
        gen_id = request.generation_id or new_generation_id()
        seconds = snap_duration(request.seconds)
        resolution = request.resolution if request.resolution in _ALLOWED_RESOLUTIONS else "720p"
        if not request.input_image_path.exists():
            raise WaveSpeedEngineError(f"input image not found: {request.input_image_path}")

        payload = {
            "image": self._data_uri(request.input_image_path),
            "prompt": request.prompt,
            "duration": seconds,
            "resolution": resolution,
            "negative_prompt": request.negative_prompt or "",
            "enable_prompt_expansion": True,
        }
        if request.seed is not None:
            payload["seed"] = request.seed

        poll_url = await self._submit_with_retry(payload)
        video_url = await self._poll(poll_url)
        out_path = await self._download(video_url, request.persona_id, gen_id)

        return VideoResult(
            generation_id=gen_id,
            persona_id=request.persona_id,
            output_path=out_path,
            engine=self.engine_name,
            model=_MODEL,
            seed=request.seed if request.seed is not None else -1,
            cost_usd=cost_for(seconds, resolution),
            duration_sec=time.monotonic() - start,
            timestamp=datetime.now(timezone.utc),
            prompt=request.prompt,
            seconds=seconds,
            extra={"video_url": video_url, "resolution": resolution},
        )

    @staticmethod
    def _data_uri(path: Path) -> str:
        b64 = base64.b64encode(path.read_bytes()).decode()
        return f"data:image/jpeg;base64,{b64}"

    async def _submit_with_retry(self, payload: dict) -> str:
        last: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=120, transport=self._transport) as c:
                    r = await c.post(_SUBMIT, headers=self._headers, json=payload)
                if r.status_code == 429:
                    raise httpx.HTTPStatusError("429", request=r.request, response=r)
                if 400 <= r.status_code < 500:
                    raise WaveSpeedEngineError(f"WaveSpeed rejected ({r.status_code}): {r.text[:300]}")
                r.raise_for_status()
                d = r.json().get("data", r.json())
                pid = d.get("id")
                return (d.get("urls") or {}).get("get") or f"{_BASE}/api/v3/predictions/{pid}/result"
            except WaveSpeedEngineError:
                raise
            except httpx.HTTPStatusError as exc:
                last = exc
                code = exc.response.status_code if exc.response is not None else 0
                logger.warning("WaveSpeed submit attempt %d/%d failed: %s", attempt, self._max_retries, exc)
                if attempt < self._max_retries:
                    wait = 10.0 + (2 ** attempt) + random.uniform(0, 5) if code == 429 else 2 ** attempt
                    await asyncio.sleep(wait)
            except Exception as exc:  # network/transport — retryable, not billed
                last = exc
                logger.warning("WaveSpeed submit attempt %d/%d error: %s", attempt, self._max_retries, exc)
                if attempt < self._max_retries:
                    await asyncio.sleep(2 ** attempt)
        raise WaveSpeedTransientError(f"WaveSpeed submit failed after {self._max_retries} retries: {last}")

    async def _poll(self, poll_url: str, max_wait: int = 600) -> str:
        waited, interval = 0, 6
        async with httpx.AsyncClient(timeout=60, transport=self._transport) as c:
            while waited < max_wait:
                r = await c.get(poll_url, headers=self._headers)
                r.raise_for_status()
                d = r.json().get("data", r.json())
                status = d.get("status")
                if status in ("completed", "succeeded"):
                    outs = d.get("outputs") or d.get("output") or []
                    if isinstance(outs, str):
                        outs = [outs]
                    if not outs:
                        raise WaveSpeedEngineError(f"completed but no outputs: {d}")
                    return outs[0]
                if status in ("failed", "error", "canceled"):
                    raise WaveSpeedEngineError(f"prediction {status}: {d.get('error')}")
                await asyncio.sleep(interval)
                waited += interval
        raise WaveSpeedEngineError(f"poll timed out after {max_wait}s")

    async def _download(self, url: str, persona_id: str, gen_id: str) -> Path:
        out_dir = Path("state/personas/videos") / persona_id / gen_id
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / "output.mp4"
        last: Exception | None = None
        for attempt in range(1, 4):  # re-GET is free, no re-bill
            try:
                async with httpx.AsyncClient(timeout=300, transport=self._dl_transport) as c:
                    r = await c.get(url)
                    r.raise_for_status()
                    dest.write_bytes(r.content)
                return dest
            except Exception as exc:  # noqa: BLE001
                last = exc
                await asyncio.sleep(2 ** attempt)
        raise WaveSpeedEngineError(f"download failed: {last}")
```

Note: the `_poll` line uses `await c.get(...)`; write it plainly as `r = await c.get(poll_url, headers=self._headers)` (the `if False else` is shown only to make the await explicit — use the plain form).

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_wavespeed_spicy_engine.py -v`
Expected: PASS (cost table, success, 429→transient, failed→terminal, snap).

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_video/engines/wavespeed_spicy_engine.py tests/test_wavespeed_spicy_engine.py
git -c commit.gpgsign=false commit -m "feat(video): WaveSpeedSpicyEngine (uncensored, base64, transient/terminal split, pricing)"
```

---

### Task 3: Router spicy branch

**Files:**
- Modify: `app/services/block_m2_video/engines/router.py`
- Test: `tests/test_video_batch_animate.py` (create, router test only)

- [ ] **Step 1: Write the failing test**

Create `tests/test_video_batch_animate.py`:

```python
import pytest

from app.services.block_m2_video.engines.router import EngineRouter


class _FakeEngine:
    engine_name = "fake_spicy"
    async def is_available(self): return True
    async def generate(self, request): ...


@pytest.mark.asyncio
async def test_router_spicy_returns_injected_wavespeed():
    router = EngineRouter(wavespeed=_FakeEngine())
    eng = await router.select("spicy")
    assert eng.engine_name == "fake_spicy"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_video_batch_animate.py::test_router_spicy_returns_injected_wavespeed -v`
Expected: FAIL (`unexpected keyword 'wavespeed'`).

- [ ] **Step 3: Add the spicy branch**

In `router.py`, add `wavespeed` to `__init__` (after the `runpod` param, line 21):

```python
        wavespeed: VideoGenerator | None = None,
```

and in the body (after `self._runpod = runpod`, line 26):

```python
        self._wavespeed = wavespeed
```

Add a getter after `_get_runpod` (line 36):

```python
    def _get_wavespeed(self) -> VideoGenerator:
        if self._wavespeed is None:
            from .wavespeed_spicy_engine import WaveSpeedSpicyEngine
            self._wavespeed = WaveSpeedSpicyEngine()
        return self._wavespeed
```

In `select`, add a branch before the `fast` branch (line 45):

```python
        if mode == "spicy":
            logger.info("EngineRouter: mode=spicy -> WaveSpeedSpicyEngine")
            return self._get_wavespeed()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_video_batch_animate.py::test_router_spicy_returns_injected_wavespeed -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_video/engines/router.py tests/test_video_batch_animate.py
git -c commit.gpgsign=false commit -m "feat(video): router mode=spicy -> WaveSpeed"
```

---

### Task 4: Generic `animate_batch` runner (semaphore + transient sweep + isolation)

**Files:**
- Create: `app/services/block_m2_video/batch_animate.py`
- Test: `tests/test_video_batch_animate.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_video_batch_animate.py`:

```python
from pathlib import Path
from types import SimpleNamespace

from app.services.block_m2_video.batch_animate import animate_batch
from app.services.block_m2_video.engines.wavespeed_spicy_engine import (
    WaveSpeedTransientError, WaveSpeedEngineError,
)


class _ScriptedEngine:
    """generate() behavior driven by a per-call script keyed on request.prompt."""
    engine_name = "scripted"
    def __init__(self, script):
        self.script = script
        self.calls = {}
    async def generate(self, request):
        key = request.prompt
        self.calls[key] = self.calls.get(key, 0) + 1
        behavior = self.script[key].pop(0)
        if behavior == "ok":
            return SimpleNamespace(output_path=Path(f"/tmp/{key}.mp4"))
        if behavior == "transient":
            raise WaveSpeedTransientError("429")
        raise WaveSpeedEngineError("failed")


def _req(prompt):
    return SimpleNamespace(prompt=prompt)


@pytest.mark.asyncio
async def test_animate_batch_isolates_and_aligns():
    eng = _ScriptedEngine({"a": ["ok"], "b": ["terminal"], "c": ["ok"]})
    out = await animate_batch(eng, [_req("a"), _req("b"), _req("c")], concurrency=2)
    assert [str(p) if p else None for p in out] == ["/tmp/a.mp4", None, "/tmp/c.mp4"]


@pytest.mark.asyncio
async def test_animate_batch_sweeps_transient_only():
    # 'a' is transient on first attempt then ok on sweep; 'b' terminal (must NOT retry).
    eng = _ScriptedEngine({"a": ["transient", "ok"], "b": ["terminal"]})
    out = await animate_batch(eng, [_req("a"), _req("b")], concurrency=2)
    assert out[0] == Path("/tmp/a.mp4")   # recovered by sweep
    assert out[1] is None                  # terminal stays failed
    assert eng.calls["a"] == 2             # retried once (transient)
    assert eng.calls["b"] == 1             # MONEY: terminal never retried
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_video_batch_animate.py -q`
Expected: FAIL (module `batch_animate` not found).

- [ ] **Step 3: Write the runner**

Create `app/services/block_m2_video/batch_animate.py`:

```python
# -*- coding: utf-8 -*-
"""Engine-agnostic concurrent runner for N video generations.

Mirrors the swap engine's money-safe pattern: a semaphore caps concurrent
generations (429 defense), each generation is isolated, and only TRANSIENT
failures (WaveSpeedTransientError) are retried in a final sequential sweep.
TERMINAL/billable failures are recorded as None and NEVER retried. Returns a
list aligned to ``requests`` (Path on success, None on failure).
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .engines.wavespeed_spicy_engine import WaveSpeedTransientError

logger = logging.getLogger(__name__)


async def animate_batch(
    engine,
    requests: list,
    *,
    concurrency: int = 2,
    progress_cb=None,
    cancel_check=None,
) -> list[Path | None]:
    total = len(requests)
    results: list[Path | None] = [None] * total
    transient_idxs: list[int] = []
    sem = asyncio.Semaphore(max(1, concurrency))
    done = {"n": 0}
    done_lock = asyncio.Lock()

    async def _attempt(idx: int, req) -> tuple[Path | None, bool]:
        try:
            res = await engine.generate(req)
            return res.output_path, False
        except WaveSpeedTransientError as exc:
            logger.warning("animate idx=%d transient: %s", idx, exc)
            return None, True
        except Exception as exc:  # noqa: BLE001 — terminal, do not retry
            logger.warning("animate idx=%d terminal: %s", idx, exc)
            return None, False

    async def _one(idx: int, req) -> None:
        if cancel_check and cancel_check():
            return
        async with sem:
            if cancel_check and cancel_check():
                return
            path, transient = await _attempt(idx, req)
        results[idx] = path
        if transient:
            transient_idxs.append(idx)
        async with done_lock:
            done["n"] += 1
            completed = done["n"]
        if progress_cb:
            progress_cb("animate_progress", {"completed": completed, "total": total, "index": idx, "ok": path is not None})

    await asyncio.gather(*[_one(i, r) for i, r in enumerate(requests)])

    # Sweep: retry ONLY transient failures, sequentially, with a pause.
    for idx in list(transient_idxs):
        if cancel_check and cancel_check():
            break
        await asyncio.sleep(2)
        path, transient = await _attempt(idx, requests[idx])
        if path is not None:
            results[idx] = path
        if progress_cb:
            progress_cb("animate_retry", {"index": idx, "ok": path is not None})
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_video_batch_animate.py -v`
Expected: PASS (alignment/isolation, transient-swept, terminal `calls==1`).

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_video/batch_animate.py tests/test_video_batch_animate.py
git -c commit.gpgsign=false commit -m "feat(video): engine-agnostic animate_batch runner (semaphore, transient-only sweep)"
```

---

### Task 5: Orchestrator `confirm_animate_batch` (parallel) + session resolution

**Files:**
- Modify: `app/services/block_m2_face_swap/batch_orchestrator.py`
- Test: `tests/test_swapbatch_animate_cost.py` (create; orchestrator test here)

- [ ] **Step 1: Write the failing test**

Create `tests/test_swapbatch_animate_cost.py`:

```python
from pathlib import Path

import pytest
from PIL import Image

from app.services.block_m2_face_swap.batch_orchestrator import (
    BatchOrchestrator, STATE_DONE,
)


class _V:
    def count_faces(self, p): return 1


def _jpg(p: Path) -> Path:
    Image.new("RGB", (16, 16), (1, 2, 3)).save(p, "JPEG"); return p


def _orch(tmp_path):
    return BatchOrchestrator(state_root=tmp_path / "b", validator=_V())


def _seed_swapped(orch, chat, tmp_path):
    orch.begin_source(chat); orch.submit_source(chat, _jpg(tmp_path / "s.jpg"))
    orch.begin_targets(chat)
    orch.add_targets(chat, [_jpg(tmp_path / "t0.jpg"), _jpg(tmp_path / "t1.jpg")])
    # mark both swapped (simulate SWAP_DONE)
    import asyncio
    async def _swap(src, tgts, cc): return [_jpg(tmp_path / "r0.jpg"), _jpg(tmp_path / "r1.jpg")]
    asyncio.run(orch.confirm_swap(chat, swap_fn=_swap))


@pytest.mark.asyncio
async def test_confirm_animate_batch_records_videos(tmp_path):
    orch = _orch(tmp_path); chat = 1
    _seed_swapped(orch, chat, tmp_path)

    async def _animate_fn(photos, cancel_check):
        # one ok, one failed -> aligned list
        return [tmp_path / "v0.mp4", None]

    out = await orch.confirm_animate_batch(chat, animate_fn=_animate_fn)
    sess = orch.get(chat)
    assert sess.status == STATE_DONE
    swapped = [t for t in sess.targets if t.swap_result_path]
    assert swapped[0].animate_result_path == str(tmp_path / "v0.mp4")
    assert swapped[1].animate_result_path is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swapbatch_animate_cost.py -v`
Expected: FAIL (`no attribute confirm_animate_batch`).

- [ ] **Step 3: Add `resolution` to BatchSession + `confirm_animate_batch`**

In `batch_orchestrator.py`, add to `BatchSession` after the `fps` field (line 99):

```python
    resolution: str = "720p"
```

and in `BatchSession.from_dict` (after the `fps=...` line ~129):

```python
            resolution=str(data.get("resolution", "720p")),
```

Add this method to `BatchOrchestrator` after `confirm_animate` (after line 475). It mirrors `confirm_swap`: hands ALL swapped photos to a parallel `animate_fn`, maps results back:

```python
    async def confirm_animate_batch(
        self,
        chat_id: int,
        *,
        animate_fn: Callable[
            [list[Path], Callable[[], bool]], Awaitable[list[Path | None]]
        ],
        progress_cb: ProgressCb | None = None,
    ) -> list[Path | None]:
        """Animate all swapped photos IN PARALLEL via ``animate_fn``.

        ``animate_fn(photos, cancel_check) -> list[Path|None]`` is injected (the
        bot wiring passes the WaveSpeed animate_batch runner). Results align to
        the swapped photos in display order; each maps back to its target's
        ``animate_result_path``. Transitions SWAP_DONE -> ANIMATING -> DONE.
        """
        with self._lock:
            sess = self._require(chat_id, {STATE_SWAP_DONE})
            swapped = [t for t in sess.targets if t.swap_result_path]
            if not swapped:
                raise OrchestratorError("Нет swapped фото для анимации.")
            photos = [Path(t.swap_result_path) for t in swapped]
            sess.status = STATE_ANIMATING
            sess.cancel_requested = False
            sess.last_error = None
            self._touch(sess)
            self._persist(sess)

        def _cancel_check() -> bool:
            with self._lock:
                cur = self._sessions.get(chat_id)
                return bool(cur and cur.cancel_requested)

        try:
            results = await animate_fn(photos, _cancel_check)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                cur = self._sessions.get(chat_id)
                if cur is not None:
                    cur.last_error = f"animate engine: {exc}"
                    cur.status = STATE_SWAP_DONE  # allow retry
                    self._touch(cur)
                    self._persist(cur)
            raise

        with self._lock:
            cur = self._sessions.get(chat_id)
            if cur is None:
                return results
            swapped_targets = [t for t in cur.targets if t.swap_result_path]
            for t, r in zip(swapped_targets, results):
                if r is not None:
                    t.animate_result_path = str(r)
            cur.status = STATE_DONE
            self._touch(cur)
            self._persist(cur)
            self._fire(progress_cb, "animate_phase_done", {
                "succeeded": sum(1 for t in cur.targets if t.animate_result_path),
                "failed": sum(1 for t in cur.targets if t.swap_result_path and not t.animate_result_path),
            })
        return results
```

`Path`, `Callable`, `Awaitable`, `STATE_SWAP_DONE`, `STATE_ANIMATING`, `STATE_DONE`, `ProgressCb` are already imported/defined in this module.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swapbatch_animate_cost.py tests/test_swapbatch_orchestrator.py -q`
Expected: PASS (new + existing orchestrator tests; the new `resolution` field defaults, so persistence round-trip stays green). If an existing orchestrator test fails, STOP and report.

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_face_swap/batch_orchestrator.py tests/test_swapbatch_animate_cost.py
git -c commit.gpgsign=false commit -m "feat(swapbatch): confirm_animate_batch (parallel) + session resolution"
```

---

### Task 6: Handler — animate cost estimate + cost-gate reply + run_animate_batch_phase

**Files:**
- Modify: `app/handlers/face_swap_handler.py`
- Modify: `app/services/block_m2_face_swap/quality_settings.py`
- Test: `tests/test_swapbatch_animate_cost.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_swapbatch_animate_cost.py`:

```python
def test_animate_cost_estimate_math(tmp_path):
    from app.handlers.face_swap_handler import animate_cost_estimate
    # 2 swapped photos, 10s, 720p -> 2 * 1.00 = $2.00
    est = animate_cost_estimate(swapped_count=2, seconds=10, resolution="720p")
    assert est["total_usd"] == pytest.approx(2.00)
    assert est["per_usd"] == pytest.approx(1.00)
    assert est["count"] == 2
    assert est["minutes"] > 0


@pytest.mark.asyncio
async def test_handle_animate_yes_shows_cost_does_not_run(tmp_path):
    from app.handlers.face_swap_handler import FaceSwapHandler
    orch = _orch(tmp_path); chat = 7
    _seed_swapped(orch, chat, tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    reply = h.handle_animate_yes(chat)
    assert reply.text is not None
    assert "$" in reply.text
    assert "/swapbatch_animate_go" in reply.text
    # still in SWAP_DONE (NOT run)
    assert orch.status(chat) == "SWAP_DONE"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swapbatch_animate_cost.py -q`
Expected: FAIL (`animate_cost_estimate`/`handle_animate_yes` missing).

- [ ] **Step 3: Add the cost estimate + handler methods**

In `face_swap_handler.py`, add a module-level function (after the imports / before `class FaceSwapHandler`):

```python
def animate_cost_estimate(*, swapped_count: int, seconds: int, resolution: str) -> dict:
    """Cost + rough wall-clock for animating ``swapped_count`` videos on WaveSpeed."""
    from app.services.block_m2_video.engines.wavespeed_spicy_engine import (
        cost_for, gen_seconds,
    )
    import math
    per = cost_for(seconds, resolution)
    concurrency = max(1, int(os.getenv("SWAPBATCH_ANIMATE_CONCURRENCY", "2")))
    minutes = math.ceil(swapped_count / concurrency) * gen_seconds(seconds) / 60.0
    return {
        "count": swapped_count, "per_usd": per,
        "total_usd": round(per * swapped_count, 2),
        "minutes": round(minutes, 1),
        "seconds": seconds, "resolution": resolution,
    }
```

Add `handle_animate_yes` to `FaceSwapHandler` (replace the routing that used to run immediately — this now only SHOWS cost). Place it near the other animate handlers:

```python
    def handle_animate_yes(self, chat_id: int) -> HandlerReply:
        """Show the animate cost+time for current settings and require an
        explicit /swapbatch_animate_go to actually spend money."""
        sess = self.orchestrator.get(chat_id)
        if sess is None or sess.status != STATE_SWAP_DONE:
            return HandlerReply(text="⚠️ Сначала заверши swap (/swapbatch_go).")
        swapped = sum(1 for t in sess.targets if t.swap_result_path)
        if swapped == 0:
            return HandlerReply(text="⚠️ Нет swapped фото для анимации.")
        est = animate_cost_estimate(
            swapped_count=swapped, seconds=sess.duration_sec, resolution=sess.resolution,
        )
        return HandlerReply(text=(
            f"🎬 Анимация {est['count']} фото × {est['seconds']}с × {est['resolution']}\n"
            f"Стоимость: ~${est['total_usd']:.2f} (${est['per_usd']:.2f}/видео)\n"
            f"Время: ~{est['minutes']:.0f} мин\n\n"
            f"/swapbatch_animate_go — запустить (платно)\n"
            f"/swapbatch_set_quality duration=5|10|15 resolution=720p|1080p — изменить\n"
            f"/swapbatch_no — без анимации"
        ))
```

Add `run_animate_batch_phase` (the paid runner; mirrors `run_animate_phase` but uses `confirm_animate_batch`):

```python
    async def run_animate_batch_phase(
        self,
        chat_id: int,
        animate_fn,
        progress_cb: Callable[[str, dict[str, Any]], None] | None = None,
        *,
        user_id: int | None = None,
        username: str | None = None,
    ) -> HandlerReply:
        sess0 = self.orchestrator.get(chat_id)
        seconds = sess0.duration_sec if sess0 else 10
        resolution = sess0.resolution if sess0 else "720p"
        try:
            await self.orchestrator.confirm_animate_batch(
                chat_id, animate_fn=animate_fn, progress_cb=progress_cb,
            )
        except OrchestratorError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        except Exception as exc:  # noqa: BLE001
            return HandlerReply(text=f"❌ Ошибка animate: {translate_exception(exc)}")

        sess = self.orchestrator.get(chat_id)
        if sess is None:
            return HandlerReply(text="Сессия пропала (вероятно, отменена).")
        videos = [Path(t.animate_result_path) for t in sess.targets if t.animate_result_path]
        succeeded = len(videos)
        failed = sum(1 for t in sess.targets if t.swap_result_path and not t.animate_result_path)
        lines = [f"🎬 Animate завершён: {succeeded} видео"]
        if failed:
            lines.append(f"  ⚠️ {failed} не удалось")
        # Bill ONLY successful videos at the real WaveSpeed rate.
        from app.services.block_m2_video.engines.wavespeed_spicy_engine import cost_for
        amount = succeeded * cost_for(seconds, resolution)
        if user_id is not None and amount > 0:
            try:
                _cost.record_cost(user_id, username, amount)
            except Exception as exc:  # noqa: BLE001
                logger.warning("cost: record_cost failed: %s", exc)
        self.orchestrator.prune(chat_id)
        return HandlerReply(text="\n".join(lines), videos=videos)
```

In `quality_settings.py`, extend parsing/validation to accept `resolution` and snap duration to {5,10,15} for the spicy path. Add a helper used by the handler:

```python
def parse_animate_quality(args_text: str) -> dict:
    """Parse 'duration=5|10|15 resolution=720p|1080p'. Raises QualityError on bad input."""
    out: dict = {}
    for tok in (args_text or "").split():
        if "=" not in tok:
            continue
        k, v = tok.split("=", 1)
        k = k.strip().lower()
        if k == "duration":
            try:
                d = int(v)
            except ValueError as exc:
                raise QualityError("duration должен быть числом 5/10/15") from exc
            if d not in (5, 10, 15):
                raise QualityError("duration: только 5, 10 или 15")
            out["duration"] = d
        elif k == "resolution":
            r = v.strip().lower()
            if r not in ("720p", "1080p"):
                raise QualityError("resolution: только 720p или 1080p")
            out["resolution"] = r
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swapbatch_animate_cost.py tests/test_swapbatch_handler.py -q`
Expected: PASS. If a pre-existing `test_swapbatch_handler.py` test asserted the OLD `handle_animate_yes`/post-swap menu behavior, STOP and report the exact assertion before changing it (the cost-gate is an intended behavior change; surface it).

- [ ] **Step 5: Commit**

```bash
git add app/handlers/face_swap_handler.py app/services/block_m2_face_swap/quality_settings.py tests/test_swapbatch_animate_cost.py
git -c commit.gpgsign=false commit -m "feat(swapbatch): animate cost gate + parallel run_animate_batch_phase"
```

---

### Task 7: Bot wiring — repoint animate to WaveSpeed, cost-gate, progress, flag, settings

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py` (`_swapbatch_dispatch`, `_swapbatch_run_phase`, `_progress`, `handle_set_quality` wiring)

⚠️ This file holds the SWAP code too. Touch ONLY the animate path; do not alter the `command == "go"` swap branch.

- [ ] **Step 1: Route `animate_yes` to the cost-gate (no run) and add `animate_go`**

In `_swapbatch_dispatch`, the animate-enabled guard currently lists animate commands. Change routing so:
- `animate_yes` → `_swapbatch_apply_reply(chat_id_s, handler.handle_animate_yes(chat_id_int))` (shows cost, does NOT run).
- add `animate_go` to the long-running set.

Find the dispatch block that handles `command == "animate_custom"` etc. Add, alongside the simple-reply commands:

```python
    if command == "animate_yes":
        if not animate_enabled():
            send(chat_id_s, "🎬 Видео-фаза временно отключена. /swapbatch_no — завершить.")
            return
        _swapbatch_apply_reply(chat_id_s, handler.handle_animate_yes(chat_id_int))
        return
```

And change the long-running set to include `animate_go` (and keep the others gated by `animate_enabled()` per the existing guard):

```python
    if command in ("go", "animate_go"):
        if command == "animate_go" and not animate_enabled():
            send(chat_id_s, "🎬 Видео-фаза временно отключена. /swapbatch_no — завершить.")
            return
        _swapbatch_run_phase(chat_id_int, chat_id_s, command, handler)
        return
```

(Leave the existing custom-prompt commands — `animate_custom`/`confirm`/`apply_partial`/`apply_first`/`retry` — gated by `animate_enabled()` as today. They are OUT of FLOW-1 scope and must NOT reach the new WaveSpeed path yet; the existing guard already short-circuits them when the flag is off, and the flag is OFF by default.)

- [ ] **Step 2: Implement the `animate_go` branch in `_swapbatch_run_phase` (WaveSpeed, parallel)**

In `_swapbatch_run_phase`, the `_run()` body currently does `if command == "go": ... else: <RunPod animate>`. Replace the `else:` (RunPod animate) branch with an `elif command == "animate_go":` WaveSpeed branch, and leave a final `else:` that rejects the deferred custom commands. New `animate_go` branch:

```python
            elif command == "animate_go":
                from app.services.block_m2_video.engines.router import EngineRouter
                from app.services.block_m2_video.engines.engine_protocol import (
                    VideoRequest, new_generation_id,
                )
                from app.services.block_m2_video.batch_animate import animate_batch

                _hq, _orch_q = _swapbatch_get_handler()
                _sess_q = _orch_q.get(chat_id_int) if _orch_q else None
                _seconds = int(getattr(_sess_q, "duration_sec", 10) or 10)
                _resolution = str(getattr(_sess_q, "resolution", "720p") or "720p")
                _DEFAULT_MOTION_PROMPT = "gentle natural body movement, subtle motion, soft cinematic lighting, photorealistic"
                _concurrency = int(os.getenv("SWAPBATCH_ANIMATE_CONCURRENCY", "2"))

                router = EngineRouter()
                engine = _aio.run(router.select("spicy"))  # WaveSpeedSpicyEngine

                async def _animate_fn(photos, cancel_check):
                    reqs = [
                        VideoRequest(
                            persona_id=f"swapbatch_{chat_id_int}",
                            persona_name="swapbatch",
                            input_image_path=ph,
                            prompt=_DEFAULT_MOTION_PROMPT,
                            seconds=_seconds,
                            resolution=_resolution,
                            generation_id=new_generation_id(),
                        )
                        for ph in photos
                    ]
                    return await animate_batch(
                        engine, reqs, concurrency=_concurrency,
                        progress_cb=_progress, cancel_check=cancel_check,
                    )

                reply = _aio.run(
                    handler.run_animate_batch_phase(
                        chat_id_int, _animate_fn, progress_cb=_progress,
                        user_id=chat_id_int,
                        username=_USERNAME_BY_CHAT.get(chat_id_s),
                    )
                )
```

Then replace the old custom-prompt `else` animate branch with a guard so it can't reach RunPod:

```python
            else:  # confirm / apply_partial / apply_first (custom-prompt) — deferred
                send(chat_id_s, "🎬 Кастомные промпты для видео пока недоступны на новом движке. Используй /swapbatch_animate_yes.")
                reply = None
```

Add the import for `animate_enabled` at the top of the dispatch usage if not present:

```python
from app.services.block_m2_face_swap.cost_estimator import animate_enabled
```

(It was already imported in the earlier animate-isolation work; confirm it's importable at the dispatch site.)

- [ ] **Step 3: Add the `animate_progress` branch to `_progress`**

In the `_progress` callback inside `_swapbatch_run_phase`, add (next to the `swap_progress` branch):

```python
        elif stage == "animate_progress":
            completed = payload.get("completed", 0)
            total = payload.get("total", 0)
            if completed == total or completed % 10 == 0:
                send(chat_id_s, f"🎬 Animate {completed}/{total}…")
```

- [ ] **Step 4: Wire resolution into `/swapbatch_set_quality`**

In `face_swap_handler.handle_set_quality`, use `parse_animate_quality` for the spicy path so `duration=5|10|15 resolution=720p|1080p` is accepted and stored. Minimal change: after the existing parse, also parse resolution and persist via the orchestrator. Replace the body of `handle_set_quality` to:

```python
    def handle_set_quality(self, chat_id: int, args_text: str) -> HandlerReply:
        sess = self.orchestrator.get(chat_id)
        if sess is None:
            return HandlerReply(text="⚠️ Нет активного батча. Начни с /swapbatch_source.")
        from app.services.block_m2_face_swap.quality_settings import (
            parse_animate_quality, QualityError,
        )
        try:
            parsed = parse_animate_quality(args_text or "")
        except QualityError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        if not parsed:
            return HandlerReply(text=(
                f"📐 Текущее: {sess.duration_sec}с, {sess.resolution}.\n"
                f"Изменить: /swapbatch_set_quality duration=5|10|15 resolution=720p|1080p"
            ))
        duration = parsed.get("duration", sess.duration_sec)
        resolution = parsed.get("resolution", sess.resolution)
        self.orchestrator.set_quality(chat_id, duration_sec=duration, fps=sess.fps)
        # persist resolution too
        s = self.orchestrator.get(chat_id)
        if s is not None:
            s.resolution = resolution
            self.orchestrator._persist(s)  # resolution is plain config
        return HandlerReply(text=(
            f"✅ Качество: {duration}с, {resolution}.\n"
            f"Дальше: /swapbatch_animate_yes (покажу стоимость) → /swapbatch_animate_go."
        ))
```

(If touching `set_quality` to also store resolution cleanly is preferred, add a `resolution` param to `BatchOrchestrator.set_quality` instead of the direct `_persist`. Either is acceptable; the direct persist avoids changing the orchestrator signature.)

- [ ] **Step 5: Verify import + existing swap path intact**

Run: `./.venv/Scripts/python.exe -c "import tools.jarvis_smart_telegram_control"`
Expected: clean import.
Run: `./.venv/Scripts/python.exe -m pytest tests/test_swapbatch_handler.py tests/test_swapbatch_accumulation.py tests/test_lucataco_engine.py -q`
Expected: PASS (swap path untouched). If any swap test breaks, STOP and report.

- [ ] **Step 6: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py app/handlers/face_swap_handler.py
git -c commit.gpgsign=false commit -m "feat(swapbatch): animate via WaveSpeed (mode=spicy), cost-gate /swapbatch_animate_go, progress, resolution"
```

---

# PHASE 1 — Live validation (first spend; STOP for the user)

---

### Task 8: ROLLOUT GATE — small live test (2–3 videos)

**This is the spec's required first checkpoint. Do NOT scale to 100 until it passes.**

- [ ] **Step 1: Enable the flag + confirm key/concurrency**

Ensure `C:\jarvis\.env` has `WAVESPEED_API_KEY=...`, `SWAPBATCH_ANIMATE_ENABLED=1`, and (optional) `SWAPBATCH_ANIMATE_CONCURRENCY=2`.
Run: `./.venv/Scripts/python.exe -c "import os; from dotenv import load_dotenv; load_dotenv(r'C:\\jarvis\\.env'); print('key:', bool(os.getenv('WAVESPEED_API_KEY'))); print('animate_enabled flag:', os.getenv('SWAPBATCH_ANIMATE_ENABLED'))"`
Expected: `key: True`, flag `1`.

- [ ] **Step 2: Restart the bot to load new code**

Kill the bot python(s) so the guardian respawns from disk (see jarvis-bot-guardian-task memory): the guardian relaunches within ~30s. Confirm a fresh bot PID + heartbeat, and `from app.services.block_m2_video.engines.wavespeed_spicy_engine import cost_for` imports in the venv.

- [ ] **Step 3: Full mock suite green before live**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_wavespeed_spicy_engine.py tests/test_video_batch_animate.py tests/test_swapbatch_animate_cost.py tests/test_swapbatch_orchestrator.py tests/test_swapbatch_handler.py -q`
Expected: all PASS.

- [ ] **Step 4: STOP — hand off to the user for the live Telegram test**

The user runs, via the bot, with a SMALL batch (2–3 photos):
1. `/swapbatch_source` → face; `/swapbatch_batch` → 2–3 targets → `/swapbatch_go` (swap).
2. `/swapbatch_set_quality duration=10 resolution=720p` (or leave defaults).
3. `/swapbatch_animate_yes` → expect cost line `~$2–3` + `/swapbatch_animate_go`.
4. `/swapbatch_animate_go` → expect progress, then 2–3 **videos** delivered, uncensored, tally.

**Do NOT proceed to Task 9 until the user confirms:** videos arrived, uncensored, cost line correct, 429 not losing clips. Report the prediction ids / any 429 from logs if asked.

---

### Task 9: Scale validation (~10–15, then 100) — user-driven, cost-gated

- [ ] **Step 1: Mid-scale live test (~10–15 videos)**

User runs an ~10–15 swapped batch → `/swapbatch_animate_yes` (cost ~$10–22) → `/swapbatch_animate_go`. Watch logs for 429; the transient sweep should recover them. Confirm tally honest (terminal failures only).

- [ ] **Step 2: Full 100 (when the user is ready — $150 operation)**

User runs 100. The cost-gate shows `~$150 (or per chosen duration/res)` + time before `/swapbatch_animate_go`. Confirm: all delivered (sweep recovers 429), honest tally, videos uncensored. Tune `SWAPBATCH_ANIMATE_CONCURRENCY` from 429 frequency in logs.

- [ ] **Step 3: Note results**

Record actual per-video latency, 429 rate, and total cost for future tuning. STEP-2 FLOW-1 complete when 100 runs clean.

---

## Self-Review

**Spec coverage:**
- WaveSpeedSpicyEngine via VideoGenerator seam → Task 2; router `spicy` → Task 3. ✅
- VideoRequest resolution/negative_prompt → Task 1. ✅
- base64 data-URI input → Task 2 (`_data_uri`). ✅
- Length 5/10/15 (default 10), quality 720/1080 (default 720) → Task 1 defaults, Task 6 `parse_animate_quality`, Task 7 set_quality. ✅
- Mandatory cost+time gate before spend → Task 6 `handle_animate_yes` (shows cost, no run) + Task 7 `/swapbatch_animate_go` paid trigger. ✅
- Money invariant (terminal not retried, transient swept; submit-count==1) → Task 4 runner + tests; engine transient/terminal split Task 2. ✅
- Concurrency=2 (env) + 429 backoff + sweep → Task 2 (backoff) + Task 4 (semaphore+sweep). ✅
- Per-video isolation + honest tally → Task 4 + Task 5 + Task 6. ✅
- Flag OFF default, routes to WaveSpeed when ON, never RunPod → Task 7 (guards + animate_go only path; custom commands deferred/guarded). ✅
- ReplicateEngine kept, RunPod frozen, FLOW-2 forward-compat → Task 3 (router additive), no deletions. ✅
- Delivery videos one-by-one → reuses existing `_send_local_video` (HandlerReply.videos). ✅
- ROLLOUT small-first then 100 + STOP on live → Tasks 8–9. ✅

**Placeholder scan:** No TBD/TODO; full code in every code step. The `_poll` `if False else` is annotated to use the plain `await c.get(...)` form. ✅

**Type consistency:** `animate_fn(photos: list[Path], cancel_check) -> list[Path|None]` consistent across Task 5 (orchestrator), Task 6 (handler), Task 7 (wiring). `cost_for(seconds, resolution)`, `gen_seconds`, `snap_duration` defined in Task 2, used in Tasks 6–7. `confirm_animate_batch`/`run_animate_batch_phase` names consistent. `animate_progress` emitted (Task 4) and consumed (Task 7). `WaveSpeedTransientError`/`WaveSpeedEngineError` defined Task 2, used Task 4. ✅

**Scope note:** Per-photo CUSTOM-prompt animate is explicitly DEFERRED (Task 7 guards it off the WaveSpeed path) to keep FLOW 1 focused and avoid touching the swap-adjacent custom-prompt state machine. FLOW 2 (engine choice + standalone `/animate`) is out of scope by design.

**Test infra:** async tests assume `pytest-asyncio` (already used by the swap suite). If async collection errors, confirm `asyncio_mode = auto`; `@pytest.mark.asyncio` is also present.
```
