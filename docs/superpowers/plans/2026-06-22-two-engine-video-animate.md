# Two-Engine Video Animate (WaveSpeed + Seedance) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a `/swapbatch` swap (and via standalone `/animate`), animate photos → short videos through two managed engines — **WaveSpeed wan-2.6 spicy** (uncensored) and **Replicate seedance-1-pro-fast** (cheap, SFW) — choosing engine, prompt, length and resolution in the UI, behind a mandatory cost-confirmation gate, money-safe (transient-only) retries, and **no RunPod**.

**Architecture:** Both engines implement the existing `VideoGenerator` protocol and are selected by `EngineRouter` mode (`spicy` / `seedance`). A per-engine `EngineCapabilities` descriptor is the single source the UI, cost gate, and validation read from. A generic `animate_batch` runner adds concurrency (semaphore=2) + 429 backoff + transient-only sweep + per-video isolation, catching a shared `TransientVideoError` base so the second engine drops in additively. **Phase A** ships WaveSpeed end-to-end (the core skin case) and must be fully user-tested before **Phase B** adds Seedance + engine choice + `/animate`.

**Tech Stack:** Python 3, `httpx` (async REST, `MockTransport` for tests), `pytest` + `pytest-asyncio`. WaveSpeed `alibaba/wan-2.6/image-to-video-spicy` (Bearer auth). Replicate `bytedance/seedance-1-pro-fast` (browser User-Agent + base64 data-URI to dodge the Cloudflare tarpit). Submit→poll→download mp4.

**Spec:** `docs/superpowers/specs/2026-06-22-two-engine-video-animate-design.md`

**Verified live (2026-06-22):**
- WaveSpeed wan-2.6-spicy → real 15s/30fps mp4, uncensored, from a base64 data-URI; submit→poll on `data.urls.get`; output at `data.outputs[0]`. 720p 15s = $1.50.
- Seedance seedance-1-pro-fast → 5s/1080p mp4: 1248×1664, **24.000 fps, 121 frames = 5.04s**, warm start (~5s queue, 144s total), excellent identity/motion consistency. Schema: `duration` int 2–12, `resolution` {480p,720p,1080p}, `fps` 24 fixed, `prompt` required, `aspect_ratio` ignored when image supplied.

**Money invariant (carried from swap, DO NOT break):** a per-video TERMINAL/billable failure (completed-but-failed prediction, 4xx) is NEVER retried; only TRANSIENT failures (`TransientVideoError`: 429/network, no prediction created) are retried/swept. Tests assert **submit/post-count == 1** for terminal failures.

**Safeties (non-negotiable, see Risks):** cost-gate before every paid run + worst-case ceiling shown (WaveSpeed 100×15s×1080p = $225; Seedance 100×10s×1080p ≈ $55); concurrency=2 + backoff + sweep on BOTH engines; ROLLOUT GATE small (2–3) test before 100 on EACH engine; first paid run = STOP and hand to the user; never break existing swap tests silently; concurrency is PARALLEL (semaphore), never sequential.

---

## File Structure

**New:**
- `app/services/block_m2_video/engines/errors.py` — shared `TransientVideoError` / `TerminalVideoError` bases.
- `app/services/block_m2_video/engines/capabilities.py` — `EngineCapabilities` + `WAVESPEED_CAPS` + `SEEDANCE_CAPS` + `caps_for(mode)`. Single source for allowed durations/resolutions, defaults, native fps, pricing, wall-clock.
- `app/services/block_m2_video/engines/wavespeed_spicy_engine.py` — `WaveSpeedSpicyEngine` (Phase A).
- `app/services/block_m2_video/engines/replicate_seedance_engine.py` — `ReplicateSeedanceEngine` (Phase B).
- `app/services/block_m2_video/batch_animate.py` — engine-agnostic `animate_batch` runner.
- `app/services/block_m2_face_swap/animate_session.py` — standalone `/animate` session store (Phase B).
- Tests: `tests/test_video_capabilities.py`, `tests/test_wavespeed_spicy_engine.py`, `tests/test_replicate_seedance_engine.py`, `tests/test_video_batch_animate.py`, `tests/test_swapbatch_animate_cost.py`, `tests/test_animate_standalone.py`.

**Modified:**
- `app/services/block_m2_video/engines/engine_protocol.py` — `VideoRequest` gains `resolution`, `negative_prompt`; `GenerationMode` gains `"spicy"`, `"seedance"`.
- `app/services/block_m2_video/engines/router.py` — `_get_wavespeed`/`select("spicy")` (A); `_get_seedance`/`select("seedance")` (B).
- `app/services/block_m2_face_swap/batch_orchestrator.py` — `BatchSession.resolution`, `.video_engine`, `.motion_prompt`; `confirm_animate_batch`; `set_engine_and_defaults`.
- `app/services/block_m2_face_swap/quality_settings.py` — `parse_animate_quality` (engine-constrained), `QualityError`.
- `app/handlers/face_swap_handler.py` — `animate_cost_estimate` (caps-based), `handle_animate_yes` (cost gate), `handle_set_prompt`, `handle_set_quality`, `run_animate_batch_phase`, engine-pick handlers.
- `tools/jarvis_smart_telegram_control.py` — repoint animate to the selected managed engine; `/swapbatch_animate_go`; `/swapbatch_set_prompt`; engine-pick commands; progress; flag. (Phase B: `/animate` wiring.)

**Test command (Windows venv):** `./.venv/Scripts/python.exe -m pytest <path> -v`

---

# PHASE A — WaveSpeed end-to-end (core; user-tested before Phase B)

---

### Task 1: Shared error bases + VideoRequest fields + modes

**Files:**
- Create: `app/services/block_m2_video/engines/errors.py`
- Modify: `app/services/block_m2_video/engines/engine_protocol.py`
- Test: `tests/test_wavespeed_spicy_engine.py` (create, first test only)

- [ ] **Step 1: Write the failing test**

Create `tests/test_wavespeed_spicy_engine.py`:

```python
from pathlib import Path

from app.services.block_m2_video.engines.engine_protocol import VideoRequest
from app.services.block_m2_video.engines.errors import (
    TransientVideoError, TerminalVideoError,
)


def test_video_request_has_resolution_and_negative_prompt_defaults():
    r = VideoRequest(
        persona_id="p", persona_name="n",
        input_image_path=Path("x.jpg"), prompt="move",
    )
    assert r.resolution == "720p"
    assert r.negative_prompt == ""
    assert r.seconds == 5


def test_error_bases_are_distinct_runtimeerrors():
    assert issubclass(TransientVideoError, RuntimeError)
    assert issubclass(TerminalVideoError, RuntimeError)
    assert not issubclass(TransientVideoError, TerminalVideoError)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_wavespeed_spicy_engine.py -v`
Expected: FAIL (`No module named ...errors` / `unexpected keyword 'resolution'`).

- [ ] **Step 3: Create the error bases**

Create `app/services/block_m2_video/engines/errors.py`:

```python
# -*- coding: utf-8 -*-
"""Shared video-engine error classes.

The runner distinguishes RETRYABLE from BILLABLE failures by these bases, so a
new engine only needs to subclass the right one to get correct money behavior.
"""
from __future__ import annotations


class TransientVideoError(RuntimeError):
    """Retryable: 429 / network, NO prediction created -> safe to retry & sweep."""


class TerminalVideoError(RuntimeError):
    """Terminal/billable: completed-but-failed prediction or a 4xx -> NEVER retry."""
```

- [ ] **Step 4: Add VideoRequest fields + modes**

In `engine_protocol.py`, change the `GenerationMode` line:

```python
GenerationMode = Literal["fast", "hq", "auto", "spicy", "seedance"]
```

In the `VideoRequest` dataclass, after the `generation_id` field, add:

```python
    # Managed-engine extras. Ignored by Replicate(legacy)/RunPod (kept default).
    resolution: str = "720p"
    negative_prompt: str = ""  # WaveSpeed only; Seedance ignores
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_wavespeed_spicy_engine.py -v`
Expected: PASS.

- [ ] **Step 6: Verify nothing else broke**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_replicate_engine.py tests/test_persona_video_handler.py -q`
Expected: PASS (new fields optional; legacy engine unaffected). If anything fails, STOP and report.

- [ ] **Step 7: Commit**

```bash
git add app/services/block_m2_video/engines/errors.py app/services/block_m2_video/engines/engine_protocol.py tests/test_wavespeed_spicy_engine.py
git -c commit.gpgsign=false commit -m "feat(video): shared transient/terminal error bases + VideoRequest resolution/negative_prompt + spicy/seedance modes"
```

---

### Task 2: Per-engine capability descriptor (drives UI + cost)

**Files:**
- Create: `app/services/block_m2_video/engines/capabilities.py`
- Test: `tests/test_video_capabilities.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_video_capabilities.py`:

```python
import pytest

from app.services.block_m2_video.engines.capabilities import (
    caps_for, WAVESPEED_CAPS, SEEDANCE_CAPS,
)


def test_wavespeed_caps_shape():
    c = WAVESPEED_CAPS
    assert c.engine_mode == "spicy"
    assert c.allowed_durations == (5, 10, 15)
    assert c.allowed_resolutions == ("720p", "1080p")
    assert c.native_fps == 30
    assert c.censored is False


def test_seedance_caps_shape():
    c = SEEDANCE_CAPS
    assert c.engine_mode == "seedance"
    assert c.allowed_durations == (5, 10)
    assert c.allowed_resolutions == ("480p", "720p", "1080p")
    assert c.native_fps == 24
    assert c.censored is True


