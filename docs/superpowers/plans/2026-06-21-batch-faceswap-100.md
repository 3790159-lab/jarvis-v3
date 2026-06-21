# Batch Face-Swap (100) on a Pluggable Swap Engine — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user send one source face + up to 100 target photos and receive 100 face-swapped results via the uncensored lucataco/Replicate path, behind a replaceable `SwapEngine` interface so a future Path-B engine swaps in without touching the plumbing.

**Architecture:** Reuse the existing `/swapbatch_*` orchestrator/handler/cost/intake/delivery (B-51 already fixed there). Formalize the already-present `swap_fn` callback seam into a `SwapEngine` protocol + factory selected by `SWAP_ENGINE` env (default `lucataco`). Add `LucatacoSwapEngine` that encodes local files as data-URIs (verified accepted — no hosting), runs them through lucataco with a concurrency semaphore + 429 backoff, and downloads results to local paths. Then scale intake (multi-album accumulation, cap 20→100) and delivery (album chunks of 10 + size-split zips).

**Tech Stack:** Python 3, `httpx` (async Replicate calls), `Pillow` 12.2 (down-scale), `requests` (Telegram upload), `pytest` + `pytest-asyncio`. Telegram lib: `pyTelegramBotAPI` 4.32 (sync polling, worker threads).

**Spec:** `docs/superpowers/specs/2026-06-21-batch-faceswap-100-design.md`

**Verified before planning:** lucataco accepts `data:image/...;base64,…` inputs and returns a real output URL; on no-face it returns `status=succeeded` with `output=None` (billable) — the engine MUST treat that as a per-target failure and never retry it.

---

## File Structure

**New files:**
- `app/services/block_m2_face_swap/engines/__init__.py` — package marker.
- `app/services/block_m2_face_swap/engines/base.py` — `SwapEngine` Protocol.
- `app/services/block_m2_face_swap/engines/factory.py` — `get_swap_engine()`, `get_swap_cost_per_photo()`, `get_swap_cold_start_usd()`.
- `app/services/block_m2_face_swap/engines/lucataco_engine.py` — `LucatacoSwapEngine` + cost constants.
- `app/services/block_m_common/lucataco_client.py` — async lucataco Replicate client.
- `app/services/block_m2_face_swap/result_delivery.py` — pure helpers: `chunk_photos`, `build_result_zips`.
- `tests/test_lucataco_client.py`, `tests/test_lucataco_engine.py`, `tests/test_swap_engine_factory.py`, `tests/test_result_delivery.py`, `tests/test_swapbatch_accumulation.py`.

**Modified files:**
- `app/services/block_m2_face_swap/cost_estimator.py` — `estimate()` gains `swap_usd_per_photo` / `cold_start_usd` overrides.
- `app/services/block_m2_face_swap/batch_orchestrator.py` — `MAX_TARGETS` 20→100; new `add_targets()`; cost via factory.
- `app/handlers/face_swap_handler.py` — `consume_targets_album` accumulates; `HandlerReply.documents` field; accumulation reply text.
- `tools/jarvis_smart_telegram_control.py` — `go` branch uses factory; album intercept gate allows accumulation; delivery sends zips + throttle; progress batched.

---

# PHASE 0 — Working small batch (3–5 photos) on lucataco

Rollout gate per spec: a correct small batch end-to-end **before** any 100-scale work. `MAX_TARGETS` stays 20 in this phase (≥5), single album, single media-group delivery — no accumulation/zip needed yet.

---

### Task 1: `SwapEngine` protocol + factory

**Files:**
- Create: `app/services/block_m2_face_swap/engines/__init__.py`
- Create: `app/services/block_m2_face_swap/engines/base.py`
- Create: `app/services/block_m2_face_swap/engines/factory.py`
- Test: `tests/test_swap_engine_factory.py`

- [ ] **Step 1: Create the package marker**

Create `app/services/block_m2_face_swap/engines/__init__.py`:

```python
# -*- coding: utf-8 -*-
"""Pluggable face-swap engines for Block M.2.5."""
```

- [ ] **Step 2: Write the protocol**

Create `app/services/block_m2_face_swap/engines/base.py`:

```python
# -*- coding: utf-8 -*-
"""SwapEngine — the one interface the batch pipeline depends on.

The orchestrator/handler call an engine ONLY through ``swap_batch``. Today's
impl is lucataco (Replicate); Path B (our ComfyUI graph) will be a second impl.
Switching engines = change ``SWAP_ENGINE`` env + add a class. Nothing else moves.
"""
from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable, Protocol, runtime_checkable

ProgressCb = Callable[[str, dict], None]
CancelCheck = Callable[[], bool]


@runtime_checkable
class SwapEngine(Protocol):
    name: str
    cost_per_swap_usd: float

    async def swap_batch(
        self,
        source: Path,
        targets: list[Path],
        *,
        progress_cb: ProgressCb | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> list[Path | None]:
        """Swap ``source``'s face into each target. Returns a list aligned to
        ``targets``: a local Path on success, ``None`` on per-target failure."""
        ...
```

- [ ] **Step 3: Write the failing factory test**

Create `tests/test_swap_engine_factory.py`:

```python
import importlib

import pytest


def _factory():
    mod = importlib.import_module(
        "app.services.block_m2_face_swap.engines.factory"
    )
    return importlib.reload(mod)


def test_default_is_lucataco(monkeypatch):
    monkeypatch.delenv("SWAP_ENGINE", raising=False)
    f = _factory()
    assert f.get_swap_cost_per_photo() == pytest.approx(0.005)
    assert f.get_swap_cold_start_usd() == pytest.approx(0.0)


def test_env_selects_runpod(monkeypatch):
    monkeypatch.setenv("SWAP_ENGINE", "runpod")
    f = _factory()
    assert f.get_swap_cost_per_photo() == pytest.approx(0.02)
    assert f.get_swap_cold_start_usd() == pytest.approx(0.05)


def test_unknown_engine_raises(monkeypatch):
    monkeypatch.setenv("SWAP_ENGINE", "bogus")
    f = _factory()
    with pytest.raises(ValueError):
        f.get_swap_engine()
```

