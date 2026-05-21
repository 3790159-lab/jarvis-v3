# Sniper Provisioning Callback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a successful RunPod sniper catch into an automatically-provisioned, ready-to-use ComfyUI pod with three Telegram outcomes (✅ ready / ❌ failed / ⏰ timeout) and no manual SSH.

**Architecture:** Three layers. A RunPod template (already-plumbed `RUNPOD_TEMPLATE_ID`) injects `startCmd=bash -lc '/workspace/bootstrap.sh'` into every pod boot. `bootstrap.sh` (on the persistent network volume) gates a slow install via a versioned manifest and always runs a Python import probe before launching ComfyUI. A new passive `pod_provisioner` module observes the pod via interleaved HTTP `/system_stats` + `RunpodClient.get_pod()` and returns a three-state `ProvisionResult`. The sniper imports the provisioner, calls it after catch, and fires one of three (plus one SIGINT) Telegram alerts.

**Tech Stack:** Python 3.11 + asyncio, `httpx.AsyncClient`, `pydantic` v2 BaseModel, `unittest.mock.AsyncMock` for tests, pytest-asyncio, RunPod GraphQL via existing `RunpodClient`, bash for the on-pod bootstrap.

**Spec:** [`docs/superpowers/specs/2026-05-21-sniper-provisioning-callback-design.md`](../specs/2026-05-21-sniper-provisioning-callback-design.md) (commit `1a74733`).

---

## File Structure

### Files created

| Path | Responsibility |
|---|---|
| `app/services/block_m2_video/runpod/pod_provisioner.py` | Pure passive observability — `wait_for_pod_ready` polls HTTP + pod state, returns `ProvisionResult`. No pod mutation. |
| `tests/test_pod_provisioner.py` | Unit tests for the provisioner. All deps injected (clock, sleeper, http_client, interrupted). Zero real network, zero real time. |
| `scripts/remote/bootstrap_pod.sh` | Repo source-of-truth for the on-volume bootstrap. Versioned manifest gate + import probe + ComfyUI exec. |
| `docs/runpod_template_config.md` | Backup documentation of the RunPod template (image, startCmd, ports, mount). 5-minute recreation from a clean account. |

### Files modified

| Path | Change |
|---|---|
| `scripts/runpod_gpu_sniper.py` | ~40-line diff: import provisioner, call after catch, append "(waiting…)" to existing alert, branch on outcome for second alert, handle SIGINT-during-readiness, extend `_write_status()` payload. |
| `tests/test_runpod_gpu_sniper.py` | Light extensions: monkeypatch `sniper.wait_for_pod_ready`, parametrized assertions per outcome, SIGINT-during-readiness test. |

### Files NOT touched (deliberate)

`app/services/block_m2_video/runpod/runpod_client.py`, `app/services/block_m2_video/runpod/runpod_config.py`, `scripts/runpod_recreate_phase3.py`, `app/services/notifications/*`. See spec § "Files NOT touched".

---

## Conventions (used across all tasks)

- **Async tests:** use `@pytest.mark.asyncio` (matches existing `tests/test_runpod_gpu_sniper.py`). Do **not** use `@pytest.mark.anyio` — that's only used in `test_runpod_client.py`.
- **Mock pattern:** `unittest.mock.AsyncMock` / `MagicMock(spec=httpx.AsyncClient)`. No real HTTP, no real time.
- **Imports in `pod_provisioner.py`:** `from __future__ import annotations` at the top.
- **Commit style:** Conventional Commits matching the project's recent history (e.g., `feat(runpod): …`, `test(runpod): …`, `docs(runpod): …`). End each commit message with the standard `Co-Authored-By` trailer block via heredoc — see Task 1's commit step for the exact format.
- **Run tests with:** `pytest tests/test_pod_provisioner.py -v` from the repo root.

---

## Task 1: Module scaffold — data classes + function signature

**Goal:** Get the new module importable and provide the empty function shape. No real logic yet — the function returns `TIMEOUT` immediately for any input. This lets later tasks add behavior incrementally.

**Files:**
- Create: `app/services/block_m2_video/runpod/pod_provisioner.py`
- Test: `tests/test_pod_provisioner.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pod_provisioner.py`:

```python
# -*- coding: utf-8 -*-
"""Unit tests for the RunPod pod_provisioner module.

All HTTP and RunPod API traffic is mocked. The provisioner is exercised
through its injectable kwargs (clock, sleeper, http_client, interrupted)
exactly like the sniper's _snipe() — no real time, no real network.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services.block_m2_video.runpod.pod_provisioner import (
    ProvisionOutcome,
    ProvisionResult,
    wait_for_pod_ready,
)
from app.services.block_m2_video.runpod.runpod_client import (
    PodInfo,
    RunpodClient,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_pod(pod_id: str = "pod_abc123", desired_status: str = "RUNNING") -> PodInfo:
    return PodInfo.from_api(
        {
            "id": pod_id,
            "name": "jarvis-i2v-sniper-1",
            "desiredStatus": desired_status,
            "costPerHr": 1.89,
            "imageName": "runpod/test:latest",
            "machineId": "m_test",
            "gpuCount": 1,
            "lastStatusChange": "now",
            "runtime": {
                "ports": [
                    {
                        "privatePort": 8188,
                        "publicPort": 8188,
                        "ip": "1.2.3.4",
                        "isIpPublic": True,
                        "type": "http",
                    }
                ],
                "uptimeInSeconds": 5,
            },
        }
    )


def _make_response(status_code: int) -> httpx.Response:
    request = httpx.Request("GET", "https://example.proxy.runpod.net/system_stats")
    return httpx.Response(status_code=status_code, request=request)


def _make_http(*responses_or_excs) -> MagicMock:
    """Build a mocked AsyncClient whose .get cycles through the given side_effect."""
    http = MagicMock(spec=httpx.AsyncClient)
    http.get = AsyncMock(side_effect=list(responses_or_excs))
    return http


def _make_clock(*ticks: float):
    """Iterator-backed clock; raises StopIteration if loop runs too long (=bug)."""
    it = iter(ticks)
    return lambda: next(it)


@pytest.fixture
def fake_sleep() -> AsyncMock:
    async def _no_op(_seconds: float) -> None:
        return None

    return AsyncMock(side_effect=_no_op)


# ── tests ─────────────────────────────────────────────────────────────────────


def test_provision_outcome_enum_members():
    assert ProvisionOutcome.READY.value == "ready"
    assert ProvisionOutcome.CONTAINER_EXITED.value == "container_exited"
    assert ProvisionOutcome.TIMEOUT.value == "timeout"


def test_provision_result_is_pydantic_model():
    r = ProvisionResult(
        outcome=ProvisionOutcome.READY,
        pod_id="pod_abc123",
        public_url="https://pod_abc123-8188.proxy.runpod.net",
        elapsed_sec=12.5,
        detail="ready in 12.5s",
    )
    assert r.outcome is ProvisionOutcome.READY
    assert r.pod_id == "pod_abc123"
    assert r.elapsed_sec == 12.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
pytest tests/test_pod_provisioner.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'app.services.block_m2_video.runpod.pod_provisioner'`.

- [ ] **Step 3: Write the minimal module**

Create `app/services/block_m2_video/runpod/pod_provisioner.py`:

```python
# -*- coding: utf-8 -*-
"""Pod provisioning observer.

After the GPU sniper successfully spawns a pod, this module waits for
ComfyUI to bind ``/system_stats`` while also watching the pod's RunPod-
level lifecycle. It returns a three-state :class:`ProvisionResult` the
sniper translates into one of three Telegram alerts.

The module is **purely passive**: it never stops or terminates pods.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Awaitable, Callable

import httpx
from pydantic import BaseModel

from .runpod_client import PodInfo, RunpodClient

logger = logging.getLogger(__name__)

DEFAULT_READINESS_TIMEOUT_MIN = 25
DEFAULT_POLL_INTERVAL_SEC = 30
CONSECUTIVE_TRANSIENT_FAILURE_THRESHOLD = 3
SYSTEM_STATS_PATH = "/system_stats"
HTTP_PROBE_TIMEOUT_SEC = 10.0


class ProvisionOutcome(str, Enum):
    READY = "ready"
    CONTAINER_EXITED = "container_exited"
    TIMEOUT = "timeout"


class ProvisionResult(BaseModel):
    outcome: ProvisionOutcome
    pod_id: str
    public_url: str | None
    elapsed_sec: float
    detail: str

    model_config = {"extra": "ignore"}


async def wait_for_pod_ready(
    client: RunpodClient,
    pod: PodInfo,
    *,
    http_client: httpx.AsyncClient | None = None,
    timeout_min: int = DEFAULT_READINESS_TIMEOUT_MIN,
    poll_interval_sec: int = DEFAULT_POLL_INTERVAL_SEC,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], Awaitable[None]] | None = None,
    interrupted: Callable[[], bool] | None = None,
) -> ProvisionResult:
    """Poll a freshly-caught pod until READY, CONTAINER_EXITED, or TIMEOUT.

    All four ``http_client`` / ``clock`` / ``sleeper`` / ``interrupted``
    kwargs are injectable so the function can be unit-tested with zero
    real time and zero real network.
    """
    # Placeholder — later tasks build out the real loop.
    return ProvisionResult(
        outcome=ProvisionOutcome.TIMEOUT,
        pod_id=pod.id,
        public_url=None,
        elapsed_sec=0.0,
        detail="not implemented yet",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/test_pod_provisioner.py -v
```

Expected: both `test_provision_outcome_enum_members` and `test_provision_result_is_pydantic_model` PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_video/runpod/pod_provisioner.py tests/test_pod_provisioner.py
git commit -m "$(cat <<'EOF'
feat(runpod): pod_provisioner module scaffold

ProvisionOutcome enum (READY / CONTAINER_EXITED / TIMEOUT) and
ProvisionResult pydantic model. wait_for_pod_ready signature with
injectable clock/sleeper/http_client/interrupted kwargs; real loop
arrives in subsequent commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: READY outcome — HTTP 200 happy path + transient error recovery

**Goal:** First HTTP 200 returns `READY` immediately. Transient `httpx.ConnectError` / `httpx.TimeoutException` are absorbed silently and the loop keeps polling.

**Files:**
- Modify: `app/services/block_m2_video/runpod/pod_provisioner.py`
- Test: `tests/test_pod_provisioner.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pod_provisioner.py`:

```python
@pytest.mark.asyncio
async def test_outcome_ready_on_first_poll(fake_sleep: AsyncMock) -> None:
    """First HTTP call returns 200 → READY without sleeping."""
    pod = _make_pod()
    client = AsyncMock(spec=RunpodClient)
    client.get_pod = AsyncMock(return_value=pod)
    http = _make_http(_make_response(200))

    result = await wait_for_pod_ready(
        client,
        pod,
        http_client=http,
        clock=_make_clock(0.0, 0.0, 0.5),  # start, deadline-check, post-success
        sleeper=fake_sleep,
        timeout_min=25,
        poll_interval_sec=30,
    )

    assert result.outcome is ProvisionOutcome.READY
    assert result.pod_id == pod.id
    assert result.public_url == "https://pod_abc123-8188.proxy.runpod.net"
    assert result.elapsed_sec == pytest.approx(0.5, abs=0.01)
    http.get.assert_awaited_once()
    fake_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_outcome_ready_after_transient_connect_errors(
    fake_sleep: AsyncMock,
) -> None:
    """ConnectError on the first two probes, then 200 on the third → READY."""
    pod = _make_pod()
    client = AsyncMock(spec=RunpodClient)
    client.get_pod = AsyncMock(return_value=pod)
    http = _make_http(
        httpx.ConnectError("not bound yet"),
        httpx.TimeoutException("slow"),
        _make_response(200),
    )

    result = await wait_for_pod_ready(
        client,
        pod,
        http_client=http,
        clock=_make_clock(0.0, 0.0, 30.0, 60.0, 60.5),
        sleeper=fake_sleep,
        timeout_min=25,
        poll_interval_sec=30,
    )

    assert result.outcome is ProvisionOutcome.READY
    assert http.get.await_count == 3
    assert fake_sleep.await_count == 2  # one sleep per failed probe
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
pytest tests/test_pod_provisioner.py::test_outcome_ready_on_first_poll tests/test_pod_provisioner.py::test_outcome_ready_after_transient_connect_errors -v
```

Expected: both FAIL — placeholder returns `TIMEOUT`.

- [ ] **Step 3: Implement the READY loop**

Replace the placeholder body of `wait_for_pod_ready` in `app/services/block_m2_video/runpod/pod_provisioner.py` with:

```python
async def wait_for_pod_ready(
    client: RunpodClient,
    pod: PodInfo,
    *,
    http_client: httpx.AsyncClient | None = None,
    timeout_min: int = DEFAULT_READINESS_TIMEOUT_MIN,
    poll_interval_sec: int = DEFAULT_POLL_INTERVAL_SEC,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], Awaitable[None]] | None = None,
    interrupted: Callable[[], bool] | None = None,
) -> ProvisionResult:
    import asyncio
    import time

    _clock = clock if clock is not None else time.monotonic
    _sleep = sleeper if sleeper is not None else asyncio.sleep
    _own_http = http_client is None
    http = http_client or httpx.AsyncClient(timeout=HTTP_PROBE_TIMEOUT_SEC)

    public_url = f"https://{pod.id}-8188.proxy.runpod.net"
    start = _clock()
    deadline = start + timeout_min * 60

    try:
        while _clock() < deadline:
            # 1. HTTP probe
            try:
                response = await http.get(
                    f"{public_url}{SYSTEM_STATS_PATH}",
                    timeout=HTTP_PROBE_TIMEOUT_SEC,
                )
                if response.status_code == 200:
                    elapsed = _clock() - start
                    return ProvisionResult(
                        outcome=ProvisionOutcome.READY,
                        pod_id=pod.id,
                        public_url=public_url,
                        elapsed_sec=elapsed,
                        detail=f"ComfyUI ready in {elapsed:.1f}s",
                    )
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                logger.debug("[provisioner] HTTP probe transient: %s", exc)

            await _sleep(poll_interval_sec)

        # Deadline reached
        elapsed = _clock() - start
        return ProvisionResult(
            outcome=ProvisionOutcome.TIMEOUT,
            pod_id=pod.id,
            public_url=public_url,
            elapsed_sec=elapsed,
            detail=(
                f"Pod still RUNNING but /system_stats never returned 200 "
                f"in {timeout_min} min — check web terminal for pod {pod.id}"
            ),
        )
    finally:
        if _own_http:
            await http.aclose()
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/test_pod_provisioner.py -v
```

Expected: 4 tests PASS (the two from Task 1 + the two new ones).

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_video/runpod/pod_provisioner.py tests/test_pod_provisioner.py
git commit -m "$(cat <<'EOF'
feat(runpod): READY outcome + transient HTTP error recovery

wait_for_pod_ready returns ProvisionOutcome.READY on first HTTP 200
from /system_stats. ConnectError and TimeoutException are absorbed
silently between polls; the loop keeps trying until deadline.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: CONTAINER_EXITED outcome + precedence over HTTP 404