def test_snap_duration_and_resolution():
    assert WAVESPEED_CAPS.snap_duration(7) == 5
    assert WAVESPEED_CAPS.snap_duration(13) == 15
    assert SEEDANCE_CAPS.snap_duration(15) == 10           # seedance has no 15
    assert SEEDANCE_CAPS.snap_resolution("1080p") == "1080p"
    assert WAVESPEED_CAPS.snap_resolution("480p") == "720p"  # WS has no 480; -> default


def test_cost_for_tables():
    assert WAVESPEED_CAPS.cost_for(15, "720p") == pytest.approx(1.50)
    assert WAVESPEED_CAPS.cost_for(5, "1080p") == pytest.approx(0.75)
    assert SEEDANCE_CAPS.cost_for(10, "1080p") == pytest.approx(0.55)
    assert SEEDANCE_CAPS.cost_for(5, "720p") == pytest.approx(0.11)


def test_caps_for_mode():
    assert caps_for("spicy") is WAVESPEED_CAPS
    assert caps_for("seedance") is SEEDANCE_CAPS
    with pytest.raises(KeyError):
        caps_for("nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_video_capabilities.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Write the descriptor**

Create `app/services/block_m2_video/engines/capabilities.py`:

```python
# -*- coding: utf-8 -*-
"""Per-engine capability descriptors.

The single source of truth the UI, cost gate, and request validation read from.
Each engine offers exactly what it can do; the engine implementations also use
their own caps to snap duration/resolution and compute cost.

Seedance pricing is APPROXIMATE (Replicate token billing) — refine from the
first real billing. WaveSpeed pricing is verified.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EngineCapabilities:
    engine_mode: str
    display_name: str
    allowed_durations: tuple[int, ...]
    allowed_resolutions: tuple[str, ...]
    default_duration: int
    default_resolution: str
    native_fps: int
    censored: bool
    pricing: dict           # {(resolution, seconds): usd}
    gen_seconds_table: dict  # {seconds: rough wall-clock seconds per clip}

    def snap_duration(self, seconds: int) -> int:
        return min(self.allowed_durations, key=lambda d: (abs(d - seconds), d))

    def snap_resolution(self, resolution: str) -> str:
        return resolution if resolution in self.allowed_resolutions else self.default_resolution

    def cost_for(self, seconds: int, resolution: str) -> float:
        return self.pricing[(self.snap_resolution(resolution), self.snap_duration(seconds))]

    def gen_seconds(self, seconds: int) -> int:
        return self.gen_seconds_table[self.snap_duration(seconds)]


WAVESPEED_CAPS = EngineCapabilities(
    engine_mode="spicy",
    display_name="WaveSpeed (без цензуры)",
    allowed_durations=(5, 10, 15),
    allowed_resolutions=("720p", "1080p"),
    default_duration=10,
    default_resolution="720p",
    native_fps=30,
    censored=False,
    pricing={
        ("720p", 5): 0.50, ("720p", 10): 1.00, ("720p", 15): 1.50,
        ("1080p", 5): 0.75, ("1080p", 10): 1.50, ("1080p", 15): 2.25,
    },
    gen_seconds_table={5: 40, 10: 60, 15: 90},
)

SEEDANCE_CAPS = EngineCapabilities(
    engine_mode="seedance",
    display_name="Seedance (дёшево, 10с/1080p)",
    allowed_durations=(5, 10),
    allowed_resolutions=("480p", "720p", "1080p"),
    default_duration=5,
    default_resolution="1080p",
    native_fps=24,
    censored=True,
    pricing={  # APPROX — refine from first real Replicate billing
        ("480p", 5): 0.05, ("480p", 10): 0.10,
        ("720p", 5): 0.11, ("720p", 10): 0.22,
        ("1080p", 5): 0.25, ("1080p", 10): 0.55,
    },
    gen_seconds_table={5: 90, 10: 150},
)

CAPS_BY_MODE = {"spicy": WAVESPEED_CAPS, "seedance": SEEDANCE_CAPS}


def caps_for(mode: str) -> EngineCapabilities:
    """Return the capability descriptor for an engine mode, else KeyError."""
    return CAPS_BY_MODE[mode]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_video_capabilities.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_video/engines/capabilities.py tests/test_video_capabilities.py
git -c commit.gpgsign=false commit -m "feat(video): per-engine EngineCapabilities descriptor (WaveSpeed + Seedance) with snap/cost"
```

---

### Task 3: WaveSpeedSpicyEngine

**Files:**
- Create: `app/services/block_m2_video/engines/wavespeed_spicy_engine.py`
- Test: `tests/test_wavespeed_spicy_engine.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wavespeed_spicy_engine.py`:

```python
import httpx
import pytest

from app.services.block_m2_video.engines.wavespeed_spicy_engine import (
    WaveSpeedSpicyEngine, WaveSpeedTransientError, WaveSpeedEngineError,
)
from app.services.block_m2_video.engines.errors import (
    TransientVideoError, TerminalVideoError,
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


def test_wavespeed_errors_subclass_shared_bases():
    assert issubclass(WaveSpeedTransientError, TransientVideoError)
    assert issubclass(WaveSpeedEngineError, TerminalVideoError)


@pytest.mark.asyncio
async def test_generate_success(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(200, json={"code": 200, "data": {"id": "v1", "status": "created", "urls": {"get": "https://api.wavespeed.ai/api/v3/predictions/v1/result"}}})
        return httpx.Response(200, json={"data": {"status": "completed", "outputs": ["https://cdn/x.mp4"]}})

    from app.services.block_m2_video.engines.engine_protocol import VideoRequest
    eng = _engine(handler)
    res = await eng.generate(VideoRequest(
        persona_id="swapbatch_1", persona_name="b",
        input_image_path=_img(tmp_path), prompt="move", seconds=15, resolution="720p",
    ))
    assert res.output_path.exists()
    assert res.cost_usd == pytest.approx(1.50)
    assert res.engine == "wavespeed_spicy"
    assert res.seconds == 15


@pytest.mark.asyncio
async def test_generate_429_exhausted_raises_transient(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(429, json={"message": "rate limited"})
        return httpx.Response(200, json={"data": {"status": "completed", "outputs": ["x"]}})

    from app.services.block_m2_video.engines.engine_protocol import VideoRequest
    eng = _engine(handler, max_retries=2, backoff_base=0.0)
    with pytest.raises(WaveSpeedTransientError):
        await eng.generate(VideoRequest(
            persona_id="p", persona_name="b",
            input_image_path=_img(tmp_path), prompt="m", seconds=10,
        ))


@pytest.mark.asyncio
async def test_generate_failed_status_is_terminal(tmp_path):
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


@pytest.mark.asyncio
async def test_submit_4xx_is_terminal_not_retried(tmp_path):
    posts = {"n": 0}
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            posts["n"] += 1
            return httpx.Response(400, json={"message": "bad request"})
        return httpx.Response(200, json={"data": {"status": "completed", "outputs": ["x"]}})

    from app.services.block_m2_video.engines.engine_protocol import VideoRequest
    eng = _engine(handler, max_retries=5, backoff_base=0.0)
    with pytest.raises(WaveSpeedEngineError):
        await eng.generate(VideoRequest(
            persona_id="p", persona_name="b",
            input_image_path=_img(tmp_path), prompt="m", seconds=5,
        ))
    assert posts["n"] == 1   # MONEY: 4xx never retried
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_wavespeed_spicy_engine.py -q`
Expected: FAIL (module `wavespeed_spicy_engine` not found).

- [ ] **Step 3: Write the engine**

Create `app/services/block_m2_video/engines/wavespeed_spicy_engine.py`:

```python
# -*- coding: utf-8 -*-
"""WaveSpeed wan-2.6 spicy image-to-video engine (uncensored, managed).

Patient 429 backoff; WaveSpeedTransientError (429/network, no prediction created
-> retry/sweep) vs WaveSpeedEngineError (completed-but-failed / 4xx -> terminal,
billable, never retry). Local image sent as a base64 data-URI (no hosting).
Never logs the API key. Cost/snap come from WAVESPEED_CAPS.
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

import httpx

from .capabilities import WAVESPEED_CAPS
from .engine_protocol import VideoRequest, VideoResult, new_generation_id
from .errors import TerminalVideoError, TransientVideoError

logger = logging.getLogger(__name__)

_BASE = "https://api.wavespeed.ai"
_MODEL = "alibaba/wan-2.6/image-to-video-spicy"
_SUBMIT = f"{_BASE}/api/v3/{_MODEL}"


class WaveSpeedEngineError(TerminalVideoError):
    """Terminal failure: prediction completed-but-failed, or a 4xx."""


class WaveSpeedTransientError(TransientVideoError):
    """429/network retries exhausted; NO prediction created -> safe to retry."""


class WaveSpeedSpicyEngine:
    engine_name = "wavespeed_spicy"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        download_transport: httpx.BaseTransport | None = None,
        max_retries: int = 8,
        backoff_base: float = 1.0,   # tests set 0.0 to skip sleeps
    ) -> None:
        self._key = (api_key or os.getenv("WAVESPEED_API_KEY", "")).strip()
        if not self._key:
            raise WaveSpeedEngineError("WAVESPEED_API_KEY not set")
        self._headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        self._transport = transport
        self._dl_transport = download_transport
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    async def is_available(self) -> bool:
        return bool(self._key)

    async def generate(self, request: VideoRequest) -> VideoResult:
        start = time.monotonic()
        gen_id = request.generation_id or new_generation_id()
        seconds = WAVESPEED_CAPS.snap_duration(request.seconds)
        resolution = WAVESPEED_CAPS.snap_resolution(request.resolution)
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
            generation_id=gen_id, persona_id=request.persona_id, output_path=out_path,
            engine=self.engine_name, model=_MODEL,
            seed=request.seed if request.seed is not None else -1,
            cost_usd=WAVESPEED_CAPS.cost_for(seconds, resolution),
            duration_sec=time.monotonic() - start, timestamp=datetime.now(timezone.utc),
            prompt=request.prompt, seconds=seconds,
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
                logger.warning("WaveSpeed submit %d/%d failed: %s", attempt, self._max_retries, exc)
                if attempt < self._max_retries:
                    wait = (10.0 + 2 ** attempt + random.uniform(0, 5)) if code == 429 else 2 ** attempt
                    await asyncio.sleep(wait * self._backoff_base)
            except Exception as exc:  # network/transport — retryable, not billed
                last = exc
                logger.warning("WaveSpeed submit %d/%d error: %s", attempt, self._max_retries, exc)
                if attempt < self._max_retries:
                    await asyncio.sleep((2 ** attempt) * self._backoff_base)
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
                await asyncio.sleep(interval * self._backoff_base)
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
                await asyncio.sleep((2 ** attempt) * self._backoff_base)
        raise WaveSpeedEngineError(f"download failed: {last}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_wavespeed_spicy_engine.py -v`
Expected: PASS (success, 429→transient, failed→terminal, 4xx submit-count==1).

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_video/engines/wavespeed_spicy_engine.py tests/test_wavespeed_spicy_engine.py
git -c commit.gpgsign=false commit -m "feat(video): WaveSpeedSpicyEngine (uncensored, base64, transient/terminal via shared bases, caps cost)"
```

---

### Task 4: Router spicy branch

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

In `router.py` `__init__`, after the `runpod` param add `wavespeed: VideoGenerator | None = None,` and in the body after `self._runpod = runpod` add `self._wavespeed = wavespeed`.

Add a getter after `_get_runpod`:

```python
    def _get_wavespeed(self) -> VideoGenerator:
        if self._wavespeed is None:
            from .wavespeed_spicy_engine import WaveSpeedSpicyEngine
            self._wavespeed = WaveSpeedSpicyEngine()
        return self._wavespeed
```

In `select`, add before the `fast` branch:

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

### Task 5: Generic `animate_batch` runner (semaphore + transient-only sweep + isolation)

**Files:**
- Create: `app/services/block_m2_video/batch_animate.py`
- Test: `tests/test_video_batch_animate.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_video_batch_animate.py`:

```python
from pathlib import Path
from types import SimpleNamespace

from app.services.block_m2_video.batch_animate import animate_batch
from app.services.block_m2_video.engines.errors import (
    TransientVideoError, TerminalVideoError,
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
            raise TransientVideoError("429")
        raise TerminalVideoError("failed")


def _req(prompt):
    return SimpleNamespace(prompt=prompt)


@pytest.mark.asyncio
async def test_animate_batch_isolates_and_aligns():
    eng = _ScriptedEngine({"a": ["ok"], "b": ["terminal"], "c": ["ok"]})
    out = await animate_batch(eng, [_req("a"), _req("b"), _req("c")], concurrency=2)
    assert [str(p) if p else None for p in out] == ["/tmp/a.mp4", None, "/tmp/c.mp4"]


@pytest.mark.asyncio
async def test_animate_batch_sweeps_transient_only():
    eng = _ScriptedEngine({"a": ["transient", "ok"], "b": ["terminal"]})
    out = await animate_batch(eng, [_req("a"), _req("b")], concurrency=2, sweep_pause=0.0)
    assert out[0] == Path("/tmp/a.mp4")   # recovered by sweep
    assert out[1] is None                  # terminal stays failed
    assert eng.calls["a"] == 2             # retried once (transient)
    assert eng.calls["b"] == 1             # MONEY: terminal never retried


@pytest.mark.asyncio
async def test_animate_batch_respects_cancel():
    eng = _ScriptedEngine({"a": ["ok"], "b": ["ok"]})
    out = await animate_batch(eng, [_req("a"), _req("b")], concurrency=1, cancel_check=lambda: True)
    assert out == [None, None]             # nothing generated
    assert eng.calls == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_video_batch_animate.py -q`
Expected: FAIL (module `batch_animate` not found).

- [ ] **Step 3: Write the runner**

Create `app/services/block_m2_video/batch_animate.py`:

```python
# -*- coding: utf-8 -*-
"""Engine-agnostic concurrent runner for N video generations.

Money-safe like the swap engine: a semaphore caps concurrent generations (429
defense), each generation is isolated, and only TRANSIENT failures
(TransientVideoError) are retried in a final sequential sweep. TERMINAL/billable
failures are recorded as None and NEVER retried. Returns a list aligned to
``requests`` (Path on success, None on failure).
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from .engines.errors import TransientVideoError

logger = logging.getLogger(__name__)


async def animate_batch(
    engine,
    requests: list,
    *,
    concurrency: int = 2,
    progress_cb=None,
    cancel_check=None,
    sweep_pause: float = 2.0,
) -> list:
    total = len(requests)
    results: list = [None] * total
    transient_idxs: list = []
    sem = asyncio.Semaphore(max(1, concurrency))
    done = {"n": 0}
    done_lock = asyncio.Lock()

    async def _attempt(idx: int, req):
        try:
            res = await engine.generate(req)
            return res.output_path, False
        except TransientVideoError as exc:
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
        if sweep_pause:
            await asyncio.sleep(sweep_pause)
        path, _ = await _attempt(idx, requests[idx])
        if path is not None:
            results[idx] = path
        if progress_cb:
            progress_cb("animate_retry", {"index": idx, "ok": path is not None})
    return results
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_video_batch_animate.py -v`
Expected: PASS (alignment/isolation, transient-swept, terminal `calls==1`, cancel).

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_video/batch_animate.py tests/test_video_batch_animate.py
git -c commit.gpgsign=false commit -m "feat(video): engine-agnostic animate_batch runner (semaphore, transient-only sweep, cancel)"
```

---

### Task 6: Orchestrator — session fields + `confirm_animate_batch` (parallel)

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
    import asyncio
    async def _swap(src, tgts, cc): return [_jpg(tmp_path / "r0.jpg"), _jpg(tmp_path / "r1.jpg")]
    asyncio.run(orch.confirm_swap(chat, swap_fn=_swap))


def test_session_video_defaults(tmp_path):
    orch = _orch(tmp_path); chat = 1
    _seed_swapped(orch, chat, tmp_path)
    sess = orch.get(chat)
    assert sess.video_engine == "spicy"
    assert sess.resolution == "720p"
    assert sess.motion_prompt == ""


@pytest.mark.asyncio
async def test_confirm_animate_batch_records_videos(tmp_path):
    orch = _orch(tmp_path); chat = 1
    _seed_swapped(orch, chat, tmp_path)

    async def _animate_fn(photos, cancel_check):
        return [tmp_path / "v0.mp4", None]   # one ok, one failed

    out = await orch.confirm_animate_batch(chat, animate_fn=_animate_fn)
    sess = orch.get(chat)
    assert sess.status == STATE_DONE
    swapped = [t for t in sess.targets if t.swap_result_path]
    assert swapped[0].animate_result_path == str(tmp_path / "v0.mp4")
    assert swapped[1].animate_result_path is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swapbatch_animate_cost.py -v`
Expected: FAIL (`no attribute video_engine` / `confirm_animate_batch`).

- [ ] **Step 3: Add session fields + `confirm_animate_batch`**

In `batch_orchestrator.py`, in `BatchSession` after the `fps: int = 21` field, add:

```python
    resolution: str = "720p"
    video_engine: str = "spicy"   # "spicy" | "seedance"
    motion_prompt: str = ""        # shared batch motion prompt; "" -> engine default
```

In `BatchSession.from_dict`, after the `fps=int(...)` line, add:

```python
            resolution=str(data.get("resolution", "720p")),
            video_engine=str(data.get("video_engine", "spicy")),
            motion_prompt=str(data.get("motion_prompt", "")),
```

Add this method to `BatchOrchestrator` right after `confirm_animate` (it mirrors `confirm_swap`: hands ALL swapped photos to a parallel `animate_fn`, maps results back, transitions SWAP_DONE→ANIMATING→DONE):

```python
    async def confirm_animate_batch(
        self,
        chat_id: int,
        *,
        animate_fn: Callable[
            [list[Path], Callable[[], bool]], Awaitable[list]
        ],
        progress_cb: ProgressCb | None = None,
    ) -> list:
        """Animate all swapped photos IN PARALLEL via injected ``animate_fn``.

        ``animate_fn(photos, cancel_check) -> list[Path|None]`` aligns to the
        swapped photos in display order; each maps back to its target's
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

`Path`, `Callable`, `Awaitable`, `STATE_SWAP_DONE`, `STATE_ANIMATING`, `STATE_DONE`, `OrchestratorError`, `ProgressCb` are already imported/defined in this module (confirm at the top; `_require`, `_touch`, `_persist`, `_fire` already exist).

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swapbatch_animate_cost.py tests/test_swapbatch_orchestrator.py -q`
Expected: PASS (new fields default; persistence round-trip stays green). If an existing orchestrator test fails, STOP and report.

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_face_swap/batch_orchestrator.py tests/test_swapbatch_animate_cost.py
git -c commit.gpgsign=false commit -m "feat(swapbatch): BatchSession video_engine/resolution/motion_prompt + confirm_animate_batch (parallel)"
```

---

### Task 7: Quality + prompt settings (engine-constrained)

**Files:**
- Modify: `app/services/block_m2_face_swap/quality_settings.py`
- Test: `tests/test_swapbatch_animate_cost.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_swapbatch_animate_cost.py`:

```python
def test_parse_animate_quality_spicy_ok():
    from app.services.block_m2_face_swap.quality_settings import parse_animate_quality
    out = parse_animate_quality("duration=15 resolution=1080p", engine_mode="spicy")
    assert out == {"duration": 15, "resolution": "1080p"}


def test_parse_animate_quality_rejects_unsupported_for_engine():
    from app.services.block_m2_face_swap.quality_settings import (
        parse_animate_quality, QualityError,
    )
    # seedance has no 15s
    with pytest.raises(QualityError):
        parse_animate_quality("duration=15", engine_mode="seedance")
    # wavespeed has no 480p
    with pytest.raises(QualityError):
        parse_animate_quality("resolution=480p", engine_mode="spicy")


def test_parse_animate_quality_empty_returns_empty():
    from app.services.block_m2_face_swap.quality_settings import parse_animate_quality
    assert parse_animate_quality("", engine_mode="spicy") == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swapbatch_animate_cost.py -q`
Expected: FAIL (`parse_animate_quality` missing).

- [ ] **Step 3: Add the parser (engine-constrained via caps)**

In `quality_settings.py`, add (keep any existing `QualityError`; if absent, define it):

```python
class QualityError(ValueError):
    """Raised on invalid quality settings for the chosen engine."""


def parse_animate_quality(args_text: str, *, engine_mode: str) -> dict:
    """Parse 'duration=.. resolution=..' constrained to the engine's caps.

    Only values the selected engine supports are accepted (e.g. Seedance has no
    15s; WaveSpeed has no 480p). Raises QualityError on anything unsupported.
    """
    from app.services.block_m2_video.engines.capabilities import caps_for
    caps = caps_for(engine_mode)
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
                raise QualityError("duration должен быть числом") from exc
            if d not in caps.allowed_durations:
                allowed = "/".join(str(x) for x in caps.allowed_durations)
                raise QualityError(f"duration для {engine_mode}: только {allowed}с")
            out["duration"] = d
        elif k == "resolution":
            r = v.strip().lower()
            if r not in caps.allowed_resolutions:
                allowed = "/".join(caps.allowed_resolutions)
                raise QualityError(f"resolution для {engine_mode}: только {allowed}")
            out["resolution"] = r
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swapbatch_animate_cost.py tests/test_quality_settings.py -q`
Expected: PASS (new + existing quality tests; if a pre-existing test asserted the old `parse_animate_quality` signature, STOP and report before changing it).

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_face_swap/quality_settings.py tests/test_swapbatch_animate_cost.py
git -c commit.gpgsign=false commit -m "feat(swapbatch): engine-constrained parse_animate_quality (caps-driven)"
```

---

### Task 8: Handler — cost gate, prompt/quality setters, parallel run

**Files:**
- Modify: `app/handlers/face_swap_handler.py`
- Test: `tests/test_swapbatch_animate_cost.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_swapbatch_animate_cost.py`:

```python
def test_animate_cost_estimate_math():
    from app.handlers.face_swap_handler import animate_cost_estimate
    # 2 photos, spicy, 10s, 720p -> 2 * 1.00 = $2.00
    est = animate_cost_estimate(swapped_count=2, seconds=10, resolution="720p", engine_mode="spicy")
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
    assert orch.status(chat) == "SWAP_DONE"   # NOT run


def test_handle_set_prompt_stores_shared_prompt(tmp_path):
    from app.handlers.face_swap_handler import FaceSwapHandler
    orch = _orch(tmp_path); chat = 8
    _seed_swapped(orch, chat, tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    reply = h.handle_set_prompt(chat, "slow dance, neon light")
    assert "slow dance" in (orch.get(chat).motion_prompt)
    assert reply.text is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swapbatch_animate_cost.py -q`
Expected: FAIL (`animate_cost_estimate`/`handle_animate_yes`/`handle_set_prompt` missing).

- [ ] **Step 3: Add the cost estimate + handler methods**

In `face_swap_handler.py`, add a module-level function (before `class FaceSwapHandler`):

```python
def animate_cost_estimate(*, swapped_count: int, seconds: int, resolution: str, engine_mode: str) -> dict:
    """Cost + rough wall-clock for animating N videos on the chosen engine."""
    import math, os
    from app.services.block_m2_video.engines.capabilities import caps_for
    caps = caps_for(engine_mode)
    per = caps.cost_for(seconds, resolution)
    concurrency = max(1, int(os.getenv("SWAPBATCH_ANIMATE_CONCURRENCY", "2")))
    minutes = math.ceil(swapped_count / concurrency) * caps.gen_seconds(seconds) / 60.0
    return {
        "count": swapped_count, "per_usd": per,
        "total_usd": round(per * swapped_count, 2),
        "minutes": round(minutes, 1),
        "seconds": caps.snap_duration(seconds),
        "resolution": caps.snap_resolution(resolution),
        "engine_mode": engine_mode, "display_name": caps.display_name,
    }
```

Add `handle_animate_yes` (SHOWS cost only — explicit `/swapbatch_animate_go` spends):

```python
    def handle_animate_yes(self, chat_id: int) -> HandlerReply:
        sess = self.orchestrator.get(chat_id)
        if sess is None or sess.status != STATE_SWAP_DONE:
            return HandlerReply(text="⚠️ Сначала заверши swap (/swapbatch_go).")
        swapped = sum(1 for t in sess.targets if t.swap_result_path)
        if swapped == 0:
            return HandlerReply(text="⚠️ Нет swapped фото для анимации.")
        est = animate_cost_estimate(
            swapped_count=swapped, seconds=sess.duration_sec,
            resolution=sess.resolution, engine_mode=sess.video_engine,
        )
        prompt_line = sess.motion_prompt or "(дефолтный промт движения)"
        return HandlerReply(text=(
            f"🎬 {est['display_name']}\n"
            f"Анимация {est['count']} фото × {est['seconds']}с × {est['resolution']}\n"
            f"Промт: {prompt_line}\n"
            f"Стоимость: ~${est['total_usd']:.2f} (${est['per_usd']:.2f}/видео)\n"
            f"Время: ~{est['minutes']:.0f} мин\n\n"
            f"/swapbatch_animate_go — запустить (платно)\n"
            f"/swapbatch_set_prompt <текст> — задать движение/сцену\n"
            f"/swapbatch_set_quality duration=.. resolution=.. — качество\n"
            f"/swapbatch_no — без анимации"
        ))
```

Add `handle_set_prompt`:

```python
    def handle_set_prompt(self, chat_id: int, text: str) -> HandlerReply:
        sess = self.orchestrator.get(chat_id)
        if sess is None:
            return HandlerReply(text="⚠️ Нет активного батча.")
        sess.motion_prompt = (text or "").strip()
        self.orchestrator._persist(sess)
        if sess.motion_prompt:
            return HandlerReply(text=f"✅ Промт движения задан:\n«{sess.motion_prompt}»")
        return HandlerReply(text="✅ Промт сброшен на дефолтный.")
```

Add `handle_set_quality` (engine-constrained; persists duration + resolution):

```python
    def handle_set_quality(self, chat_id: int, args_text: str) -> HandlerReply:
        sess = self.orchestrator.get(chat_id)
        if sess is None:
            return HandlerReply(text="⚠️ Нет активного батча. Начни с /swapbatch_source.")
        from app.services.block_m2_face_swap.quality_settings import (
            parse_animate_quality, QualityError,
        )
        from app.services.block_m2_video.engines.capabilities import caps_for
        caps = caps_for(sess.video_engine)
        try:
            parsed = parse_animate_quality(args_text or "", engine_mode=sess.video_engine)
        except QualityError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        if not parsed:
            dur = "/".join(str(x) for x in caps.allowed_durations)
            res = "/".join(caps.allowed_resolutions)
            return HandlerReply(text=(
                f"📐 {caps.display_name}: сейчас {sess.duration_sec}с, {sess.resolution}, "
                f"fps {caps.native_fps} (нативный, не настраивается).\n"
                f"Доступно: duration={dur}, resolution={res}.\n"
                f"Изменить: /swapbatch_set_quality duration=.. resolution=.."
            ))
        if "duration" in parsed:
            sess.duration_sec = parsed["duration"]
        if "resolution" in parsed:
            sess.resolution = parsed["resolution"]
        self.orchestrator._persist(sess)
        return HandlerReply(text=(
            f"✅ Качество: {sess.duration_sec}с, {sess.resolution} "
            f"(fps {caps.native_fps}, нативный).\n"
            f"Дальше: /swapbatch_animate_yes (покажу стоимость) → /swapbatch_animate_go."
        ))
```

Add `run_animate_batch_phase` (the paid parallel runner; bills only successful videos):

```python
    async def run_animate_batch_phase(
        self,
        chat_id: int,
        animate_fn,
        progress_cb: "Callable[[str, dict[str, Any]], None] | None" = None,
        *,
        user_id: int | None = None,
        username: str | None = None,
    ) -> HandlerReply:
        sess0 = self.orchestrator.get(chat_id)
        seconds = sess0.duration_sec if sess0 else 10
        resolution = sess0.resolution if sess0 else "720p"
        engine_mode = sess0.video_engine if sess0 else "spicy"
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
        from app.services.block_m2_video.engines.capabilities import caps_for
        amount = succeeded * caps_for(engine_mode).cost_for(seconds, resolution)
        if user_id is not None and amount > 0:
            try:
                _cost.record_cost(user_id, username, amount)
            except Exception as exc:  # noqa: BLE001
                logger.warning("cost: record_cost failed: %s", exc)
        self.orchestrator.prune(chat_id)
        return HandlerReply(text="\n".join(lines), videos=videos)
```

(`HandlerReply`, `STATE_SWAP_DONE`, `Path`, `Any`, `Callable`, `OrchestratorError`, `translate_exception`, `_cost`, `logger` are already imported in this handler module — confirm at the top; if `_cost`/`record_cost` differs, match the swap phase's billing call.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swapbatch_animate_cost.py tests/test_swapbatch_handler.py -q`
Expected: PASS. If a pre-existing `test_swapbatch_handler.py` test asserted the OLD `handle_animate_yes`/post-swap behavior, STOP and report the exact assertion before changing it (the cost-gate is an intended behavior change).

- [ ] **Step 5: Commit**

```bash
git add app/handlers/face_swap_handler.py tests/test_swapbatch_animate_cost.py
git -c commit.gpgsign=false commit -m "feat(swapbatch): caps-based cost gate + set_prompt/set_quality + parallel run_animate_batch_phase"
```

---

### Task 9: Bot wiring — repoint animate to WaveSpeed, cost-gate, prompt, progress, flag

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py`

⚠️ This file holds the SWAP code too. Touch ONLY the animate path; do NOT alter the `command == "go"` swap branch.

- [ ] **Step 1: Route `animate_yes` to cost-gate (no run); add `animate_go`, `set_prompt`**

In `_swapbatch_dispatch`, add alongside the simple-reply commands (keep the existing `animate_enabled()` guard semantics):

```python
    if command == "animate_yes":
        if not animate_enabled():
            send(chat_id_s, "🎬 Видео-фаза временно отключена. /swapbatch_no — завершить.")
            return
        _swapbatch_apply_reply(chat_id_s, handler.handle_animate_yes(chat_id_int))
        return
    if command == "set_prompt":
        _swapbatch_apply_reply(chat_id_s, handler.handle_set_prompt(chat_id_int, arg_text))
        return
    if command == "set_quality":
        _swapbatch_apply_reply(chat_id_s, handler.handle_set_quality(chat_id_int, arg_text))
        return
```

And the long-running set includes `animate_go`:

```python
    if command in ("go", "animate_go"):
        if command == "animate_go" and not animate_enabled():
            send(chat_id_s, "🎬 Видео-фаза временно отключена. /swapbatch_no — завершить.")
            return
        _swapbatch_run_phase(chat_id_int, chat_id_s, command, handler)
        return
```

(`arg_text` = the message text after the command keyword; reuse the file's existing arg-extraction. Leave the deferred custom-prompt commands gated by `animate_enabled()` as today — they must NOT reach the managed path.)

- [ ] **Step 2: Implement the `animate_go` branch in `_swapbatch_run_phase` (WaveSpeed, parallel)**

In `_swapbatch_run_phase`, the `_run()` body currently does `if command == "go": ... else: <RunPod animate>`. Replace the `else:` (RunPod) branch with an `elif command == "animate_go":` WaveSpeed branch, and a final `else:` rejecting deferred custom commands:

```python
            elif command == "animate_go":
                from app.services.block_m2_video.engines.router import EngineRouter
                from app.services.block_m2_video.engines.engine_protocol import (
                    VideoRequest, new_generation_id,
                )
                from app.services.block_m2_video.batch_animate import animate_batch

                _h2, _orch_q = _swapbatch_get_handler()
                _sess_q = _orch_q.get(chat_id_int) if _orch_q else None
                _seconds = int(getattr(_sess_q, "duration_sec", 10) or 10)
                _resolution = str(getattr(_sess_q, "resolution", "720p") or "720p")
                _engine_mode = str(getattr(_sess_q, "video_engine", "spicy") or "spicy")
                _motion = str(getattr(_sess_q, "motion_prompt", "") or "")
                _DEFAULT_MOTION = ("gentle natural body movement, subtle motion, "
                                   "soft cinematic lighting, photorealistic")
                _prompt = _motion or _DEFAULT_MOTION
                _concurrency = int(os.getenv("SWAPBATCH_ANIMATE_CONCURRENCY", "2"))

                router = EngineRouter()
                engine = _aio.run(router.select(_engine_mode))  # spicy -> WaveSpeed

                async def _animate_fn(photos, cancel_check):
                    reqs = [
                        VideoRequest(
                            persona_id=f"swapbatch_{chat_id_int}", persona_name="swapbatch",
                            input_image_path=ph, prompt=_prompt,
                            seconds=_seconds, resolution=_resolution,
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
                        user_id=chat_id_int, username=_USERNAME_BY_CHAT.get(chat_id_s),
                    )
                )
            else:  # confirm / apply_partial / apply_first (custom-prompt) — deferred
                send(chat_id_s, "🎬 Кастомные промпты для видео пока недоступны на новом движке. Используй /swapbatch_animate_yes.")
                reply = None
```

(Match `_swapbatch_get_handler`, `_aio`, `_progress`, `_USERNAME_BY_CHAT` to the names already in this file. If `animate_enabled` isn't imported at the dispatch site, add `from app.services.block_m2_face_swap.cost_estimator import animate_enabled`.)

- [ ] **Step 3: Add the `animate_progress` branch to `_progress`**

In the `_progress` callback inside `_swapbatch_run_phase`, next to the `swap_progress` branch:

```python
        elif stage == "animate_progress":
            completed = payload.get("completed", 0)
            total = payload.get("total", 0)
            if total and (completed == total or completed % 10 == 0):
                send(chat_id_s, f"🎬 Animate {completed}/{total}…")
```

- [ ] **Step 4: Verify import + swap path intact**

Run: `./.venv/Scripts/python.exe -c "import tools.jarvis_smart_telegram_control"`
Expected: clean import.
Run: `./.venv/Scripts/python.exe -m pytest tests/test_swapbatch_handler.py tests/test_swapbatch_accumulation.py tests/test_lucataco_engine.py -q`
Expected: PASS (swap path untouched). If any swap test breaks, STOP and report.

- [ ] **Step 5: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py
git -c commit.gpgsign=false commit -m "feat(swapbatch): animate via WaveSpeed (mode=spicy), cost-gate /swapbatch_animate_go, set_prompt/quality, progress"
```

---

### Task 10: PHASE A ROLLOUT GATE — small live test (STOP for the user)

**This is the spec's required first checkpoint. Do NOT scale to 100, and do NOT start Phase B, until the user confirms this passes.**

- [ ] **Step 1: Full Phase-A mock suite green**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_video_capabilities.py tests/test_wavespeed_spicy_engine.py tests/test_video_batch_animate.py tests/test_swapbatch_animate_cost.py tests/test_swapbatch_orchestrator.py tests/test_swapbatch_handler.py -q`
Expected: all PASS.

- [ ] **Step 2: Enable flag + confirm key/concurrency**

Ensure `C:\jarvis\.env` has `WAVESPEED_API_KEY=…`, `SWAPBATCH_ANIMATE_ENABLED=1`, optional `SWAPBATCH_ANIMATE_CONCURRENCY=2`.
Run: `./.venv/Scripts/python.exe -c "import os; from dotenv import load_dotenv; load_dotenv(r'C:\\jarvis\\.env'); print('key:', bool(os.getenv('WAVESPEED_API_KEY'))); print('flag:', os.getenv('SWAPBATCH_ANIMATE_ENABLED'))"`
Expected: `key: True`, flag `1`.

- [ ] **Step 3: Restart the bot from disk**

Kill the bot python(s) so the guardian respawns fresh (see jarvis-bot-guardian-task memory). Confirm a fresh PID + heartbeat and that `from app.services.block_m2_video.engines.capabilities import caps_for` imports in the venv.

- [ ] **Step 4: STOP — hand to the user for the live Telegram test (2–3 photos)**

The user runs, via the bot, a SMALL batch:
1. `/swapbatch_source` → face; `/swapbatch_batch` → 2–3 targets → `/swapbatch_go` (swap).
2. `/swapbatch_set_prompt <движение>` and/or `/swapbatch_set_quality duration=10 resolution=720p`.
3. `/swapbatch_animate_yes` → expect cost line (~$2–3) + engine + prompt + `/swapbatch_animate_go`.
4. `/swapbatch_animate_go` → expect progress, then 2–3 **uncensored** videos, honest tally.

**Do NOT proceed until the user confirms:** videos arrived, uncensored, cost line correct, prompt/quality/length honored, 429 not losing clips. Only after that does the user authorize a larger run; and only after Phase A is signed off do you start Phase B.

---

# PHASE B — Seedance + engine choice + standalone /animate (only after Phase A signed off)

---

### Task 11: ReplicateSeedanceEngine

**Files:**
- Create: `app/services/block_m2_video/engines/replicate_seedance_engine.py`
- Test: `tests/test_replicate_seedance_engine.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_replicate_seedance_engine.py`:

```python
from pathlib import Path

import httpx
import pytest

from app.services.block_m2_video.engines.engine_protocol import VideoRequest
from app.services.block_m2_video.engines.errors import (
    TransientVideoError, TerminalVideoError,
)
from app.services.block_m2_video.engines.replicate_seedance_engine import (
    ReplicateSeedanceEngine, ReplicateSeedanceTransientError, ReplicateSeedanceEngineError,
)

_UA_MARK = "Mozilla/"


def _img(tmp_path) -> Path:
    p = tmp_path / "src.jpg"; p.write_bytes(b"\xff\xd8\xff\xe0FAKE"); return p


def _engine(handler, dl=None, **kw):
    return ReplicateSeedanceEngine(
        api_token="t",
        transport=httpx.MockTransport(handler),
        download_transport=httpx.MockTransport(dl or (lambda r: httpx.Response(200, content=b"MP4"))),
        backoff_base=0.0, **kw,
    )


def test_errors_subclass_shared_bases():
    assert issubclass(ReplicateSeedanceTransientError, TransientVideoError)
    assert issubclass(ReplicateSeedanceEngineError, TerminalVideoError)


@pytest.mark.asyncio
async def test_generate_success_sends_browser_ua_and_data_uri(tmp_path):
    seen = {}
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            seen["ua"] = req.headers.get("user-agent", "")
            import json
            seen["image"] = json.loads(req.content)["input"]["image"][:30]
            return httpx.Response(201, json={"id": "p1", "status": "starting",
                "urls": {"get": "https://api.replicate.com/v1/predictions/p1"}})
        return httpx.Response(200, json={"status": "succeeded", "output": "https://cdn/x.mp4"})

    eng = _engine(handler)
    res = await eng.generate(VideoRequest(
        persona_id="swapbatch_1", persona_name="b",
        input_image_path=_img(tmp_path), prompt="move", seconds=10, resolution="1080p",
    ))
    assert res.output_path.exists()
    assert res.engine == "replicate_seedance"
    assert res.cost_usd == pytest.approx(0.55)
    assert _UA_MARK in seen["ua"]                  # browser UA (tarpit defense)
    assert seen["image"].startswith("data:image/")  # base64 data-URI


@pytest.mark.asyncio
async def test_generate_429_exhausted_transient(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(429, json={"detail": "throttled"})
        return httpx.Response(200, json={"status": "succeeded", "output": "x"})
    eng = _engine(handler, max_retries=2)
    with pytest.raises(ReplicateSeedanceTransientError):
        await eng.generate(VideoRequest(
            persona_id="p", persona_name="b",
            input_image_path=_img(tmp_path), prompt="m", seconds=5))


@pytest.mark.asyncio
async def test_generate_failed_is_terminal(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(201, json={"id": "p", "status": "starting",
                "urls": {"get": "https://api.replicate.com/v1/predictions/p"}})
        return httpx.Response(200, json={"status": "failed", "error": "boom"})
    eng = _engine(handler)
    with pytest.raises(ReplicateSeedanceEngineError):
        await eng.generate(VideoRequest(
            persona_id="p", persona_name="b",
            input_image_path=_img(tmp_path), prompt="m", seconds=5))


@pytest.mark.asyncio
async def test_submit_4xx_terminal_not_retried(tmp_path):
    posts = {"n": 0}
    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            posts["n"] += 1
            return httpx.Response(422, json={"detail": "bad"})
        return httpx.Response(200, json={"status": "succeeded", "output": "x"})
    eng = _engine(handler, max_retries=5)
    with pytest.raises(ReplicateSeedanceEngineError):
        await eng.generate(VideoRequest(
            persona_id="p", persona_name="b",
            input_image_path=_img(tmp_path), prompt="m", seconds=5))
    assert posts["n"] == 1   # MONEY: 4xx never retried
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_replicate_seedance_engine.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Write the engine**

Create `app/services/block_m2_video/engines/replicate_seedance_engine.py`:

```python
# -*- coding: utf-8 -*-
"""Replicate bytedance/seedance-1-pro-fast image-to-video engine (managed, SFW).

Twin of WaveSpeedSpicyEngine. Uses the Replicate predictions REST API directly
via httpx with a BROWSER User-Agent + base64 data-URI image (bare SDK/file-handle
gets Cloudflare-tarpitted — verified recon). Transient (429/network, no prediction
created -> retry/sweep) vs Terminal (completed-but-failed / 4xx -> billable, never
retry). Cost/snap from SEEDANCE_CAPS. Never logs the token.
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

import httpx

from .capabilities import SEEDANCE_CAPS
from .engine_protocol import VideoRequest, VideoResult, new_generation_id
from .errors import TerminalVideoError, TransientVideoError

logger = logging.getLogger(__name__)

_MODEL = "bytedance/seedance-1-pro-fast"
_CREATE = f"https://api.replicate.com/v1/models/{_MODEL}/predictions"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class ReplicateSeedanceEngineError(TerminalVideoError):
    """Terminal: prediction failed/canceled, or a 4xx."""


class ReplicateSeedanceTransientError(TransientVideoError):
    """429/network exhausted; NO prediction created -> safe to retry."""


class ReplicateSeedanceEngine:
    engine_name = "replicate_seedance"

    def __init__(
        self,
        api_token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        download_transport: httpx.BaseTransport | None = None,
        max_retries: int = 8,
        backoff_base: float = 1.0,
    ) -> None:
        self._token = (
            api_token or os.getenv("REPLICATE_API_TOKEN") or os.getenv("REPLICATE_API_KEY") or ""
        ).strip()
        if not self._token:
            raise ReplicateSeedanceEngineError("REPLICATE_API_TOKEN not set")
        self._headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "User-Agent": _UA,                # tarpit defense
        }
        self._transport = transport
        self._dl_transport = download_transport
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    async def is_available(self) -> bool:
        return bool(self._token)

    async def generate(self, request: VideoRequest) -> VideoResult:
        start = time.monotonic()
        gen_id = request.generation_id or new_generation_id()
        seconds = SEEDANCE_CAPS.snap_duration(request.seconds)
        resolution = SEEDANCE_CAPS.snap_resolution(request.resolution)
        if not request.input_image_path.exists():
            raise ReplicateSeedanceEngineError(f"input image not found: {request.input_image_path}")

        payload = {"input": {
            "image": self._data_uri(request.input_image_path),
            "prompt": request.prompt,
            "duration": seconds,
            "resolution": resolution,
        }}
        if request.seed is not None:
            payload["input"]["seed"] = request.seed

        poll_url = await self._submit_with_retry(payload)
        video_url = await self._poll(poll_url)
        out_path = await self._download(video_url, request.persona_id, gen_id)

        return VideoResult(
            generation_id=gen_id, persona_id=request.persona_id, output_path=out_path,
            engine=self.engine_name, model=_MODEL,
            seed=request.seed if request.seed is not None else -1,
            cost_usd=SEEDANCE_CAPS.cost_for(seconds, resolution),
            duration_sec=time.monotonic() - start, timestamp=datetime.now(timezone.utc),
            prompt=request.prompt, seconds=seconds,
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
                    r = await c.post(_CREATE, headers=self._headers, json=payload)
                if r.status_code == 429:
                    raise httpx.HTTPStatusError("429", request=r.request, response=r)
                if 400 <= r.status_code < 500:
                    raise ReplicateSeedanceEngineError(f"Replicate rejected ({r.status_code}): {r.text[:300]}")
                r.raise_for_status()
                d = r.json()
                return (d.get("urls") or {}).get("get") or f"https://api.replicate.com/v1/predictions/{d.get('id')}"
            except ReplicateSeedanceEngineError:
                raise
            except httpx.HTTPStatusError as exc:
                last = exc
                code = exc.response.status_code if exc.response is not None else 0
                logger.warning("Seedance submit %d/%d failed: %s", attempt, self._max_retries, exc)
                if attempt < self._max_retries:
                    wait = (10.0 + 2 ** attempt + random.uniform(0, 5)) if code == 429 else 2 ** attempt
                    await asyncio.sleep(wait * self._backoff_base)
            except Exception as exc:  # network/transport — retryable, not billed
                last = exc
                logger.warning("Seedance submit %d/%d error: %s", attempt, self._max_retries, exc)
                if attempt < self._max_retries:
                    await asyncio.sleep((2 ** attempt) * self._backoff_base)
        raise ReplicateSeedanceTransientError(f"Seedance submit failed after {self._max_retries} retries: {last}")

    async def _poll(self, poll_url: str, max_wait: int = 600) -> str:
        waited, interval = 0, 5
        async with httpx.AsyncClient(timeout=60, transport=self._transport) as c:
            while waited < max_wait:
                r = await c.get(poll_url, headers=self._headers)
                r.raise_for_status()
                d = r.json()
                status = d.get("status")
                if status == "succeeded":
                    out = d.get("output")
                    url = out if isinstance(out, str) else (out[0] if isinstance(out, list) and out else None)
                    if not url:
                        raise ReplicateSeedanceEngineError(f"succeeded but no output: {d}")
                    return url
                if status in ("failed", "canceled"):
                    raise ReplicateSeedanceEngineError(f"prediction {status}: {d.get('error')}")
                await asyncio.sleep(interval * self._backoff_base)
                waited += interval
        raise ReplicateSeedanceEngineError(f"poll timed out after {max_wait}s")

    async def _download(self, url: str, persona_id: str, gen_id: str) -> Path:
        out_dir = Path("state/personas/videos") / persona_id / gen_id
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / "output.mp4"
        last: Exception | None = None
        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(timeout=300, transport=self._dl_transport) as c:
                    r = await c.get(url, headers={"User-Agent": _UA})
                    r.raise_for_status()
                    dest.write_bytes(r.content)
                return dest
            except Exception as exc:  # noqa: BLE001
                last = exc
                await asyncio.sleep((2 ** attempt) * self._backoff_base)
        raise ReplicateSeedanceEngineError(f"download failed: {last}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_replicate_seedance_engine.py -v`
Expected: PASS (UA + data-URI sent, success, 429→transient, failed→terminal, 4xx submit-count==1).

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_video/engines/replicate_seedance_engine.py tests/test_replicate_seedance_engine.py
git -c commit.gpgsign=false commit -m "feat(video): ReplicateSeedanceEngine (seedance-1-pro-fast, UA+data-URI, transient/terminal, caps cost)"
```

---

### Task 12: Router seedance branch

**Files:**
- Modify: `app/services/block_m2_video/engines/router.py`
- Test: `tests/test_video_batch_animate.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_video_batch_animate.py`:

```python
@pytest.mark.asyncio
async def test_router_seedance_returns_injected():
    class _Fake:
        engine_name = "fake_seedance"
        async def is_available(self): return True
        async def generate(self, request): ...
    router = EngineRouter(seedance=_Fake())
    eng = await router.select("seedance")
    assert eng.engine_name == "fake_seedance"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_video_batch_animate.py::test_router_seedance_returns_injected -v`
Expected: FAIL (`unexpected keyword 'seedance'`).

- [ ] **Step 3: Add the seedance branch**

In `router.py` `__init__`, add `seedance: VideoGenerator | None = None,` and `self._seedance = seedance`. Add getter:

```python
    def _get_seedance(self) -> VideoGenerator:
        if self._seedance is None:
            from .replicate_seedance_engine import ReplicateSeedanceEngine
            self._seedance = ReplicateSeedanceEngine()
        return self._seedance
```

In `select`, after the `spicy` branch:

```python
        if mode == "seedance":
            logger.info("EngineRouter: mode=seedance -> ReplicateSeedanceEngine")
            return self._get_seedance()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_video_batch_animate.py -q`
Expected: PASS (all router + runner tests).

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_video/engines/router.py tests/test_video_batch_animate.py
git -c commit.gpgsign=false commit -m "feat(video): router mode=seedance -> Seedance"
```

---

### Task 13: Engine choice — post-swap 3-way menu + defaults

**Files:**
- Modify: `app/services/block_m2_face_swap/batch_orchestrator.py` (add `set_engine_and_defaults`)
- Modify: `app/handlers/face_swap_handler.py` (engine-pick handler + menu text)
- Modify: `tools/jarvis_smart_telegram_control.py` (route engine-pick commands)
- Test: `tests/test_swapbatch_animate_cost.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_swapbatch_animate_cost.py`:

```python
def test_pick_engine_sets_engine_and_defaults(tmp_path):
    from app.handlers.face_swap_handler import FaceSwapHandler
    orch = _orch(tmp_path); chat = 9
    _seed_swapped(orch, chat, tmp_path)
    h = FaceSwapHandler(orchestrator=orch)
    reply = h.handle_pick_engine(chat, "seedance")
    sess = orch.get(chat)
    assert sess.video_engine == "seedance"
    assert sess.resolution == "1080p"     # seedance default
    assert sess.duration_sec == 5          # seedance default
    assert "$" in reply.text               # shows cost gate next
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swapbatch_animate_cost.py::test_pick_engine_sets_engine_and_defaults -v`
Expected: FAIL (`handle_pick_engine` missing).

- [ ] **Step 3: Add orchestrator helper + handler**

In `batch_orchestrator.py`, add to `BatchOrchestrator`:

```python
    def set_engine_and_defaults(self, chat_id: int, engine_mode: str) -> None:
        """Set the session's video engine and reset duration/resolution to that
        engine's defaults (so the UI never carries an unsupported value over)."""
        from app.services.block_m2_video.engines.capabilities import caps_for
        caps = caps_for(engine_mode)
        with self._lock:
            sess = self._sessions.get(chat_id)
            if sess is None:
                raise OrchestratorError("Нет активного батча.")
            sess.video_engine = engine_mode
            sess.duration_sec = caps.default_duration
            sess.resolution = caps.default_resolution
            self._touch(sess)
            self._persist(sess)
```

In `face_swap_handler.py`, add:

```python
    def handle_pick_engine(self, chat_id: int, engine_mode: str) -> HandlerReply:
        if engine_mode not in ("spicy", "seedance"):
            return HandlerReply(text="⚠️ Неизвестный движок.")
        try:
            self.orchestrator.set_engine_and_defaults(chat_id, engine_mode)
        except OrchestratorError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        return self.handle_animate_yes(chat_id)   # show cost gate for the chosen engine
```

Update the post-swap menu text (wherever the SWAP_DONE summary is produced) to offer the 3 options:

```
🎬 /swapbatch_animate_wavespeed — WaveSpeed (без цензуры)
🎬 /swapbatch_animate_seedance — Seedance (дёшево, 10с/1080p)
📷 /swapbatch_no — без анимации
```

- [ ] **Step 4: Route the engine-pick commands**

In `_swapbatch_dispatch`, add:

```python
    if command == "animate_wavespeed":
        if not animate_enabled():
            send(chat_id_s, "🎬 Видео-фаза временно отключена. /swapbatch_no — завершить.")
            return
        _swapbatch_apply_reply(chat_id_s, handler.handle_pick_engine(chat_id_int, "spicy"))
        return
    if command == "animate_seedance":
        if not animate_enabled():
            send(chat_id_s, "🎬 Видео-фаза временно отключена. /swapbatch_no — завершить.")
            return
        _swapbatch_apply_reply(chat_id_s, handler.handle_pick_engine(chat_id_int, "seedance"))
        return
```

(Keep `animate_yes` as a back-compat alias → `handle_pick_engine(chat_id_int, "spicy")` or the existing `handle_animate_yes`. The `animate_go` branch already reads `sess.video_engine`, so it dispatches to Seedance automatically when chosen — no change needed there.)

- [ ] **Step 5: Run tests + import check**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swapbatch_animate_cost.py -q && ./.venv/Scripts/python.exe -c "import tools.jarvis_smart_telegram_control"`
Expected: PASS + clean import. If a swap/handler test breaks, STOP and report.

- [ ] **Step 6: Commit**

```bash
git add app/services/block_m2_face_swap/batch_orchestrator.py app/handlers/face_swap_handler.py tools/jarvis_smart_telegram_control.py tests/test_swapbatch_animate_cost.py
git -c commit.gpgsign=false commit -m "feat(swapbatch): post-swap 3-way engine menu (WaveSpeed/Seedance/none) + per-engine defaults"
```

---

### Task 14: Standalone `/animate` (one + mini-batch ≤20)

**Files:**
- Create: `app/services/block_m2_face_swap/animate_session.py`
- Modify: `app/handlers/face_swap_handler.py` (animate-standalone handlers)
- Modify: `tools/jarvis_smart_telegram_control.py` (`/animate` wiring)
- Test: `tests/test_animate_standalone.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_animate_standalone.py`:

```python
from pathlib import Path

import pytest
from PIL import Image

from app.services.block_m2_face_swap.animate_session import AnimateStore, ANIMATE_CAP


def _jpg(p: Path) -> Path:
    Image.new("RGB", (16, 16), (4, 5, 6)).save(p, "JPEG"); return p


def test_store_accumulates_up_to_cap(tmp_path):
    store = AnimateStore()
    chat = 1
    store.begin(chat)
    for i in range(ANIMATE_CAP + 5):
        store.add_photo(chat, _jpg(tmp_path / f"p{i}.jpg"))
    sess = store.get(chat)
    assert len(sess.photos) == ANIMATE_CAP        # capped, no overflow


def test_store_sets_engine_and_quality(tmp_path):
    store = AnimateStore()
    chat = 2
    store.begin(chat)
    store.add_photo(chat, _jpg(tmp_path / "a.jpg"))
    store.set_engine(chat, "seedance")
    sess = store.get(chat)
    assert sess.video_engine == "seedance"
    assert sess.resolution == "1080p"             # seedance default
    assert sess.duration_sec == 5


@pytest.mark.asyncio
async def test_run_animate_standalone_records_videos(tmp_path):
    from app.handlers.face_swap_handler import FaceSwapHandler
    store = AnimateStore(); chat = 3
    store.begin(chat); store.add_photo(chat, _jpg(tmp_path / "a.jpg"))
    store.set_engine(chat, "spicy")
    h = FaceSwapHandler(animate_store=store)

    async def _animate_fn(photos, cancel_check):
        return [tmp_path / "v.mp4"]

    reply = await h.run_animate_standalone(chat, _animate_fn, user_id=3)
    assert reply.videos == [Path(tmp_path / "v.mp4")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_animate_standalone.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Write the store**

Create `app/services/block_m2_face_swap/animate_session.py`:

```python
# -*- coding: utf-8 -*-
"""Lightweight standalone /animate session — independent of swap.

Accumulates up to ANIMATE_CAP photos, an engine choice, and quality, then feeds
the SAME animate_batch runner + cost gate + delivery as the post-swap flow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

ANIMATE_CAP = 20


@dataclass
class AnimateSession:
    chat_id: int
    photos: list = field(default_factory=list)
    video_engine: str = "spicy"
    duration_sec: int = 10
    resolution: str = "720p"
    motion_prompt: str = ""


class AnimateStore:
    def __init__(self) -> None:
        self._sessions: dict = {}

    def begin(self, chat_id: int) -> AnimateSession:
        sess = AnimateSession(chat_id=chat_id)
        self._sessions[chat_id] = sess
        return sess

    def get(self, chat_id: int):
        return self._sessions.get(chat_id)

    def add_photo(self, chat_id: int, path: Path) -> int:
        sess = self._sessions.setdefault(chat_id, AnimateSession(chat_id=chat_id))
        if len(sess.photos) < ANIMATE_CAP:
            sess.photos.append(Path(path))
        return len(sess.photos)

    def set_engine(self, chat_id: int, engine_mode: str) -> None:
        from app.services.block_m2_video.engines.capabilities import caps_for
        caps = caps_for(engine_mode)
        sess = self._sessions.setdefault(chat_id, AnimateSession(chat_id=chat_id))
        sess.video_engine = engine_mode
        sess.duration_sec = caps.default_duration
        sess.resolution = caps.default_resolution

    def set_prompt(self, chat_id: int, text: str) -> None:
        sess = self._sessions.setdefault(chat_id, AnimateSession(chat_id=chat_id))
        sess.motion_prompt = (text or "").strip()

    def clear(self, chat_id: int) -> None:
        self._sessions.pop(chat_id, None)
```

- [ ] **Step 4: Add the handler runner**

In `face_swap_handler.py`, ensure `FaceSwapHandler.__init__` accepts an optional `animate_store=None` and stores it as `self.animate_store`. Add:

```python
    async def run_animate_standalone(
        self, chat_id: int, animate_fn, *, user_id: int | None = None, username: str | None = None,
    ) -> HandlerReply:
        sess = self.animate_store.get(chat_id) if self.animate_store else None
        if sess is None or not sess.photos:
            return HandlerReply(text="⚠️ Сначала пришли фото для /animate.")
        from app.services.block_m2_video.engines.capabilities import caps_for
        caps = caps_for(sess.video_engine)
        try:
            results = await animate_fn(list(sess.photos), lambda: False)
        except Exception as exc:  # noqa: BLE001
            return HandlerReply(text=f"❌ Ошибка animate: {translate_exception(exc)}")
        videos = [Path(r) for r in results if r is not None]
        failed = len(sess.photos) - len(videos)
        amount = len(videos) * caps.cost_for(sess.duration_sec, sess.resolution)
        if user_id is not None and amount > 0:
            try:
                _cost.record_cost(user_id, username, amount)
            except Exception as exc:  # noqa: BLE001
                logger.warning("cost: record_cost failed: %s", exc)
        self.animate_store.clear(chat_id)
        lines = [f"🎬 Animate: {len(videos)} видео"]
        if failed:
            lines.append(f"  ⚠️ {failed} не удалось")
        return HandlerReply(text="\n".join(lines), videos=videos)
```

Also add a cost-gate reply `handle_animate_standalone_yes(chat_id)` mirroring `handle_animate_yes` but reading from the `AnimateSession` (count = `len(sess.photos)`); it shows the estimate + `/animate_go`. (Reuse `animate_cost_estimate` with `swapped_count=len(sess.photos)`.)

- [ ] **Step 5: Wire `/animate` in the bot**

Add an `/animate` command group in `tools/jarvis_smart_telegram_control.py` (separate from `/swapbatch`), gated by `animate_enabled()`:
- `/animate` → `store.begin(chat)`; prompt the user to send 1..20 photos, then pick engine.
- photo messages while an animate session is open → `store.add_photo`.
- `/animate_wavespeed` | `/animate_seedance` → `store.set_engine(...)` → show cost gate (`handle_animate_standalone_yes`).
- `/animate_set_prompt <text>` → `store.set_prompt`.
- `/animate_set_quality ...` → `parse_animate_quality(args, engine_mode=sess.video_engine)` → update session.
- `/animate_go` → build `VideoRequest`s (same `_DEFAULT_MOTION` fallback) → `router.select(sess.video_engine)` → `animate_batch` → `handler.run_animate_standalone(...)`.

Use the SAME `animate_batch`, `EngineRouter`, cost-gate text, and `_send_local_video` delivery as the swap path. Keep the `/animate` photo-intake separate from the `/swapbatch` source/target intake so the two flows never cross-contaminate state.

- [ ] **Step 6: Run tests + import check**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_animate_standalone.py -q && ./.venv/Scripts/python.exe -c "import tools.jarvis_smart_telegram_control"`
Expected: PASS + clean import.

- [ ] **Step 7: Commit**

```bash
git add app/services/block_m2_face_swap/animate_session.py app/handlers/face_swap_handler.py tools/jarvis_smart_telegram_control.py tests/test_animate_standalone.py
git -c commit.gpgsign=false commit -m "feat(animate): standalone /animate (1..20 photos, engine+quality+prompt, shared runner/cost-gate)"
```

---

### Task 15: PHASE B ROLLOUT GATE — Seedance live test then scale (STOP for the user)

**Do NOT scale either engine to 100 until the user confirms a small test on THAT engine.**

- [ ] **Step 1: Full suite green**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_video_capabilities.py tests/test_wavespeed_spicy_engine.py tests/test_replicate_seedance_engine.py tests/test_video_batch_animate.py tests/test_swapbatch_animate_cost.py tests/test_animate_standalone.py tests/test_swapbatch_orchestrator.py tests/test_swapbatch_handler.py -q`
Expected: all PASS.

- [ ] **Step 2: Confirm `REPLICATE_API_TOKEN` + restart bot from disk**

Ensure `.env` has `REPLICATE_API_TOKEN`. Restart the bot (guardian respawn) and confirm `from app.services.block_m2_video.engines.replicate_seedance_engine import ReplicateSeedanceEngine` imports in the venv.

- [ ] **Step 3: STOP — hand to the user for the live Seedance test (2–3 photos)**

User runs via the bot:
1. Post-swap → `/swapbatch_animate_seedance` → cost line (~$0.5–1.7 for 2–3×10s/1080p) + `/swapbatch_animate_go`; OR
2. `/animate` → send 2–3 photos → `/animate_seedance` → `/animate_set_quality duration=10 resolution=1080p` → cost gate → `/animate_go`.
3. Expect: progress, 2–3 **1080p** videos (~24fps), warm start, honest tally, correct cost line.

**Do NOT proceed to scale until the user confirms** videos arrived, 1080p/10s honored, cost correct, 429 not losing clips, prompt honored. Then the user may run larger batches (cost gate shows ceiling each time). Record actual per-video latency / 429 rate / cost to refine `SEEDANCE_CAPS.pricing` and `SWAPBATCH_ANIMATE_CONCURRENCY`.

- [ ] **Step 4: Refine Seedance pricing from real billing**

After the first real Seedance runs, update `SEEDANCE_CAPS.pricing` in `capabilities.py` with the actual per-clip costs from Replicate billing; run `tests/test_video_capabilities.py` (adjust the asserted values to match) and commit.

```bash
git add app/services/block_m2_video/engines/capabilities.py tests/test_video_capabilities.py
git -c commit.gpgsign=false commit -m "chore(video): refine Seedance pricing table from real billing"
```

---

## Self-Review

**Spec coverage:**
- Two engines via `VideoGenerator` seam + router modes (`spicy`/`seedance`) → Tasks 3,4,11,12. ✅
- Per-engine capability descriptor drives UI/cost/validation → Task 2; used in 7,8,13,14. ✅
- VideoRequest resolution/negative_prompt → Task 1. ✅
- Prompt mandatory + default + custom (`/swapbatch_set_prompt`, `/animate` prompt), shared-batch now → Tasks 6 (field), 8 (`handle_set_prompt`), 14 (`/animate_set_prompt`); individual deferred (spec out-of-scope). ✅
- Resolution choice per-engine (WS 720/1080, Seedance 480/720/1080) → Tasks 2,7,8,13. ✅
- Length choice per-engine (WS 5/10/15, Seedance 5/10) → Tasks 2,7. ✅
- fps info-only (30/24) → Task 8 `handle_set_quality` text; never settable. ✅
- Engine choice menu (post-swap + /animate) → Tasks 13,14. ✅
- Cost-gate mandatory + ceilings ($225 WS / $55 Seedance) → Task 8 `handle_animate_yes` (shows cost, no run) + Task 9 `/swapbatch_animate_go` paid trigger; standalone Task 14. ✅
- Money invariant (terminal not retried, transient swept; submit/post-count==1) → Task 1 bases, Task 3/11 engine 4xx-not-retried tests, Task 5 runner `calls==1` test. ✅
- Concurrency=2 (env) PARALLEL + 429 backoff + sweep on BOTH engines → Task 3/11 backoff, Task 5 semaphore+sweep. ✅
- Per-video isolation + honest tally → Tasks 5,6,8,14. ✅
- Flag OFF default routes to selected managed engine, never RunPod → Task 9 guards; `animate_go` reads `video_engine`; custom commands deferred/guarded. ✅
- ROLLOUT small-first then 100 + STOP on live, per engine → Tasks 10,15. ✅
- Don't break swap tests silently → Tasks 6,8,9,13 each run existing suites + STOP instructions. ✅
- Phase A fully working/tested before Phase B → Task 10 gate is terminal for Phase A; Phase B starts at Task 11. ✅
- Delivery one-by-one via existing `_send_local_video` (HandlerReply.videos) → Tasks 8,14. ✅
- Standalone `/animate` ≤20 → Task 14 (`ANIMATE_CAP=20`). ✅

**Placeholder scan:** No TBD/TODO; full code in every code step. Seedance pricing intentionally APPROX with a dedicated refine step (Task 15.4). Task 14 step 5 (bot `/animate` group) is described as wiring against named, already-defined functions (router/animate_batch/run_animate_standalone) — matching the existing `/swapbatch` dispatch pattern in the same file. ✅

**Type consistency:** `EngineCapabilities.cost_for(seconds, resolution)` / `snap_duration` / `snap_resolution` / `gen_seconds` defined Task 2, used Tasks 3,8,11,13,14. `TransientVideoError`/`TerminalVideoError` defined Task 1, subclassed Tasks 3,11, caught Task 5. `animate_fn(photos, cancel_check) -> list[Path|None]` consistent across Tasks 6,8,9,14. `confirm_animate_batch`/`run_animate_batch_phase`/`run_animate_standalone`/`handle_pick_engine`/`set_engine_and_defaults`/`parse_animate_quality(args, engine_mode=)`/`animate_cost_estimate(...engine_mode=)` names consistent across tasks. `BatchSession` fields `video_engine`/`resolution`/`motion_prompt` defined Task 6, used Tasks 8,9,13. `animate_progress` emitted (Task 5) and consumed (Task 9). ✅

**Scope note:** Per-photo INDIVIDUAL prompts, Seedance audio/multimodal/`camera_fixed`/`shot_type`, RunPod, and the legacy `ReplicateEngine` are out of scope by spec — no task touches them on the managed path. ✅