- [ ] **Step 4: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swap_engine_factory.py -v`
Expected: FAIL (module `factory` not found).

- [ ] **Step 5: Write the factory**

Create `app/services/block_m2_face_swap/engines/factory.py`:

```python
# -*- coding: utf-8 -*-
"""Select the active SwapEngine from the SWAP_ENGINE env var.

Cost lookups (used by the cost estimator before any engine is built) map the
engine name to constants WITHOUT instantiating — so a token-less environment
can still compute an estimate.
"""
from __future__ import annotations

import os

from .base import SwapEngine
from .lucataco_engine import (
    COLD_START_USD as _LUCATACO_COLD_START,
    COST_PER_SWAP_USD as _LUCATACO_COST,
)

_DEFAULT = "lucataco"

# Cost-only metadata (no class construction). RunPod values mirror the legacy
# SWAPBATCH_* env defaults; lucataco is managed (no cold start).
_COST_PER_PHOTO = {"lucataco": _LUCATACO_COST, "runpod": 0.02}
_COLD_START = {"lucataco": _LUCATACO_COLD_START, "runpod": 0.05}


def _name() -> str:
    return (os.getenv("SWAP_ENGINE", _DEFAULT) or _DEFAULT).strip().lower()


def get_swap_cost_per_photo() -> float:
    return _COST_PER_PHOTO.get(_name(), 0.02)


def get_swap_cold_start_usd() -> float:
    return _COLD_START.get(_name(), 0.05)


def get_swap_engine() -> SwapEngine:
    name = _name()
    if name == "lucataco":
        from .lucataco_engine import LucatacoSwapEngine
        return LucatacoSwapEngine()
    if name == "runpod":
        # Frozen RunPod path — kept for rollback, NOT the default.
        from ..face_swap_engine import FaceSwapEngine
        return FaceSwapEngine()
    raise ValueError(f"Unknown SWAP_ENGINE={name!r} (use lucataco|runpod)")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swap_engine_factory.py -v`
Expected: PASS (3 passed). The factory imports `lucataco_engine` constants — create them in Task 3; if running this task standalone before Task 3, the import fails. **Execute Task 3 before re-running**, or temporarily inline the constants. Recommended order: do Task 2 and Task 3, then return and run this test.

- [ ] **Step 7: Commit**

```bash
git add app/services/block_m2_face_swap/engines/__init__.py app/services/block_m2_face_swap/engines/base.py app/services/block_m2_face_swap/engines/factory.py tests/test_swap_engine_factory.py
git commit -m "feat(swapbatch): SwapEngine protocol + engine factory"
```

---

### Task 2: Async lucataco client

**Files:**
- Create: `app/services/block_m_common/lucataco_client.py`
- Test: `tests/test_lucataco_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_lucataco_client.py`:

```python
import httpx
import pytest

from app.services.block_m_common.faceswap_client import PredictionFailed
from app.services.block_m_common.lucataco_client import LucatacoClient


def _client(handler):
    transport = httpx.MockTransport(handler)
    return LucatacoClient(api_token="t", transport=transport)


@pytest.mark.asyncio
async def test_succeeded_returns_output_url():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"id": "p1"})
        return httpx.Response(
            200, json={"status": "succeeded", "output": "https://x/y.jpg"}
        )

    out = await _client(handler).swap("data:image/jpeg;base64,A", "data:image/jpeg;base64,B")
    assert out == "https://x/y.jpg"


@pytest.mark.asyncio
async def test_succeeded_none_output_returns_none():
    # lucataco's no-face outcome: succeeded + output=None (billable, no retry).
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(201, json={"id": "p2"})
        return httpx.Response(200, json={"status": "succeeded", "output": None})

    out = await _client(handler).swap("data:image/jpeg;base64,A", "data:image/jpeg;base64,B")
    assert out is None