**Goal:** When `get_pod()` shows `desired_status == "EXITED"`, return `CONTAINER_EXITED` and exit immediately. This signal takes precedence over the HTTP probe (the pod is gone — don't wait for the rest of the deadline).

**Files:**
- Modify: `app/services/block_m2_video/runpod/pod_provisioner.py`
- Test: `tests/test_pod_provisioner.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pod_provisioner.py`:

```python
@pytest.mark.asyncio
async def test_outcome_container_exited_on_pod_exited(
    fake_sleep: AsyncMock,
) -> None:
    """Pod desired_status flips to EXITED → CONTAINER_EXITED, no further waits."""
    running_pod = _make_pod()
    exited_pod = _make_pod(desired_status="EXITED")
    client = AsyncMock(spec=RunpodClient)
    # First get_pod = RUNNING, second = EXITED
    client.get_pod = AsyncMock(side_effect=[running_pod, exited_pod])
    http = _make_http(_make_response(404), _make_response(404))

    result = await wait_for_pod_ready(
        client,
        running_pod,
        http_client=http,
        clock=_make_clock(0.0, 0.0, 30.0, 60.0, 60.5),
        sleeper=fake_sleep,
        timeout_min=25,
        poll_interval_sec=30,
    )

    assert result.outcome is ProvisionOutcome.CONTAINER_EXITED
    assert result.pod_id == running_pod.id
    assert "exited" in result.detail.lower()
    assert running_pod.id in result.detail
    # Returned before deadline — at most a handful of polls
    assert client.get_pod.await_count <= 3


@pytest.mark.asyncio
async def test_container_exited_takes_precedence_over_http_404(
    fake_sleep: AsyncMock,
) -> None:
    """Even if HTTP keeps 404-ing, EXITED state wins in the same iteration."""
    running_pod = _make_pod()
    exited_pod = _make_pod(desired_status="EXITED")
    client = AsyncMock(spec=RunpodClient)
    client.get_pod = AsyncMock(return_value=exited_pod)  # always EXITED
    http = _make_http(_make_response(404))  # one 404, then EXITED wins

    result = await wait_for_pod_ready(
        client,
        running_pod,
        http_client=http,
        clock=_make_clock(0.0, 0.0, 1.0),
        sleeper=fake_sleep,
        timeout_min=25,
        poll_interval_sec=30,
    )

    assert result.outcome is ProvisionOutcome.CONTAINER_EXITED
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
pytest tests/test_pod_provisioner.py::test_outcome_container_exited_on_pod_exited tests/test_pod_provisioner.py::test_container_exited_takes_precedence_over_http_404 -v
```

Expected: both FAIL — current impl has no pod-state check.

- [ ] **Step 3: Add the pod-state check**

In `app/services/block_m2_video/runpod/pod_provisioner.py`, inside the `while _clock() < deadline:` loop, **after** the HTTP probe block but **before** `await _sleep(...)`, add:

```python
            # 2. Pod-state check (precedence over continued HTTP 404)
            try:
                pod_now = await client.get_pod(pod.id)
            except Exception as exc:  # noqa: BLE001 — handled in Task 5
                logger.debug("[provisioner] get_pod transient: %s", exc)
                pod_now = None

            if pod_now and (pod_now.desired_status or "").upper() == "EXITED":
                elapsed = _clock() - start
                return ProvisionResult(
                    outcome=ProvisionOutcome.CONTAINER_EXITED,
                    pod_id=pod.id,
                    public_url=public_url,
                    elapsed_sec=elapsed,
                    detail=(
                        f"Container exited {elapsed:.0f}s after spawn — "
                        f"check RunPod console logs for pod {pod.id}"
                    ),
                )
```

The broad `except Exception` here is intentional and temporary; Task 5 replaces it with a `RunpodApiError`-specific handler + threshold counter.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/test_pod_provisioner.py -v
```

Expected: all 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_video/runpod/pod_provisioner.py tests/test_pod_provisioner.py
git commit -m "$(cat <<'EOF'
feat(runpod): CONTAINER_EXITED outcome via pod-state check

wait_for_pod_ready now also polls RunpodClient.get_pod() each
iteration. When desired_status == EXITED, return immediately with
ProvisionOutcome.CONTAINER_EXITED — this takes precedence over the
HTTP probe so we don't waste the full 25-min deadline on a pod that
is already gone.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: TIMEOUT outcome — deadline with pod still RUNNING / PENDING

**Goal:** Two scenarios both yield `TIMEOUT`: pod stayed RUNNING but HTTP never 200 within `timeout_min`, OR pod stayed PENDING the whole time. The deadline path is already in the placeholder return from Task 2 — we just need explicit tests to lock it in.

**Files:**
- Test: `tests/test_pod_provisioner.py`

(No production code changes — Task 2's deadline return already covers both cases.)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pod_provisioner.py`:

```python
@pytest.mark.asyncio
async def test_outcome_timeout_when_pod_running_but_http_never_200(
    fake_sleep: AsyncMock,
) -> None:
    """Pod stays RUNNING; HTTP returns 404 every time; deadline triggers TIMEOUT."""
    pod = _make_pod()
    client = AsyncMock(spec=RunpodClient)
    client.get_pod = AsyncMock(return_value=pod)  # always RUNNING
    http = _make_http(*[_make_response(404)] * 10)

    # Clock: 2-min timeout, jumps 60s per call after start
    # (start, deadline-check x3 → 0, 60, 120, then > 120 fails check → TIMEOUT)
    result = await wait_for_pod_ready(
        client,
        pod,
        http_client=http,
        clock=_make_clock(0.0, 0.0, 60.0, 120.0, 121.0),
        sleeper=fake_sleep,
        timeout_min=2,
        poll_interval_sec=30,
    )

    assert result.outcome is ProvisionOutcome.TIMEOUT
    assert "never returned 200" in result.detail
    assert pod.id in result.detail


@pytest.mark.asyncio
async def test_outcome_timeout_when_pod_pending_throughout(
    fake_sleep: AsyncMock,
) -> None:
    """Pod stays PENDING; HTTP errors; deadline triggers TIMEOUT."""
    pod = _make_pod()
    pending_pod = _make_pod(desired_status="PENDING")
    client = AsyncMock(spec=RunpodClient)
    client.get_pod = AsyncMock(return_value=pending_pod)
    http = _make_http(*[httpx.ConnectError("not bound") for _ in range(10)])

    result = await wait_for_pod_ready(
        client,
        pod,
        http_client=http,
        clock=_make_clock(0.0, 0.0, 60.0, 120.0, 121.0),
        sleeper=fake_sleep,
        timeout_min=2,
        poll_interval_sec=30,
    )

    assert result.outcome is ProvisionOutcome.TIMEOUT
```

- [ ] **Step 2: Run tests to verify they pass right away**

Run:
```bash
pytest tests/test_pod_provisioner.py::test_outcome_timeout_when_pod_running_but_http_never_200 tests/test_pod_provisioner.py::test_outcome_timeout_when_pod_pending_throughout -v
```

Expected: both PASS — the deadline return added in Task 2 already covers these. (If they fail, fix the deadline message text or clock-tick count, not the structure.)

If both pass on the first run, the TDD red phase is implicit: these tests assert the *contract* of the existing return; they would have failed if the deadline branch had been wrong from the start.

- [ ] **Step 3: Commit**

```bash
git add tests/test_pod_provisioner.py
git commit -m "$(cat <<'EOF'
test(runpod): lock in TIMEOUT outcome for RUNNING + PENDING pods

Adds two regression tests that pin the contract: when the readiness
deadline is reached and the pod is still RUNNING (HTTP never 200) or
still PENDING (never scheduled to RUN), wait_for_pod_ready returns
ProvisionOutcome.TIMEOUT with a detail naming the pod_id.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: RunpodApiError threshold — TIMEOUT after consecutive failures

**Goal:** If `client.get_pod()` raises `RunpodApiError` 3 times in a row, return `TIMEOUT` with an "API unreachable" detail. Fewer than 3 consecutive failures must reset the counter and keep polling.

**Files:**
- Modify: `app/services/block_m2_video/runpod/pod_provisioner.py`
- Test: `tests/test_pod_provisioner.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pod_provisioner.py`:

```python
@pytest.mark.asyncio
async def test_consecutive_runpod_api_failures_return_timeout(
    fake_sleep: AsyncMock,
) -> None:
    """3 consecutive RunpodApiError from get_pod → TIMEOUT with API-unreachable detail."""
    from app.services.block_m2_video.runpod.runpod_client import RunpodApiError

    pod = _make_pod()
    client = AsyncMock(spec=RunpodClient)
    client.get_pod = AsyncMock(
        side_effect=[
            RunpodApiError("HTTP 500"),
            RunpodApiError("HTTP 500"),
            RunpodApiError("HTTP 500"),
        ]
    )
    http = _make_http(*[_make_response(404)] * 3)

    result = await wait_for_pod_ready(
        client,
        pod,
        http_client=http,
        clock=_make_clock(0.0, 0.0, 30.0, 60.0, 90.0),
        sleeper=fake_sleep,
        timeout_min=25,
        poll_interval_sec=30,
    )

    assert result.outcome is ProvisionOutcome.TIMEOUT
    assert "api unreachable" in result.detail.lower()
    assert client.get_pod.await_count == 3


@pytest.mark.asyncio
async def test_transient_runpod_api_failures_below_threshold_recover(
    fake_sleep: AsyncMock,
) -> None:
    """2 failures then a success resets the counter; eventual 200 → READY."""
    from app.services.block_m2_video.runpod.runpod_client import RunpodApiError

    pod = _make_pod()
    client = AsyncMock(spec=RunpodClient)
    client.get_pod = AsyncMock(
        side_effect=[
            RunpodApiError("HTTP 500"),
            RunpodApiError("HTTP 502"),
            pod,  # recovered
            pod,  # still RUNNING
        ]
    )
    http = _make_http(
        _make_response(404),
        _make_response(404),
        _make_response(404),
        _make_response(200),
    )

    result = await wait_for_pod_ready(
        client,
        pod,
        http_client=http,
        clock=_make_clock(0.0, 0.0, 30.0, 60.0, 90.0, 120.0, 120.5),
        sleeper=fake_sleep,
        timeout_min=25,
        poll_interval_sec=30,
    )

    assert result.outcome is ProvisionOutcome.READY
    assert client.get_pod.await_count == 3
    assert http.get.await_count == 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
pytest tests/test_pod_provisioner.py::test_consecutive_runpod_api_failures_return_timeout tests/test_pod_provisioner.py::test_transient_runpod_api_failures_below_threshold_recover -v
```

Expected: at least `test_consecutive_runpod_api_failures_return_timeout` FAILS (current `except Exception` swallows the error indefinitely; never hits the threshold). The recovery test may pass accidentally but the threshold test won't.

- [ ] **Step 3: Replace the broad exception handler with a counter**

In `app/services/block_m2_video/runpod/pod_provisioner.py`, add the import at the top of the file:

```python
from .runpod_client import PodInfo, RunpodApiError, RunpodClient
```

Replace the pod-state check block from Task 3 with:

```python
            # 2. Pod-state check (precedence over continued HTTP 404)
            try:
                pod_now = await client.get_pod(pod.id)
                consecutive_api_failures = 0
            except RunpodApiError as exc:
                consecutive_api_failures += 1
                logger.debug(
                    "[provisioner] get_pod failed (%d/%d): %s",
                    consecutive_api_failures,
                    CONSECUTIVE_TRANSIENT_FAILURE_THRESHOLD,
                    exc,
                )
                if consecutive_api_failures >= CONSECUTIVE_TRANSIENT_FAILURE_THRESHOLD:
                    elapsed = _clock() - start
                    return ProvisionResult(
                        outcome=ProvisionOutcome.TIMEOUT,
                        pod_id=pod.id,
                        public_url=public_url,
                        elapsed_sec=elapsed,
                        detail=(
                            f"RunPod API unreachable for {consecutive_api_failures} "
                            f"consecutive polls — cannot determine pod state for "
                            f"{pod.id}"
                        ),
                    )
                pod_now = None

            if pod_now and (pod_now.desired_status or "").upper() == "EXITED":
                elapsed = _clock() - start
                return ProvisionResult(
                    outcome=ProvisionOutcome.CONTAINER_EXITED,
                    pod_id=pod.id,
                    public_url=public_url,
                    elapsed_sec=elapsed,
                    detail=(
                        f"Container exited {elapsed:.0f}s after spawn — "
                        f"check RunPod console logs for pod {pod.id}"
                    ),
                )
```

Initialize `consecutive_api_failures = 0` just **before** the `while _clock() < deadline:` loop:

```python
    consecutive_api_failures = 0

    try:
        while _clock() < deadline:
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/test_pod_provisioner.py -v
```

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_video/runpod/pod_provisioner.py tests/test_pod_provisioner.py
git commit -m "$(cat <<'EOF'
feat(runpod): consecutive-failure threshold for get_pod RunpodApiError

Replace the broad except-Exception placeholder from the previous
commit with a typed RunpodApiError handler that keeps a consecutive-
failure counter. CONSECUTIVE_TRANSIENT_FAILURE_THRESHOLD = 3 hits →
return TIMEOUT with an 'API unreachable' detail; any get_pod success
resets the counter so transient blips don't kill the loop.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Interrupted callable — SIGINT short-circuit

**Goal:** If the caller provides an `interrupted` callable and it returns `True`, the loop exits immediately with `outcome=TIMEOUT` and a detail marker that lets the sniper distinguish this from a normal timeout.

**Files:**
- Modify: `app/services/block_m2_video/runpod/pod_provisioner.py`
- Test: `tests/test_pod_provisioner.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_pod_provisioner.py`:

```python
SIGINT_DETAIL_MARKER = "SIGINT"  # also asserted in sniper tests later


@pytest.mark.asyncio
async def test_interrupted_short_circuits_loop(fake_sleep: AsyncMock) -> None:
    """interrupted() returning True mid-loop → TIMEOUT with SIGINT marker."""
    pod = _make_pod()
    client = AsyncMock(spec=RunpodClient)
    client.get_pod = AsyncMock(return_value=pod)  # always RUNNING
    http = _make_http(*[_make_response(404)] * 5)

    # interrupted: returns False on first call (loop entry), True on second.
    interrupted_calls = {"n": 0}

    def _interrupted() -> bool:
        interrupted_calls["n"] += 1
        return interrupted_calls["n"] >= 2

    result = await wait_for_pod_ready(
        client,
        pod,
        http_client=http,
        clock=_make_clock(0.0, 0.0, 30.0, 60.0),
        sleeper=fake_sleep,
        timeout_min=25,
        poll_interval_sec=30,
        interrupted=_interrupted,
    )

    assert result.outcome is ProvisionOutcome.TIMEOUT
    assert SIGINT_DETAIL_MARKER in result.detail
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pytest tests/test_pod_provisioner.py::test_interrupted_short_circuits_loop -v
```

Expected: FAIL — current loop doesn't consult `interrupted`.

- [ ] **Step 3: Add the interrupted check**

In `app/services/block_m2_video/runpod/pod_provisioner.py`, at the **top** of the `while _clock() < deadline:` loop body (before the HTTP probe):

```python
        while _clock() < deadline:
            if interrupted is not None and interrupted():
                elapsed = _clock() - start
                return ProvisionResult(
                    outcome=ProvisionOutcome.TIMEOUT,
                    pod_id=pod.id,
                    public_url=public_url,
                    elapsed_sec=elapsed,
                    detail=f"SIGINT during readiness wait for {pod.id}",
                )

            # 1. HTTP probe
            ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/test_pod_provisioner.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/block_m2_video/runpod/pod_provisioner.py tests/test_pod_provisioner.py
git commit -m "$(cat <<'EOF'
feat(runpod): interrupted callable lets caller short-circuit polling

When the optional interrupted=lambda: ... callable returns True at
the top of a poll iteration, wait_for_pod_ready returns TIMEOUT with
a 'SIGINT during readiness wait' detail marker. The sniper uses this
to distinguish a user-aborted readiness check from a real deadline
timeout and send the dedicated '🛑 readiness aborted' alert.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Sniper wiring — READY path + catch-alert tweak

**Goal:** After the sniper catches a pod, it calls `wait_for_pod_ready` and on `READY` sends a second Telegram alert. The existing catch alert gets a one-line tweak: append `"(waiting for ComfyUI bootstrap, will alert when ready)"`.

**Files:**
- Modify: `scripts/runpod_gpu_sniper.py`
- Test: `tests/test_runpod_gpu_sniper.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runpod_gpu_sniper.py` (after the existing imports, add):

```python
from app.services.block_m2_video.runpod.pod_provisioner import (
    ProvisionOutcome,
    ProvisionResult,
)
```

Then append a new test:

```python
@pytest.mark.asyncio
async def test_calls_provisioner_after_catch_and_alerts_ready(
    status_file: Path,
    fake_sleep: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After catch, sniper calls wait_for_pod_ready and sends a ✅ Pod ready alert."""
    pod = _make_pod()
    client = AsyncMock()
    client.start_pod = AsyncMock(return_value=pod)

    sent: list[str] = []
    monkeypatch.setattr(sniper, "send_alert", lambda text: sent.append(text) or True)

    async def _fake_wait_for_pod_ready(_client, _pod, **_kw):
        return ProvisionResult(
            outcome=ProvisionOutcome.READY,
            pod_id=_pod.id,
            public_url=f"https://{_pod.id}-8188.proxy.runpod.net",
            elapsed_sec=42.0,
            detail="ComfyUI ready in 42.0s",
        )

    monkeypatch.setattr(sniper, "wait_for_pod_ready", _fake_wait_for_pod_ready)

    rc = await sniper._snipe(
        max_duration_min=120,
        poll_interval_sec=20,
        notify=True,
        dry_run=False,
        config=_make_config(),
        client=client,
        sleeper=fake_sleep,
    )

    assert rc == 0
    # Two alerts: catch + ready
    assert len(sent) == 2
    assert "🎯" in sent[0]
    assert "waiting for ComfyUI bootstrap" in sent[0]
    assert "✅" in sent[1]
    assert "ready" in sent[1].lower()
    assert pod.id in sent[1]
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pytest tests/test_runpod_gpu_sniper.py::test_calls_provisioner_after_catch_and_alerts_ready -v
```

Expected: FAIL — `sniper.wait_for_pod_ready` does not exist yet (`AttributeError`).

- [ ] **Step 3: Wire the sniper to the provisioner**

In `scripts/runpod_gpu_sniper.py`, add the import block (after the existing `from app.services.block_m2_video.runpod.runpod_client import ...` and `from app.services.block_m2_video.runpod.runpod_config import ...`):

```python
from app.services.block_m2_video.runpod.pod_provisioner import (  # noqa: E402
    ProvisionOutcome,
    ProvisionResult,
    wait_for_pod_ready,
)
```

Find the existing catch-alert block (around lines 267-275). Replace the entire block (from `_safe_notify(` through `return 0`) with:

```python
            # Existing catch alert (with tweak — append the bootstrap-wait note)
            _safe_notify(
                (
                    f"🎯 RunPod sniper caught A100 in {cfg.datacenter}!\n"
                    f"pod_id: {pod_status['pod_id']}\n"
                    f"url: {pod_status['pod_public_url']}\n"
                    f"attempts: {attempt}\n"
                    f"(waiting for ComfyUI bootstrap, will alert when ready)"
                ),
                enabled=notify,
            )

            # Provisioning callback — passive readiness observation
            result = await wait_for_pod_ready(
                rp_client,
                pod,
                interrupted=lambda: _interrupted,
            )

            if result.outcome is ProvisionOutcome.READY:
                _safe_notify(
                    f"✅ Pod ready: {result.public_url}\n"
                    f"pod_id: {result.pod_id}\n"
                    f"{result.detail}",
                    enabled=notify,
                )
                return 0

            # Tasks 8-9 add CONTAINER_EXITED / TIMEOUT / SIGINT branches.
            return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/test_runpod_gpu_sniper.py -v
```

Expected: all existing sniper tests still PASS + the new `test_calls_provisioner_after_catch_and_alerts_ready` PASSES. If any pre-existing sniper test breaks because of the catch-alert text change, update its assertion to match (e.g., `"🎯" in status_text` rather than equality).

- [ ] **Step 5: Commit**

```bash
git add scripts/runpod_gpu_sniper.py tests/test_runpod_gpu_sniper.py
git commit -m "$(cat <<'EOF'
feat(runpod): sniper invokes pod_provisioner after catch (READY path)

After start_pod succeeds the sniper now calls wait_for_pod_ready,
appends a 'waiting for ComfyUI bootstrap' line to the existing
'🎯 caught' alert, and on ProvisionOutcome.READY sends a second
'✅ Pod ready' alert with the public URL. CONTAINER_EXITED and
TIMEOUT branches arrive in the next two commits.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Sniper failure outcomes — CONTAINER_EXITED + TIMEOUT alerts

**Goal:** When the provisioner returns `CONTAINER_EXITED` or `TIMEOUT`, the sniper fires the corresponding Telegram alert (❌ / ⏰).

**Files:**
- Modify: `scripts/runpod_gpu_sniper.py`
- Test: `tests/test_runpod_gpu_sniper.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runpod_gpu_sniper.py`:

```python
@pytest.mark.asyncio
async def test_alerts_container_exited(
    status_file: Path,
    fake_sleep: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CONTAINER_EXITED outcome → ❌ alert mentioning the pod_id."""
    pod = _make_pod()
    client = AsyncMock()
    client.start_pod = AsyncMock(return_value=pod)

    sent: list[str] = []
    monkeypatch.setattr(sniper, "send_alert", lambda text: sent.append(text) or True)

    async def _fake_wait(_c, _p, **_kw):
        return ProvisionResult(
            outcome=ProvisionOutcome.CONTAINER_EXITED,
            pod_id=_p.id,
            public_url=None,
            elapsed_sec=45.0,
            detail=f"Container exited 45s after spawn — check RunPod console logs for pod {_p.id}",
        )

    monkeypatch.setattr(sniper, "wait_for_pod_ready", _fake_wait)

    rc = await sniper._snipe(
        max_duration_min=120,
        poll_interval_sec=20,
        notify=True,
        dry_run=False,
        config=_make_config(),
        client=client,
        sleeper=fake_sleep,
    )

    assert rc == 0  # catch succeeded; provisioning failure is not a sniper-level error
    assert len(sent) == 2
    assert "❌" in sent[1]
    assert pod.id in sent[1]


@pytest.mark.asyncio
async def test_alerts_timeout(
    status_file: Path,
    fake_sleep: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TIMEOUT outcome → ⏰ alert mentioning the pod_id."""
    pod = _make_pod()
    client = AsyncMock()
    client.start_pod = AsyncMock(return_value=pod)

    sent: list[str] = []
    monkeypatch.setattr(sniper, "send_alert", lambda text: sent.append(text) or True)

    async def _fake_wait(_c, _p, **_kw):
        return ProvisionResult(
            outcome=ProvisionOutcome.TIMEOUT,
            pod_id=_p.id,
            public_url=f"https://{_p.id}-8188.proxy.runpod.net",
            elapsed_sec=1500.0,
            detail=f"Pod still RUNNING but /system_stats never returned 200 in 25 min — check web terminal for pod {_p.id}",
        )

    monkeypatch.setattr(sniper, "wait_for_pod_ready", _fake_wait)

    rc = await sniper._snipe(
        max_duration_min=120,
        poll_interval_sec=20,
        notify=True,
        dry_run=False,
        config=_make_config(),
        client=client,
        sleeper=fake_sleep,
    )

    assert rc == 0
    assert len(sent) == 2
    assert "⏰" in sent[1]
    assert pod.id in sent[1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
pytest tests/test_runpod_gpu_sniper.py::test_alerts_container_exited tests/test_runpod_gpu_sniper.py::test_alerts_timeout -v
```

Expected: both FAIL — current code only handles READY.

- [ ] **Step 3: Add CONTAINER_EXITED and TIMEOUT branches**

In `scripts/runpod_gpu_sniper.py`, replace the placeholder comment block from Task 7 (the `# Tasks 8-9 add CONTAINER_EXITED / TIMEOUT / SIGINT branches.` and trailing `return 0`) with:

```python
            if result.outcome is ProvisionOutcome.CONTAINER_EXITED:
                _safe_notify(
                    f"❌ Pod bootstrap failed\n"
                    f"pod_id: {result.pod_id}\n"
                    f"{result.detail}",
                    enabled=notify,
                )
                return 0

            if result.outcome is ProvisionOutcome.TIMEOUT:
                _safe_notify(
                    f"⏰ Pod unresponsive\n"
                    f"pod_id: {result.pod_id}\n"
                    f"{result.detail}",
                    enabled=notify,
                )
                return 0

            # Defensive — all three ProvisionOutcomes are handled above.
            logger.warning("[sniper] unexpected ProvisionOutcome: %s", result.outcome)
            return 0
```

(The Task 7 READY branch stays above this.)

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/test_runpod_gpu_sniper.py -v
```

Expected: all tests PASS (READY + CONTAINER_EXITED + TIMEOUT + all pre-existing).

- [ ] **Step 5: Commit**

```bash
git add scripts/runpod_gpu_sniper.py tests/test_runpod_gpu_sniper.py
git commit -m "$(cat <<'EOF'
feat(runpod): sniper alerts ❌ / ⏰ on CONTAINER_EXITED / TIMEOUT

CONTAINER_EXITED → '❌ Pod bootstrap failed' (pod_id + 'check RunPod
console logs' pointer). TIMEOUT → '⏰ Pod unresponsive' (pod_id +
'check web terminal'). Sniper exits 0 in both cases — catching the
pod was the snipe-level success; provisioning failure is a separate
operational signal.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Sniper SIGINT-during-readiness — 🛑 alert

**Goal:** When SIGINT fires while `wait_for_pod_ready` is running, the provisioner returns `TIMEOUT` with a `"SIGINT"` marker in the detail. The sniper detects this and sends the dedicated 🛑 alert instead of the regular ⏰ alert. The pod is NOT terminated.

**Files:**
- Modify: `scripts/runpod_gpu_sniper.py`
- Test: `tests/test_runpod_gpu_sniper.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runpod_gpu_sniper.py`:

```python
@pytest.mark.asyncio
async def test_sigint_during_readiness_sends_aborted_alert(
    status_file: Path,
    fake_sleep: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIGINT during wait_for_pod_ready → 🛑 'readiness aborted' alert, pod left alive."""
    pod = _make_pod()
    client = AsyncMock()
    client.start_pod = AsyncMock(return_value=pod)
    # The sniper must NOT call terminate_pod
    client.terminate_pod = AsyncMock()

    sent: list[str] = []
    monkeypatch.setattr(sniper, "send_alert", lambda text: sent.append(text) or True)

    async def _fake_wait(_c, _p, **_kw):
        # Simulate the provisioner returning TIMEOUT with the SIGINT marker
        # — the sniper differentiates by detail content, not by outcome.
        return ProvisionResult(
            outcome=ProvisionOutcome.TIMEOUT,
            pod_id=_p.id,
            public_url=f"https://{_p.id}-8188.proxy.runpod.net",
            elapsed_sec=120.0,
            detail=f"SIGINT during readiness wait for {_p.id}",
        )

    monkeypatch.setattr(sniper, "wait_for_pod_ready", _fake_wait)

    rc = await sniper._snipe(
        max_duration_min=120,
        poll_interval_sec=20,
        notify=True,
        dry_run=False,
        config=_make_config(),
        client=client,
        sleeper=fake_sleep,
    )

    assert rc == 0
    assert len(sent) == 2
    assert "🛑" in sent[1]
    assert "aborted" in sent[1].lower()
    assert pod.id in sent[1]
    client.terminate_pod.assert_not_awaited()
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pytest tests/test_runpod_gpu_sniper.py::test_sigint_during_readiness_sends_aborted_alert -v
```

Expected: FAIL — current code routes SIGINT-marker TIMEOUT through the regular ⏰ branch.

- [ ] **Step 3: Branch on SIGINT marker in the TIMEOUT path**

In `scripts/runpod_gpu_sniper.py`, modify the TIMEOUT branch added in Task 8 so it checks for the SIGINT marker **first**:

```python
            if result.outcome is ProvisionOutcome.TIMEOUT:
                if "SIGINT" in result.detail:
                    _safe_notify(
                        f"🛑 Pod caught but readiness check aborted by SIGINT\n"
                        f"pod_id: {result.pod_id}\n"
                        f"Pod still RUNNING — check manually",
                        enabled=notify,
                    )
                else:
                    _safe_notify(
                        f"⏰ Pod unresponsive\n"
                        f"pod_id: {result.pod_id}\n"
                        f"{result.detail}",
                        enabled=notify,
                    )
                return 0
```

(No new imports; no terminate_pod call.)

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/test_runpod_gpu_sniper.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/runpod_gpu_sniper.py tests/test_runpod_gpu_sniper.py
git commit -m "$(cat <<'EOF'
feat(runpod): sniper sends 🛑 'readiness aborted' on SIGINT mid-wait

When wait_for_pod_ready returns TIMEOUT with 'SIGINT' in result.detail
(set by the provisioner when its interrupted=lambda: _interrupted
callable trips), the sniper sends a dedicated alert and does NOT
terminate the pod — catching it was expensive and the user can
inspect or manually bootstrap it.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Status JSON extension — provisioning fields

**Goal:** Extend `state/runpod_sniper_status.json` with `provisioning_outcome`, `provisioning_detail`, `provisioning_elapsed_sec`, and `provisioning_completed_at` so external monitors see the final state. Additive only — existing fields untouched.

**Files:**
- Modify: `scripts/runpod_gpu_sniper.py`
- Test: `tests/test_runpod_gpu_sniper.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runpod_gpu_sniper.py`:

```python
@pytest.mark.asyncio
async def test_status_file_carries_provisioning_fields(
    status_file: Path,
    fake_sleep: AsyncMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After provisioner returns, status JSON has provisioning_* fields."""
    pod = _make_pod()
    client = AsyncMock()
    client.start_pod = AsyncMock(return_value=pod)

    async def _fake_wait(_c, _p, **_kw):
        return ProvisionResult(
            outcome=ProvisionOutcome.READY,
            pod_id=_p.id,
            public_url=f"https://{_p.id}-8188.proxy.runpod.net",
            elapsed_sec=42.0,
            detail="ComfyUI ready in 42.0s",
        )

    monkeypatch.setattr(sniper, "wait_for_pod_ready", _fake_wait)

    rc = await sniper._snipe(
        max_duration_min=120,
        poll_interval_sec=20,
        notify=False,
        dry_run=False,
        config=_make_config(),
        client=client,
        sleeper=fake_sleep,
    )

    assert rc == 0
    status = _read_status(status_file)
    # Existing fields preserved
    assert status["status"] == "caught"
    assert status["pod_id"] == pod.id
    # New provisioning fields
    assert status["provisioning_outcome"] == "ready"
    assert status["provisioning_detail"] == "ComfyUI ready in 42.0s"
    assert status["provisioning_elapsed_sec"] == 42.0
    assert "provisioning_completed_at" in status
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pytest tests/test_runpod_gpu_sniper.py::test_status_file_carries_provisioning_fields -v
```

Expected: FAIL — status file currently has no `provisioning_*` fields.

- [ ] **Step 3: Extend the status payload after readiness completes**

In `scripts/runpod_gpu_sniper.py`, add a helper near the existing `_pod_to_status` function. `ProvisionResult` is already imported via Task 7, so the type hint is unquoted:

```python
def _provisioning_status(result: ProvisionResult) -> dict[str, Any]:
    return {
        "provisioning_outcome": result.outcome.value,
        "provisioning_detail": result.detail,
        "provisioning_elapsed_sec": result.elapsed_sec,
        "provisioning_completed_at": _now_iso(),
    }
```

Then, in `_snipe()`, after `result = await wait_for_pod_ready(...)` and **before** the outcome-branching `if` chain (the READY check added in Task 7), write the extended status:

```python
            base_status.update(_provisioning_status(result))
            _write_status(base_status)
```

This way all three outcome branches benefit from a single status-write site.

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
pytest tests/test_runpod_gpu_sniper.py -v
```

Expected: all tests PASS, including the new status-fields test.

- [ ] **Step 5: Commit**

```bash
git add scripts/runpod_gpu_sniper.py tests/test_runpod_gpu_sniper.py
git commit -m "$(cat <<'EOF'
feat(runpod): status JSON carries provisioning_* fields post-readiness

Add provisioning_outcome / provisioning_detail / provisioning_elapsed
_sec / provisioning_completed_at to state/runpod_sniper_status.json
after wait_for_pod_ready returns, regardless of outcome. Additive —
existing fields and consumers are unaffected.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Create `scripts/remote/bootstrap_pod.sh`

**Goal:** Repo source-of-truth for the on-volume bootstrap. Versioned-manifest gate + import probe + ComfyUI exec. Not unit-tested (per spec § Testing).

**Files:**
- Create: `scripts/remote/bootstrap_pod.sh`

- [ ] **Step 1: Write the script**

Create `scripts/remote/bootstrap_pod.sh` with content:

```bash
#!/usr/bin/env bash
# scripts/remote/bootstrap_pod.sh
#
# Runs on the RunPod pod at container start (via the RunPod template's
# startCmd = `bash -lc '/workspace/bootstrap.sh'`). Repo is source of
# truth; the actual file at /workspace/bootstrap.sh is pasted onto the
# network volume once per environment via the RunPod web terminal.
#
# Contract (matches docs/superpowers/specs/2026-05-21-sniper-provisioning-
# callback-design.md):
#   - Idempotent: safe to run on every container boot.
#   - Versioned-manifest gate: BOOTSTRAP_VERSION constant + on-volume
#     /workspace/.bootstrap_version. Mismatch → slow install path.
#   - Import probe: ALWAYS runs, even on warm cache. Fails fast (exit 1)
#     with MISSING_MODULE log line that the human reads in RunPod
#     console logs.
#   - On success: exec python main.py so ComfyUI becomes PID 1; its
#     exit propagates as container exit → CONTAINER_EXITED to the
#     Python provisioner.

set -euo pipefail

BOOTSTRAP_VERSION="2026.05.21-001"
VOLUME_VERSION_FILE="/workspace/.bootstrap_version"
LOG_FILE="/workspace/.bootstrap_log"

# Mirror stdout/stderr to a log file on the volume for after-the-fact debug.
exec > >(tee -a "$LOG_FILE") 2>&1

echo "[bootstrap] start version=$BOOTSTRAP_VERSION at $(date -Is)"

# 1. Activate venv (created during first cold setup; lives on /workspace)
if [[ ! -f /workspace/venv/bin/activate ]]; then
    echo "[bootstrap] FATAL: /workspace/venv missing — initial setup not done"
    echo "MISSING_VENV=/workspace/venv"
    exit 1
fi
# shellcheck disable=SC1091
source /workspace/venv/bin/activate

# 2. Cache check
cached_version="$(cat "$VOLUME_VERSION_FILE" 2>/dev/null || echo "")"
if [[ "$cached_version" != "$BOOTSTRAP_VERSION" ]]; then
    echo "[bootstrap] version mismatch (cached='$cached_version' wanted='$BOOTSTRAP_VERSION') — running slow install"
    # Slow install path — refine these with the actual install commands
    # from yesterday's manual transcript when the human first builds the
    # on-volume copy. Examples:
    #   pip install -r /workspace/requirements.txt
    #   pip install "transformers<4.45"  # PyTorch 2.4 compat pin
    #   apt-get update && apt-get install -y ffmpeg
    #   git -C /workspace/ComfyUI/custom_nodes/comfyui-reactor-node pull
    echo "[bootstrap] (slow install steps run here — see comments above)"
else
    echo "[bootstrap] cache HIT — skipping slow install"
fi

# 3. Import probe (ALWAYS runs, even on warm cache)
echo "[bootstrap] running import probe"
python - <<'PY'
import importlib, sys

# Modules whose missing imports broke things in the 2026-05-20 manual fix.
# Extend this list as new failure modes surface.
MODULES = [
    "insightface",
    "segment_anything",
    "onnxruntime",
    "transformers",
]

failed = []
for m in MODULES:
    try:
        importlib.import_module(m)
    except Exception as e:
        failed.append(f"{m}: {type(e).__name__}: {e}")

if failed:
    sys.stderr.write("IMPORT_PROBE_FAILED:\n")
    for line in failed:
        sys.stderr.write(f"  MISSING_MODULE={line}\n")
    sys.exit(1)
print("[bootstrap] import probe OK")
PY

# 4. Mark version on success
echo "$BOOTSTRAP_VERSION" > "$VOLUME_VERSION_FILE"

# 5. Launch ComfyUI as PID 1
echo "[bootstrap] launching ComfyUI"
cd /workspace/ComfyUI
exec python main.py --listen 0.0.0.0 --port 8188
```

- [ ] **Step 2: Mark executable (informational)**

The file's `chmod +x` only matters when it's pasted onto the volume — the repo copy is read-only reference. Mention this in the script header (already done above) and in the commit message.

- [ ] **Step 3: Lint check (shellcheck if installed; skip if not)**

If `shellcheck` is on PATH, run:
```bash
shellcheck scripts/remote/bootstrap_pod.sh
```

Expected: no errors (the `# shellcheck disable=SC1091` line silences the source-not-followed warning for the dynamic venv activate).

If shellcheck is not installed, skip — the spec explicitly chose not to add a bash test harness for this script.

- [ ] **Step 4: Commit**

```bash
git add scripts/remote/bootstrap_pod.sh
git commit -m "$(cat <<'EOF'
feat(runpod): on-volume bootstrap script (repo source-of-truth)

Bash script paired with the RunPod template's startCmd. Versioned
manifest (/workspace/.bootstrap_version) gates the slow pip/apt
install path; the Python import probe ALWAYS runs and fails fast
with MISSING_MODULE log lines visible in the RunPod console. ComfyUI
is exec'd as PID 1 so its exit translates to a CONTAINER_EXITED
signal for the Python provisioner. Slow-install commands are
intentionally stubbed pending refinement from the 2026-05-20 manual
transcript at first deploy.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: Create `docs/runpod_template_config.md`

**Goal:** Backup documentation for the RunPod template, so it can be recreated in ~5 minutes from a clean account.

**Files:**
- Create: `docs/runpod_template_config.md`

- [ ] **Step 1: Write the doc**

Create `docs/runpod_template_config.md` with content:

````markdown
# RunPod Template Configuration

This template carries the `imageName` and `startCmd` for every pod the
sniper (`scripts/runpod_gpu_sniper.py`) catches and every pod
`RunpodClient.start_pod()` deploys. The template ID is set in
`.env.runpod` as `RUNPOD_TEMPLATE_ID`. If the template gets deleted in
the RunPod console, recreate it from the values below.

## Settings

| Field | Value |
|---|---|
| Template Name | `jarvis-i2v-comfyui-bootstrap` (or any name; ID is what matters) |
| Container Image | `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04` (or current `RUNPOD_DOCKER_IMAGE` value) |
| Container Start Command | `bash -lc '/workspace/bootstrap.sh'` |
| Expose HTTP Ports | `8188` |
| Expose TCP Ports | `22` |
| Container Disk | `50` GB (matches `start_pod()` default) |
| Volume Mount Path | `/workspace` |
| Env Variables | _(none — bootstrap.sh self-contains config)_ |

## Why each field

- **Image** — must match `RUNPOD_DOCKER_IMAGE` so `start_pod()` calls
  that DON'T pass a custom `image_name=` still pick up the template's
  image. (Currently no caller passes `image_name=`.)
- **Start Command** — runs the on-volume bootstrap script. The exact
  string is `bash -lc '/workspace/bootstrap.sh'` (note the single
  quotes — they let `-lc` pass the path as one arg).
- **Ports 8188/http** — ComfyUI's bind port. The HTTPS proxy at
  `https://<pod-id>-8188.proxy.runpod.net` is what `pod_provisioner`
  polls.
- **Ports 22/tcp** — kept for emergency manual SSH access during
  bootstrap debugging. Not used by the Python provisioner.
- **Volume mount `/workspace`** — must match `volume_mount_path` in
  `RunpodClient._deploy_pod()`. Required by RunPod whenever a network
  volume is attached.

## Recovery procedure

1. RunPod console → Templates → New Template.
2. Fill the fields above.
3. Save and copy the template ID.
4. Set `RUNPOD_TEMPLATE_ID=<new_id>` in `.env.runpod`.
5. Verify with `python scripts/runpod_gpu_sniper.py --dry-run` (dry-run
   path doesn't actually deploy a pod, but loads config — confirms the
   env var is read).
6. Run a real sniper catch and confirm the bootstrap log appears on the
   volume.

## Related files

- `scripts/remote/bootstrap_pod.sh` — repo source-of-truth for the
  on-volume script.
- `app/services/block_m2_video/runpod/runpod_config.py` —
  `RunpodConfig.template_id` field definition.
- `app/services/block_m2_video/runpod/runpod_client.py:392-393` — where
  `templateId` is passed into `podFindAndDeployOnDemand`.
````

- [ ] **Step 2: Commit**

```bash
git add docs/runpod_template_config.md
git commit -m "$(cat <<'EOF'
docs(runpod): template config backup for recovery

Documents the RunPod template the sniper relies on (imageName,
startCmd, ports, mount path) so it can be recreated in ~5 min from
a clean account if deleted. References scripts/remote/bootstrap_pod.sh
and the existing RUNPOD_TEMPLATE_ID wiring in runpod_client.py.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 13: Manual setup + end-to-end smoke test

**Goal:** With all code committed, do the one-time manual steps to activate the system, then run a real sniper to confirm end-to-end behavior.

**Files:** none — manual operational steps.

- [ ] **Step 1: Create the RunPod template**

In the RunPod console:
1. Templates → New Template.
2. Use settings from `docs/runpod_template_config.md`.
3. Save. Copy the template ID.

- [ ] **Step 2: Update `.env.runpod`**

Add or update:
```
RUNPOD_TEMPLATE_ID=<id-from-step-1>
```

Run dry-run to confirm the env var is loaded:
```bash
python scripts/runpod_gpu_sniper.py --dry-run --no-notify
```
Expected: log line includes `template_id=<id-from-step-1>` somewhere (or at minimum: no `RUNPOD_TEMPLATE_ID required` validation error).

- [ ] **Step 3: Upload bootstrap.sh to the network volume**

On any *currently-running* pod with the network volume attached (or spin one up via `python scripts/runpod_recreate_phase3.py` once), open the RunPod web terminal and:

```bash
cat > /workspace/bootstrap.sh <<'BOOTSTRAP_EOF'
<paste full contents of scripts/remote/bootstrap_pod.sh>
BOOTSTRAP_EOF
chmod +x /workspace/bootstrap.sh
```

Refine the "(slow install steps)" stub in `/workspace/bootstrap.sh` (and in the repo file via a follow-up commit) using the install transcript from the 2026-05-20 session: `pip install` lines for `transformers<4.45`, `insightface`, `segment_anything`, `onnxruntime`, plus any `apt-get install` lines (e.g., ffmpeg).

- [ ] **Step 4: Verify bootstrap.sh runs successfully on the current volume**

From the same web terminal:
```bash
bash /workspace/bootstrap.sh
```
Expected: import probe prints `[bootstrap] import probe OK`, then `python main.py` launches ComfyUI. Visit `https://<pod-id>-8188.proxy.runpod.net/system_stats` in a browser and confirm HTTP 200.

Stop or terminate that test pod before the next step.

- [ ] **Step 5: Real sniper smoke test**

```bash
python scripts/runpod_gpu_sniper.py
```

Expected Telegram messages, in order:
1. `🎯 RunPod sniper caught A100 in EU-RO-1!` (with `(waiting for ComfyUI bootstrap...)`).
2. `✅ Pod ready: https://<pod-id>-8188.proxy.runpod.net` within 5-10 minutes (or `❌` / `⏰` if something failed).

Expected status JSON (`state/runpod_sniper_status.json`):
```json
{
  "status": "caught",
  "provisioning_outcome": "ready",
  "provisioning_detail": "ComfyUI ready in 45.2s",
  ...
}
```

- [ ] **Step 6: Forced-failure smoke test**

Edit `/workspace/bootstrap.sh` import probe to include a non-existent module (e.g., add `"this_module_does_not_exist"` to `MODULES`). Bump `BOOTSTRAP_VERSION` to force cache miss. Snipe again.

Expected: `❌ Pod bootstrap failed` Telegram with the pod_id and "check RunPod console logs". The RunPod console log for that pod should show `MISSING_MODULE=this_module_does_not_exist: ModuleNotFoundError: ...`.

Roll back the probe edit on the volume; bump version again.

- [ ] **Step 7: Mark as complete**

No code commit. If smoke tests revealed bootstrap.sh refinements needed (real `pip install` lines, etc.), make those edits in `scripts/remote/bootstrap_pod.sh` (the repo file) and commit:

```bash
git add scripts/remote/bootstrap_pod.sh
git commit -m "$(cat <<'EOF'
fix(runpod): refine bootstrap.sh slow-install steps from smoke test

Real install transcript: <list the apt and pip commands actually used>.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-review checklist (run before handing off to execution)

This is for the *plan author* to verify against the spec. Run quickly with fresh eyes.

- [ ] **Spec coverage:** Walk each section of the spec doc and confirm the plan covers it:
  - Three layers (template + bootstrap.sh + provisioner) → Tasks 1-6, 11, 12.
  - Three ProvisionOutcomes → Tasks 2 (READY), 3 (CONTAINER_EXITED), 4 (TIMEOUT).
  - Transient error handling (HTTP + API threshold) → Tasks 2 (HTTP), 5 (API threshold).
  - SIGINT contract → Tasks 6 (provisioner side), 9 (sniper side).
  - Sniper wiring: catch alert tweak + three outcome alerts → Tasks 7-9.
  - Status JSON extension → Task 10.
  - bootstrap.sh: versioned manifest + import probe + exec ComfyUI → Task 11.
  - Template doc backup → Task 12.
  - Manual setup + smoke tests → Task 13.
- [ ] **Placeholder scan:** No "TBD", no "implement appropriate error handling", no "similar to Task N", no "write tests for the above" without code. Bootstrap's `(slow install steps run here — see comments above)` is the one stub — deliberately so, per spec § Components ("Exact dep list and install steps will be refined in the implementation plan from yesterday's manual install transcript"). Refinement is Task 13 step 3 + 7.
- [ ] **Type consistency:** `ProvisionOutcome`, `ProvisionResult`, `wait_for_pod_ready` names are identical across all tasks. Field names (`pod_id`, `public_url`, `elapsed_sec`, `detail`, `outcome`) are stable. Kwargs (`http_client`, `clock`, `sleeper`, `interrupted`, `timeout_min`, `poll_interval_sec`) match across function signature, test calls, and impl.
- [ ] **Commit chain:** Each task ends in a commit; the chain is linear and each commit's tests pass independently.

---

## Open follow-ups (NOT in this plan)

Per spec § Non-Goals — deferred to separate work:

- Tier 3 logs API: extend `RunpodClient` with a `get_pod_logs(pod_id)` method and have the sniper include the last N lines in `❌` / `⏰` Telegram messages.
- `runpod_recreate_phase3.py` upgrade to call `wait_for_pod_ready`.
- FaceSwapEngine pod discovery (tracker #45).
- Automated bootstrap.sh upload helper (`scripts/upload_bootstrap_to_volume.py`).
- env-configurable readiness knobs.
- Heartbeat alert at 10-min mark.