@pytest.mark.asyncio
async def test_failed_raises_prediction_failed_no_retry():
    calls = {"post": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            calls["post"] += 1
            return httpx.Response(201, json={"id": "p3"})
        return httpx.Response(200, json={"status": "failed", "error": "boom"})

    with pytest.raises(PredictionFailed):
        await _client(handler).swap("data:image/jpeg;base64,A", "data:image/jpeg;base64,B")
    assert calls["post"] == 1  # never retried
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_lucataco_client.py -v`
Expected: FAIL (module `lucataco_client` not found).

- [ ] **Step 3: Write the client**

Create `app/services/block_m_common/lucataco_client.py`:

```python
# -*- coding: utf-8 -*-
"""Async Replicate client for lucataco/faceswap (bare inswapper, uncensored).

Mirrors faceswap_client's submit/poll/backoff. Two lucataco-specific rules:
  * inputs are passed as data-URIs (verified accepted — no hosting needed);
  * a no-face run returns status=succeeded with output=None (BILLABLE) — we
    surface that as ``None`` (a per-target failure), never a retry.
httpx is unaffected by Replicate's Cloudflare urllib-UA ban, so no UA needed.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Any

import httpx

from app.services.block_m_common.faceswap_client import PredictionFailed

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.replicate.com/v1"
_LUCATACO_VERSION = "9a4298548422074c3f57258c5d544497314ae4112df80d116f0d2109e843d20d"


class LucatacoClient:
    """One swap per ``swap()`` call. Construct once, reuse across a batch."""

    def __init__(
        self,
        api_token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token = (api_token or os.getenv("REPLICATE_API_TOKEN", "")).strip()
        if not self._token:
            raise RuntimeError("REPLICATE_API_TOKEN not set")
        self._headers = {
            "Authorization": f"Token {self._token}",
            "Content-Type": "application/json",
        }
        self._transport = transport  # tests inject httpx.MockTransport

    async def swap(self, swap_image: str, target_image: str) -> str | None:
        """Run one swap. ``swap_image``/``target_image`` are data-URIs or URLs.

        Returns the output URL, or ``None`` when lucataco found no face
        (succeeded+output=None). Raises PredictionFailed on failed/canceled.
        """
        payload = {
            "version": _LUCATACO_VERSION,
            "input": {"swap_image": swap_image, "target_image": target_image},
        }
        return await self._run_prediction(payload)

    async def _run_prediction(self, payload: dict, max_retries: int = 5) -> Any:
        submit_url = f"{_BASE_URL}/predictions"
        last_err: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                pred_id = await self._submit(submit_url, payload)
                return await self._poll(pred_id)
            except PredictionFailed:
                raise  # billable, deterministic — never retry
            except httpx.HTTPStatusError as exc:
                last_err = exc
                code = exc.response.status_code
                if 400 <= code < 500 and code != 429:
                    raise RuntimeError(
                        f"Replicate rejected lucataco ({code}): "
                        f"{exc.response.text[:300]}"
                    ) from exc
                if attempt < max_retries:
                    if code == 429:
                        retry_after = exc.response.headers.get("Retry-After")
                        wait = (
                            float(retry_after) + random.uniform(0, 2)
                            if retry_after
                            else 10.0 + (2 ** attempt) + random.uniform(0, 5)
                        )
                    else:
                        wait = 2 ** attempt
                    await asyncio.sleep(wait)
            except Exception as exc:  # network/transport — retryable, not billed
                last_err = exc
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)
        raise RuntimeError(f"lucataco failed after {max_retries} retries: {last_err}")

    async def _submit(self, url: str, payload: dict) -> str:
        async with httpx.AsyncClient(timeout=60.0, transport=self._transport) as c:
            resp = await c.post(url, json=payload, headers=self._headers)
            resp.raise_for_status()
            pred_id = resp.json().get("id")
            if not pred_id:
                raise RuntimeError(f"No prediction id in response: {resp.json()}")
            return pred_id

    async def _poll(self, pred_id: str, max_wait: int = 300) -> Any:
        poll_url = f"{_BASE_URL}/predictions/{pred_id}"
        waited, interval = 0, 3
        async with httpx.AsyncClient(timeout=30.0, transport=self._transport) as c:
            while waited < max_wait:
                resp = await c.get(poll_url, headers=self._headers)
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status")
                if status == "succeeded":
                    return data.get("output")  # may be None (no-face)
                if status in ("failed", "canceled"):
                    raise PredictionFailed(
                        f"lucataco {pred_id} {status}: {data.get('error')}"
                    )
                await asyncio.sleep(interval)
                waited += interval
        raise TimeoutError(f"lucataco {pred_id} timed out after {max_wait}s")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_lucataco_client.py -v`
Expected: PASS (3 passed). If `pytest-asyncio` errors on `async def` tests, ensure `asyncio_mode = auto` is set (check `pytest.ini`/`pyproject.toml`); if not, add `@pytest.mark.asyncio` is already present.

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m_common/lucataco_client.py tests/test_lucataco_client.py
git commit -m "feat(swapbatch): async lucataco client (data-URI, no-retry on billable)"
```

---

### Task 3: `LucatacoSwapEngine`

**Files:**
- Create: `app/services/block_m2_face_swap/engines/lucataco_engine.py`
- Test: `tests/test_lucataco_engine.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_lucataco_engine.py`:

```python
from pathlib import Path

import httpx
import pytest
from PIL import Image

from app.services.block_m2_face_swap.engines.lucataco_engine import (
    LucatacoSwapEngine,
)


def _make_jpg(path: Path, size=(64, 64)) -> Path:
    Image.new("RGB", size, (123, 50, 200)).save(path, "JPEG")
    return path


@pytest.mark.asyncio
async def test_swap_batch_aligns_results_and_marks_no_face(tmp_path):
    src = _make_jpg(tmp_path / "source.jpg")
    t0 = _make_jpg(tmp_path / "0.jpg")
    t1 = _make_jpg(tmp_path / "1.jpg")

    # MockTransport: target "0" succeeds with a URL, target "1" gets no-face.
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            state["n"] += 1
            return httpx.Response(201, json={"id": f"p{state['n']}"})
        # the prediction id tells us which target; odd->url, even->None
        pid = request.url.path.rsplit("/", 1)[-1]
        if pid == "p1":
            return httpx.Response(200, json={"status": "succeeded", "output": "https://img/ok.jpg"})
        return httpx.Response(200, json={"status": "succeeded", "output": None})

    # The result download also goes through the same transport.
    def dl_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"\xff\xd8\xff\xe0FAKEJPEG")

    engine = LucatacoSwapEngine(
        api_token="t",
        transport=httpx.MockTransport(handler),
        download_transport=httpx.MockTransport(dl_handler),
        concurrency=2,
    )
    out = await engine.swap_batch(src, [t0, t1])
    assert len(out) == 2
    assert out[0] is not None and out[0].exists()  # downloaded
    assert out[1] is None  # no-face → failure
    assert engine.cost_per_swap_usd == pytest.approx(0.005)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_lucataco_engine.py -v`
Expected: FAIL (module `lucataco_engine` not found).

- [ ] **Step 3: Write the engine**

Create `app/services/block_m2_face_swap/engines/lucataco_engine.py`:

```python
# -*- coding: utf-8 -*-
"""LucatacoSwapEngine — the active (today) SwapEngine implementation.

Encodes each local target as a down-scaled JPEG data-URI (verified accepted by
lucataco — no hosting), runs all targets concurrently behind a semaphore, and
downloads each result to ``source.parent/results/NNN.jpg``. Per-target failures
(no-face = output None, or PredictionFailed) map to ``None`` in the result list
so the orchestrator's "95/100" tally is accurate.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
from pathlib import Path

import httpx
from PIL import Image

from app.services.block_m_common.faceswap_client import PredictionFailed
from app.services.block_m_common.lucataco_client import LucatacoClient
from .base import CancelCheck, ProgressCb

logger = logging.getLogger(__name__)

# Cost metadata (single source of truth; factory imports these).
COST_PER_SWAP_USD = 0.005
COLD_START_USD = 0.0

_MAX_SIDE = 1600          # down-scale longest edge before base64 (bandwidth)
_JPEG_QUALITY = 90
_DEFAULT_CONCURRENCY = 5  # conservative vs unknown Replicate account limit


def _to_data_uri(path: Path) -> str:
    """Down-scale to <= _MAX_SIDE longest edge, re-encode JPEG, base64 data-URI."""
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((_MAX_SIDE, _MAX_SIDE))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=_JPEG_QUALITY)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64}"


class LucatacoSwapEngine:
    name = "lucataco"
    cost_per_swap_usd = COST_PER_SWAP_USD

    def __init__(
        self,
        api_token: str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        download_transport: httpx.BaseTransport | None = None,
        concurrency: int = _DEFAULT_CONCURRENCY,
    ) -> None:
        self._client = LucatacoClient(api_token, transport=transport)
        self._download_transport = download_transport
        self._sem = asyncio.Semaphore(concurrency)

    async def swap_batch(
        self,
        source: Path,
        targets: list[Path],
        *,
        progress_cb: ProgressCb | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> list[Path | None]:
        source_uri = _to_data_uri(source)
        results_dir = source.parent / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        total = len(targets)
        done = {"n": 0}
        done_lock = asyncio.Lock()

        async def _one(idx: int, target: Path) -> Path | None:
            if cancel_check and cancel_check():
                return None
            async with self._sem:
                if cancel_check and cancel_check():
                    return None
                try:
                    out_url = await self._client.swap(source_uri, _to_data_uri(target))
                except PredictionFailed as exc:
                    logger.warning("lucataco swap idx=%d failed: %s", idx, exc)
                    out_url = None
                except Exception as exc:  # noqa: BLE001
                    logger.warning("lucataco swap idx=%d error: %s", idx, exc)
                    out_url = None
                result: Path | None = None
                if out_url:
                    try:
                        result = await self._download(out_url, results_dir / f"{idx:03d}.jpg")
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("lucataco download idx=%d failed: %s", idx, exc)
                        result = None
            async with done_lock:
                done["n"] += 1
                completed = done["n"]
            if progress_cb:
                progress_cb(
                    "swap_progress",
                    {"completed": completed, "total": total,
                     "index": idx, "ok": result is not None},
                )
            return result

        return await asyncio.gather(*[_one(i, t) for i, t in enumerate(targets)])

    async def _download(self, url: str, dest: Path) -> Path:
        async with httpx.AsyncClient(timeout=60.0, transport=self._download_transport) as c:
            resp = await c.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        return dest
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_lucataco_engine.py tests/test_swap_engine_factory.py -v`
Expected: PASS (factory test now resolves its lucataco import).

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_face_swap/engines/lucataco_engine.py tests/test_lucataco_engine.py
git commit -m "feat(swapbatch): LucatacoSwapEngine (downscale, data-URI, semaphore, downloads)"
```

---

### Task 4: Cost estimate sourced from the engine

**Files:**
- Modify: `app/services/block_m2_face_swap/cost_estimator.py:52`
- Modify: `app/services/block_m2_face_swap/batch_orchestrator.py:279` (inside `submit_targets`)
- Test: extend `tests/test_swap_engine_factory.py` (already covers factory cost); add an estimate-override test below.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_swap_engine_factory.py`:

```python
def test_estimate_uses_override_rate(monkeypatch):
    from app.services.block_m2_face_swap.cost_estimator import estimate
    est = estimate(100, 0, swap_usd_per_photo=0.005, cold_start_usd=0.0)
    assert est.swap_usd == pytest.approx(0.5)  # 100 * 0.005 + 0 cold start
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swap_engine_factory.py::test_estimate_uses_override_rate -v`
Expected: FAIL (`estimate() got an unexpected keyword argument`).

- [ ] **Step 3: Add overrides to `estimate()`**

In `cost_estimator.py`, change the signature and the two env reads. Replace:

```python
def estimate(valid_count: int, skipped_count: int = 0) -> CostEstimate:
    """Compute swap + animate cost and wall-clock time for a batch."""
```

with:

```python
def estimate(
    valid_count: int,
    skipped_count: int = 0,
    *,
    swap_usd_per_photo: float | None = None,
    cold_start_usd: float | None = None,
) -> CostEstimate:
    """Compute swap + animate cost and wall-clock time for a batch.

    ``swap_usd_per_photo`` / ``cold_start_usd`` let the caller inject the active
    engine's real rates (lucataco ≈ $0.005/photo, no cold start) instead of the
    SWAPBATCH_* env defaults.
    """
```

Then change the two assignments inside the function from:

```python
    swap_per_photo = _envf("SWAPBATCH_SWAP_USD_PER_PHOTO", 0.02)
```
```python
    cold_start_usd = _envf("SWAPBATCH_COLD_START_USD", 0.05)
```

to:

```python
    swap_per_photo = (
        swap_usd_per_photo
        if swap_usd_per_photo is not None
        else _envf("SWAPBATCH_SWAP_USD_PER_PHOTO", 0.02)
    )
    cold_start_usd = (
        cold_start_usd
        if cold_start_usd is not None
        else _envf("SWAPBATCH_COLD_START_USD", 0.05)
    )
```

- [ ] **Step 4: Wire the orchestrator to pass engine rates**

In `batch_orchestrator.py`, add an import near the other block imports (after line 32):

```python
from .engines.factory import get_swap_cold_start_usd, get_swap_cost_per_photo
```

In `submit_targets`, replace line 279:

```python
            est = estimate_cost(valid, skipped)
```

with:

```python
            est = estimate_cost(
                valid, skipped,
                swap_usd_per_photo=get_swap_cost_per_photo(),
                cold_start_usd=get_swap_cold_start_usd(),
            )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swap_engine_factory.py tests/test_swapbatch_cost_estimator.py -v`
Expected: PASS. (Existing cost-estimator tests still pass — defaults unchanged when overrides are `None`.)

- [ ] **Step 6: Commit**

```bash
git add app/services/block_m2_face_swap/cost_estimator.py app/services/block_m2_face_swap/batch_orchestrator.py tests/test_swap_engine_factory.py
git commit -m "feat(swapbatch): cost estimate sourced from active engine rates"
```

---

### Task 5: Wire the factory into the bot `go` branch

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py:827-841` (inside `_swapbatch_run_phase`, `command == "go"` branch)

- [ ] **Step 1: Replace the hard-coded engine**

In `_swapbatch_run_phase`, the `if command == "go":` block currently reads:

```python
                from app.services.block_m2_face_swap.face_swap_engine import (
                    FaceSwapEngine,
                )
                engine = FaceSwapEngine()
```

Replace those four lines with:

```python
                from app.services.block_m2_face_swap.engines.factory import (
                    get_swap_engine,
                )
                engine = get_swap_engine()
```

The `async def _swap_fn(...)` below it already calls `engine.swap_batch(source, targets, progress_cb=_progress, cancel_check=cancel_check)` — unchanged, since both engines share the `SwapEngine.swap_batch` signature.

- [ ] **Step 2: Verify the module imports cleanly**

Run: `./.venv/Scripts/python.exe -c "import tools.jarvis_smart_telegram_control"`
Expected: no traceback (a warning print about optional deps is fine).

- [ ] **Step 3: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py
git commit -m "feat(swapbatch): select swap engine via factory (default lucataco)"
```

---

### Task 6: ROLLOUT GATE — manual small-batch end-to-end (3–5 photos)

**This is the spec's required first checkpoint. Do not proceed to Phase 1 until it passes.**

**Files:** none (manual verification). No code changes.

- [ ] **Step 1: Confirm env**

Run: `./.venv/Scripts/python.exe -c "import os; from dotenv import load_dotenv; load_dotenv(r'C:\\jarvis\\.env'); print('SWAP_ENGINE=', os.getenv('SWAP_ENGINE','lucataco')); print('token set:', bool(os.getenv('REPLICATE_API_TOKEN')))"`
Expected: `SWAP_ENGINE= lucataco` (or unset → lucataco default), `token set: True`.

- [ ] **Step 2: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_lucataco_client.py tests/test_lucataco_engine.py tests/test_swap_engine_factory.py tests/test_swapbatch_cost_estimator.py tests/test_swapbatch_orchestrator.py -v`
Expected: all PASS.

- [ ] **Step 3: Live Telegram smoke test (human-in-the-loop)**

Through the running bot:
1. `/swapbatch_source` → send one clear face photo → expect "✅ Source принят".
2. `/swapbatch_batch` → send an album of **3–5** target photos → expect a cost report showing ~`$0.0X` swap (≈ count × $0.005), NOT a RunPod-scale number.
3. `/swapbatch_go` → expect progress, then a media-group of swapped photos and a "✅ Swap завершён: N успешно" summary.

- [ ] **Step 4: Record the result**

Confirm: swapped photos returned, cost line reflects lucataco rate, any no-face target shown as failed (not silently dropped). If all good, Phase 0 is complete. Note actual per-photo latency for Phase 1 concurrency tuning.

---

# PHASE 1 — Scale to 100 (multi-album intake, zip delivery)

---

### Task 7: Orchestrator multi-album accumulation + cap 100

**Files:**
- Modify: `app/services/block_m2_face_swap/batch_orchestrator.py:60` (MAX_TARGETS), add `add_targets()` method, `_stage_target` index width.
- Test: `tests/test_swapbatch_accumulation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_swapbatch_accumulation.py`:

```python
from pathlib import Path

import pytest
from PIL import Image

from app.services.block_m2_face_swap.batch_orchestrator import (
    BatchOrchestrator,
    OrchestratorError,
    STATE_TARGETS_RECEIVED,
)


class _AllFacesValidator:
    def count_faces(self, path):  # every photo has exactly one face
        return 1


def _jpg(p: Path) -> Path:
    Image.new("RGB", (32, 32), (10, 20, 30)).save(p, "JPEG")
    return p


def _orch(tmp_path) -> BatchOrchestrator:
    return BatchOrchestrator(
        state_root=tmp_path / "batches", validator=_AllFacesValidator()
    )


def _seed_source(orch, chat, tmp_path):
    orch.begin_source(chat)
    orch.submit_source(chat, _jpg(tmp_path / "src.jpg"))
    orch.begin_targets(chat)


def test_two_albums_accumulate(tmp_path):
    orch = _orch(tmp_path)
    chat = 1
    _seed_source(orch, chat, tmp_path)
    a = [_jpg(tmp_path / f"a{i}.jpg") for i in range(3)]
    b = [_jpg(tmp_path / f"b{i}.jpg") for i in range(2)]

    sess, est = orch.add_targets(chat, a)
    assert len(sess.targets) == 3
    assert sess.status == STATE_TARGETS_RECEIVED

    sess, est = orch.add_targets(chat, b)  # second album appends, not resets
    assert len(sess.targets) == 5
    assert est.valid_count == 5


def test_cap_enforced_across_albums(tmp_path):
    orch = _orch(tmp_path)
    chat = 2
    _seed_source(orch, chat, tmp_path)
    first = [_jpg(tmp_path / f"f{i}.jpg") for i in range(99)]
    orch.add_targets(chat, first)
    with pytest.raises(OrchestratorError):
        orch.add_targets(chat, [_jpg(tmp_path / "over1.jpg"),
                                _jpg(tmp_path / "over2.jpg")])  # 99+2 > 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swapbatch_accumulation.py -v`
Expected: FAIL (`add_targets` does not exist).

- [ ] **Step 3: Raise the cap**

In `batch_orchestrator.py`, change line 60:

```python
MAX_TARGETS = 20
```

to:

```python
# Raised 20→100 for the lucataco batch (parallel swaps, ~100s for 100 photos).
# Note: the (frozen) animate phase is sequential — large batches there are hours.
MAX_TARGETS = 100
```

- [ ] **Step 4: Widen the staging index**

In `_stage_target` (line 769), change:

```python
        target = d / f"{idx:02d}{suffix}"
```

to:

```python
        target = d / f"{idx:03d}{suffix}"
```

- [ ] **Step 5: Add the `add_targets` method**

Insert this method into `BatchOrchestrator` right after `submit_targets` (after line 284):

```python
    def add_targets(
        self, chat_id: int, target_paths: list[Path]
    ) -> tuple[BatchSession, CostEstimate]:
        """Append a freshly-flushed album to the batch (multi-album intake).

        Unlike ``submit_targets`` (which resets), this preserves already-staged
        targets so a 100-photo batch arriving as ~10 Telegram albums accumulates
        into one session. Valid from EXPECTING_TARGETS (first album) and
        TARGETS_RECEIVED (subsequent albums). Re-computes the cost estimate over
        the full accumulated set and enforces MAX_TARGETS on the cumulative count.
        """
        if not target_paths:
            raise OrchestratorError("no target photos provided")
        target_paths = list(dict.fromkeys(target_paths))
        with self._lock:
            sess = self._require(
                chat_id, {STATE_EXPECTING_TARGETS, STATE_TARGETS_RECEIVED}
            )
            base = len(sess.targets)
            if base + len(target_paths) > MAX_TARGETS:
                raise OrchestratorError(
                    f"Слишком много фото: {base + len(target_paths)}. Максимум "
                    f"{MAX_TARGETS} за батч. Уже принято {base} — пришли меньше."
                )
            for offset, raw in enumerate(target_paths):
                idx = base + offset
                staged = self._stage_target(chat_id, raw, idx)
                try:
                    fc = self._validator.count_faces(staged)
                except Exception as exc:  # noqa: BLE001
                    sess.targets.append(TargetItem(
                        path=str(staged), face_count=0, valid=False,
                        error=f"validator failed: {exc}",
                    ))
                    continue
                sess.targets.append(TargetItem(
                    path=str(staged), face_count=fc, valid=fc > 0,
                    error=None if fc > 0 else "no face detected",
                ))
            valid = sum(1 for t in sess.targets if t.valid)
            skipped = len(sess.targets) - valid
            est = estimate_cost(
                valid, skipped,
                swap_usd_per_photo=get_swap_cost_per_photo(),
                cold_start_usd=get_swap_cold_start_usd(),
            )
            sess.cost_estimate = asdict(est)
            sess.status = STATE_TARGETS_RECEIVED
            self._touch(sess)
            self._persist(sess)
            return sess, est
```

This references `STATE_EXPECTING_TARGETS` and `STATE_TARGETS_RECEIVED` (both already defined at module top) and `get_swap_cost_per_photo`/`get_swap_cold_start_usd` (imported in Task 4).

- [ ] **Step 6: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swapbatch_accumulation.py tests/test_swapbatch_orchestrator.py -v`
Expected: PASS (new accumulation tests + existing orchestrator tests).

- [ ] **Step 7: Commit**

```bash
git add app/services/block_m2_face_swap/batch_orchestrator.py tests/test_swapbatch_accumulation.py
git commit -m "feat(swapbatch): multi-album target accumulation, cap 20->100"
```

---

### Task 8: Handler + album intercept use accumulation

**Files:**
- Modify: `app/handlers/face_swap_handler.py:368-385` (`consume_targets_album`)
- Modify: `app/handlers/face_swap_handler.py:145-156` (`handle_batch_intent` text → "до 100")
- Modify: `tools/jarvis_smart_telegram_control.py:1004-1006` (album intercept gate)

- [ ] **Step 1: Make `consume_targets_album` accumulate**

Replace the body of `consume_targets_album` (lines 368-385) with:

```python
    def consume_targets_album(
        self, chat_id: int, local_photo_paths: list[Path]
    ) -> HandlerReply:
        """Append a buffered media-group to the targets (multi-album intake)."""
        status = self.orchestrator.status(chat_id)
        if status not in (STATE_EXPECTING_TARGETS, STATE_TARGETS_RECEIVED):
            return HandlerReply(consumed=False)
        try:
            sess, est = self.orchestrator.add_targets(chat_id, local_photo_paths)
        except OrchestratorError as exc:
            return HandlerReply(text=f"⚠️ {exc}")
        msg = format_cost_report_ru(
            est,
            source_face_count=sess.source_face_count,
            total_targets=len(sess.targets),
        )
        return HandlerReply(text=msg)
```

This uses `STATE_TARGETS_RECEIVED`, already imported at the top of the file (line 30).

- [ ] **Step 2: Update the batch-intent prompt text**

In `handle_batch_intent` (lines 150-156), change the user text from "до 20 штук" to reflect 100 and multi-album:

```python
        return HandlerReply(
            text=(
                "📦 Жду target-фото (до 100 штук). Можешь слать несколькими "
                "альбомами подряд — я докину каждый в текущий батч и буду "
                "показывать сколько принято. Когда всё — /swapbatch_go."
            )
        )
```

- [ ] **Step 3: Loosen the album-intercept gate**

In `tools/jarvis_smart_telegram_control.py`, `_swapbatch_album_intercept` (lines 1004-1006) currently bails unless waiting for targets. Replace:

```python
    chat_id_int = int(chat_id)
    if not orch.is_waiting_for_targets(chat_id_int):
        return False
```

with:

```python
    from app.services.block_m2_face_swap.batch_orchestrator import (
        STATE_EXPECTING_TARGETS, STATE_TARGETS_RECEIVED,
    )
    chat_id_int = int(chat_id)
    if orch.status(chat_id_int) not in (
        STATE_EXPECTING_TARGETS, STATE_TARGETS_RECEIVED,
    ):
        return False
```

This lets a second/third album (arriving when the session is already in `TARGETS_RECEIVED`) still route into accumulation.

- [ ] **Step 4: Verify**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swapbatch_handler.py -v`
Expected: PASS. (If an existing handler test asserts the old "reset" semantics or the "до 20"/single-album text, update that test's expectation to the accumulation behavior and the new text — accumulation is the intended new contract.)

- [ ] **Step 5: Commit**

```bash
git add app/handlers/face_swap_handler.py tools/jarvis_smart_telegram_control.py tests/test_swapbatch_handler.py
git commit -m "feat(swapbatch): accept targets across multiple albums (up to 100)"
```

---

### Task 9: Result delivery helpers (chunk + size-split zips)

**Files:**
- Create: `app/services/block_m2_face_swap/result_delivery.py`
- Test: `tests/test_result_delivery.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_result_delivery.py`:

```python
from pathlib import Path

from app.services.block_m2_face_swap.result_delivery import (
    build_result_zips,
    chunk_photos,
)


def test_chunk_photos_groups_by_ten():
    items = [Path(f"{i}.jpg") for i in range(23)]
    chunks = chunk_photos(items, size=10)
    assert [len(c) for c in chunks] == [10, 10, 3]


def test_build_result_zips_splits_by_size(tmp_path):
    # 5 files of ~30 KB each, cap 50 KB → multiple parts.
    files = []
    for i in range(5):
        p = tmp_path / f"r{i}.jpg"
        p.write_bytes(b"\x00" * 30_000)
        files.append(p)
    out_dir = tmp_path / "zips"
    zips = build_result_zips(files, out_dir, max_bytes=50_000)
    assert len(zips) >= 2
    assert all(z.exists() and z.suffix == ".zip" for z in zips)


def test_build_result_zips_single_part_when_small(tmp_path):
    files = []
    for i in range(3):
        p = tmp_path / f"r{i}.jpg"
        p.write_bytes(b"\x00" * 1000)
        files.append(p)
    zips = build_result_zips(files, tmp_path / "zips", max_bytes=10_000_000)
    assert len(zips) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_result_delivery.py -v`
Expected: FAIL (module not found).

- [ ] **Step 3: Write the helpers**

Create `app/services/block_m2_face_swap/result_delivery.py`:

```python
# -*- coding: utf-8 -*-
"""Pure helpers for delivering batch swap results to Telegram.

``chunk_photos`` groups paths into Telegram media-group-sized batches (10).
``build_result_zips`` packs results into one or more zips, each kept under
``max_bytes`` so it stays below Telegram's ~50 MB sendDocument cap. Stored JPEGs
barely compress, so we size parts by raw input bytes (a safe over-estimate).
"""
from __future__ import annotations

import zipfile
from pathlib import Path

# Telegram bot sendDocument hard cap is 50 MB; leave headroom for zip overhead.
DEFAULT_ZIP_MAX_BYTES = 45 * 1024 * 1024
MEDIA_GROUP_SIZE = 10


def chunk_photos(paths: list[Path], size: int = MEDIA_GROUP_SIZE) -> list[list[Path]]:
    return [paths[i:i + size] for i in range(0, len(paths), size)]


def build_result_zips(
    paths: list[Path],
    out_dir: Path,
    max_bytes: int = DEFAULT_ZIP_MAX_BYTES,
) -> list[Path]:
    """Pack ``paths`` into size-bounded zip parts under ``out_dir``.

    Returns the list of created zip Paths (named ``results_1ofN.zip`` …). A
    single oversized file still goes into its own part (never silently dropped).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = [p for p in paths if p.exists()]
    # First pass: assign files to parts by cumulative raw size.
    parts: list[list[Path]] = [[]]
    running = 0
    for p in existing:
        size = p.stat().st_size
        if parts[-1] and running + size > max_bytes:
            parts.append([])
            running = 0
        parts[-1].append(p)
        running += size
    if not parts[0]:
        return []
    total = len(parts)
    zips: list[Path] = []
    for i, part in enumerate(parts, start=1):
        zpath = out_dir / f"results_{i}of{total}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as zf:
            for p in part:
                zf.write(p, arcname=p.name)
        zips.append(zpath)
    return zips
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_result_delivery.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_face_swap/result_delivery.py tests/test_result_delivery.py
git commit -m "feat(swapbatch): result delivery helpers (album chunks + size-split zips)"
```

---

### Task 10: Deliver zips after the photo albums

**Files:**
- Modify: `app/handlers/face_swap_handler.py:51-71` (add `documents` to `HandlerReply`)
- Modify: `app/handlers/face_swap_handler.py:409-435` (build zip docs in `run_swap_phase`)
- Modify: `tools/jarvis_smart_telegram_control.py:696-721` (`_swapbatch_apply_reply` sends documents + throttle)

- [ ] **Step 1: Add a `documents` field to `HandlerReply`**

In `HandlerReply` (after the `videos` field, line 70), add:

```python
    documents: list[Path] | None = None
```

And extend the docstring's attribute list with:

```python
        documents: Local files to send as Telegram documents (e.g. result zips),
            sent after photos.
```

- [ ] **Step 2: Build the zips in `run_swap_phase`**

In `run_swap_phase`, after `photos = [...]` (line 413) and before the summary `lines` block, insert zip construction:

```python
        documents = []
        if photos:
            from app.services.block_m2_face_swap.result_delivery import (
                build_result_zips,
            )
            zip_dir = photos[0].parent.parent / "delivery"
            try:
                documents = build_result_zips(photos, zip_dir)
            except Exception as exc:  # noqa: BLE001 — zip is a convenience, not critical
                logger.warning("result zip build failed: %s", exc)
                documents = []
```

Then change the final return (line 435) from:

```python
        return HandlerReply(text="\n".join(lines), photos=photos)
```

to:

```python
        return HandlerReply(
            text="\n".join(lines), photos=photos, documents=documents,
        )
```

- [ ] **Step 3: Send documents (and throttle albums) in the wiring**

In `_swapbatch_apply_reply` (lines 712-721), replace the `reply.photos` / `reply.videos` tail with a throttled version that also sends documents:

```python
    if reply.photos:
        import time as _t
        from app.services.block_m2_face_swap.result_delivery import chunk_photos
        chunks = chunk_photos([Path(p) for p in reply.photos])
        for ci, chunk in enumerate(chunks):
            try:
                _send_local_media_group(chat_id_s, [str(p) for p in chunk])
            except Exception as exc:  # noqa: BLE001
                send(chat_id_s, f"⚠️ Не удалось отправить альбом: {exc}")
                for p in chunk:
                    _send_local_photo(chat_id_s, str(p))
            if ci < len(chunks) - 1:
                _t.sleep(1.5)  # avoid Telegram album rate-limit (429)
    documents = getattr(reply, "documents", None)
    if documents:
        for d in documents:
            try:
                _send_local_document(chat_id_s, str(d))
            except Exception as exc:  # noqa: BLE001
                send(chat_id_s, f"⚠️ Не удалось отправить архив {Path(d).name}: {exc}")
    if reply.videos:
        for v in reply.videos:
            _send_local_video(chat_id_s, str(v))
```

`Path` is already imported at module top; this requires `_send_local_document` — add it in Step 4. Note `_send_local_media_group` already chunks internally, but we chunk here too so the 1.5s throttle sits between albums.

- [ ] **Step 4: Add `_send_local_document`**

Immediately after `_send_local_video` (ends at line 296), add:

```python
def _send_local_document(chat_id, path, caption: str = "") -> None:
    """Upload a local file (e.g. a results zip) via multipart sendDocument."""
    import requests as _req
    from pathlib import Path as _Path
    p = _Path(path)
    if not p.exists():
        send(str(chat_id), f"⚠️ Document not found: {p}")
        return
    with p.open("rb") as fh:
        _req.post(
            f"{TG}/sendDocument",
            data={"chat_id": str(chat_id), "caption": caption[:1024] if caption else ""},
            files={"document": (p.name, fh, "application/zip")},
            timeout=300,
        )
```

- [ ] **Step 5: Verify imports**

Run: `./.venv/Scripts/python.exe -c "import tools.jarvis_smart_telegram_control; from app.handlers.face_swap_handler import HandlerReply; print(HandlerReply().documents)"`
Expected: prints `None`, no traceback.

- [ ] **Step 6: Commit**

```bash
git add app/handlers/face_swap_handler.py tools/jarvis_smart_telegram_control.py
git commit -m "feat(swapbatch): deliver results as albums + size-split zips"
```

---

### Task 11: Batch the progress messages (avoid Telegram flood)

**Files:**
- Modify: `tools/jarvis_smart_telegram_control.py:799-823` (`_progress` in `_swapbatch_run_phase`)

- [ ] **Step 1: Handle the new `swap_progress` event, throttled**

The lucataco engine fires `("swap_progress", {"completed", "total", "index", "ok"})` per target (Task 3). Add a branch to `_progress` that only messages every 10 completions (and on the last), so 100 swaps produce ~10 messages, not 100. Insert after the existing `elif stage == "swap_failed":` block (before `elif stage == "animate_step_done":`):

```python
        elif stage == "swap_progress":
            completed = payload.get("completed", 0)
            total = payload.get("total", 0)
            if completed == total or completed % 10 == 0:
                send(chat_id_s, f"🔄 Swap {completed}/{total}…")
```

The legacy `swap_started`/`swap_failed` branches stay for the (frozen) RunPod engine, which still fires them; lucataco simply uses `swap_progress`.

- [ ] **Step 2: Verify import**

Run: `./.venv/Scripts/python.exe -c "import tools.jarvis_smart_telegram_control"`
Expected: no traceback.

- [ ] **Step 3: Commit**

```bash
git add tools/jarvis_smart_telegram_control.py
git commit -m "feat(swapbatch): batch swap progress to every 10/N (flood guard)"
```

---

### Task 12: ROLLOUT GATE — manual large-batch end-to-end (~100 photos)

**Files:** none (manual verification).

- [ ] **Step 1: Run the full Phase-1 suite**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_swapbatch_accumulation.py tests/test_result_delivery.py tests/test_swapbatch_handler.py tests/test_lucataco_engine.py tests/test_lucataco_client.py tests/test_swap_engine_factory.py -v`
Expected: all PASS.

- [ ] **Step 2: Live Telegram test at scale**

1. `/swapbatch_source` → send a face.
2. `/swapbatch_batch` → send **several albums** totalling ~100 photos. Expect a running "принято / всего M/100" cost report after each album, with swap cost ≈ `$0.50`.
3. Confirm cap: try to exceed 100 → expect the "Слишком много фото" error.
4. `/swapbatch_go` → expect throttled "🔄 Swap N/100…" progress, then ~10 albums of swapped photos, then 1–2 `results_NofM.zip` documents, then "✅ Swap завершён: 9X успешно" (with any no-face count reported).

- [ ] **Step 3: Verify the failure tally is honest**

Include at least one face-less photo in the batch; confirm it appears in the "⏭ пропущено (без лица)" or "⚠️ не удалось" count and is NOT in the delivered results — the "95/100" must reflect reality (no-face = output None must be counted as failure, per the verified lucataco behavior).

- [ ] **Step 4: Note concurrency headroom**

Watch logs for HTTP 429 during the 100-photo run. If none, the semaphore (5) can be raised later via `_DEFAULT_CONCURRENCY` in `lucataco_engine.py`; if 429s appear, the backoff handled them — leave at 5 or lower.

---

## Self-Review

**Spec coverage:**
- Pluggable `SwapEngine` interface → Task 1; factory selection → Task 1/5; future Path-B swap = config + class (design satisfied). ✅
- lucataco data-URI engine (downscale, semaphore, 429 backoff) → Tasks 2–3. ✅
- `output=None` = billable failure, no retry; retry only on 429/network → Task 2 (client) + Task 3 (engine), verified in Task 12 Step 3. ✅
- Multi-album accumulation, cap 20→100 → Tasks 7–8. ✅
- Cost from engine, ~$0.50 confirmation before `/swapbatch_go` → Task 4 (already shown at album receipt; `go` is the confirm action). ✅
- Partial failure "95/100" → existing orchestrator/handler tally + engine `None` mapping; verified Task 12. ✅
- Delivery albums + size-split zip (50 MB cap) → Tasks 9–10. ✅
- Progress batching → Task 11. ✅
- Rollout "3–5 first, then 100" → Task 6 gate (Phase 0) precedes Phase 1; Task 12 gate. ✅
- Video untouched / RunPod frozen → no animate-path code changed; factory defaults to lucataco; `runpod` engine kept for rollback only. ✅

**Placeholder scan:** No TBD/TODO; every code step shows full code. ✅

**Type consistency:** `swap_batch(source, targets, *, progress_cb, cancel_check) -> list[Path|None]` consistent across `base.py`, `lucataco_engine.py`, and the existing `confirm_swap` callback contract. `HandlerReply.documents` added once (Task 10) and consumed once (wiring). `add_targets` returns `(BatchSession, CostEstimate)` matching `submit_targets`. Cost helpers `get_swap_cost_per_photo`/`get_swap_cold_start_usd` defined in Task 1, imported in Tasks 4 & 7. `swap_progress` event emitted in Task 3, consumed in Task 11. ✅

**Note on test infra:** async tests assume `pytest-asyncio` (used by existing `tests/test_swapbatch_*`). If a run errors on async collection, confirm `asyncio_mode = auto` in the project pytest config; `@pytest.mark.asyncio` is also present as a fallback.
