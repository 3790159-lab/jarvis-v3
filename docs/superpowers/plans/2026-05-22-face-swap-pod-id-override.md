# FACE_SWAP_POD_ID Override Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit pod-id env var (`FACE_SWAP_POD_ID`) so `FaceSwapEngine` can pin to a specific RunPod pod instead of relying on the `jarvis-m2-` name prefix.

**Architecture:** A new branch inside `FaceSwapEngine._find_or_start_pod()` reads `os.environ["FACE_SWAP_POD_ID"]`. If set and the pod is found via the already-fetched `list_pods()` result, the branch returns it directly (RUNNING) or resumes it (STOPPED/EXITED). Misses and unexpected statuses log a warning and fall through to the existing prefix-search code unchanged. A `RunpodApiError` during resume is wrapped in `FaceSwapError` rather than swallowed, so real API failures stay visible.

**Tech Stack:** Python 3.11, `httpx`, `pytest` + `pytest-anyio`, `unittest.mock` (existing patterns).

**Spec:** `docs/superpowers/specs/2026-05-22-face-swap-pod-id-override-design.md`

---

## File Structure

| File | Role | Change |
|---|---|---|
| `app/services/block_m2_face_swap/face_swap_engine.py` | Face-swap engine implementation | Add `import os`, add `_EXPLICIT_POD_ENV` constant, insert explicit-pod branch in `_find_or_start_pod()` |
| `tests/test_swapbatch_face_swap_engine.py` | Unit tests for `FaceSwapEngine` | Add `RunpodApiError` import, add 4 new test cases |

No new files. No other files touched.

---

## Task 1: Explicit pod RUNNING → use directly

**Files:**
- Modify: `app/services/block_m2_face_swap/face_swap_engine.py` (lines 21-46 imports, line 53 constants, lines 261-275 `_find_or_start_pod`)
- Test: `tests/test_swapbatch_face_swap_engine.py` (append new test at end of file)

### Step 1.1: Write the failing test

- [ ] Append this test to `tests/test_swapbatch_face_swap_engine.py`:

```python
# ── explicit pod override (FACE_SWAP_POD_ID) ────────────────────────────────


@pytest.mark.anyio
async def test_find_or_start_pod_uses_explicit_pod_when_running(
    tmp_path, monkeypatch,
):
    """FACE_SWAP_POD_ID set + pod RUNNING → engine returns it directly."""
    monkeypatch.setenv("FACE_SWAP_POD_ID", "pod_explicit")

    explicit_pod = PodInfo.model_construct(
        id="pod_explicit", name="some-arbitrary-name",
        desired_status="RUNNING", cost_per_hr=1.59,
    )
    # Include a jarvis-m2-* candidate too; the explicit branch must win.
    prefix_pod = PodInfo.model_construct(
        id="pod_other", name="jarvis-m2-other",
        desired_status="RUNNING", cost_per_hr=1.59,
    )
    client = _mock_runpod_client(pods=[prefix_pod, explicit_pod])

    engine = FaceSwapEngine(
        config=_make_config(), client=client,
        output_dir=tmp_path / "out",
    )
    pod, pod_id, reused = await engine._find_or_start_pod(client)

    assert pod_id == "pod_explicit"
    assert reused is True
    client.resume_pod.assert_not_awaited()
    client.start_pod.assert_not_awaited()
    client.wait_for_ready.assert_not_awaited()
```

### Step 1.2: Run the test, verify it fails

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py::test_find_or_start_pod_uses_explicit_pod_when_running -v
```

Expected: FAIL with `assert pod_id == "pod_explicit"` — the existing code reaches the prefix-search loop and selects `pod_other` (or whichever prefix candidate sorts first), not `pod_explicit`.

### Step 1.3: Add `import os` to the engine module

- [ ] Edit `app/services/block_m2_face_swap/face_swap_engine.py`. After `import logging` on line 26, insert `import os`:

```python
import json
import logging
import os
import time
```

(Insert `import os` so the standard-library imports stay alphabetical.)

### Step 1.4: Add the `_EXPLICIT_POD_ENV` constant

- [ ] Edit `app/services/block_m2_face_swap/face_swap_engine.py`. Immediately after line 53 (`_POD_NAME_PREFIX = "jarvis-m2-"  # share pods with Phase B (same ComfyUI image)`), add:

```python
_EXPLICIT_POD_ENV = "FACE_SWAP_POD_ID"  # set in .env (NOT .env.runpod — that file is read only by pydantic-settings and never reaches os.environ)
```

### Step 1.5: Add the env-read + RUNNING branch

- [ ] Edit `app/services/block_m2_face_swap/face_swap_engine.py`. In `_find_or_start_pod()`, immediately after the `try: pods = await client.list_pods() except RunpodApiError ...` block (after line 267) and before the line `candidates = [...]` (line 269), insert:

```python
        explicit_id = os.environ.get(_EXPLICIT_POD_ENV)
        if explicit_id:
            explicit = next(
                (p for p in pods if p.id == explicit_id), None
            )
            if explicit is not None:
                status = (explicit.desired_status or "").upper()
                if status == "RUNNING":
                    logger.info(
                        "FaceSwapEngine: using explicit pod %s (RUNNING)",
                        explicit_id,
                    )
                    return explicit, explicit.id, True
```

After this edit, the relevant section of `_find_or_start_pod` reads:

```python
        try:
            pods = await client.list_pods()
        except RunpodApiError as exc:
            raise FaceSwapError(f"list_pods failed: {exc}") from exc

        explicit_id = os.environ.get(_EXPLICIT_POD_ENV)
        if explicit_id:
            explicit = next(
                (p for p in pods if p.id == explicit_id), None
            )
            if explicit is not None:
                status = (explicit.desired_status or "").upper()
                if status == "RUNNING":
                    logger.info(
                        "FaceSwapEngine: using explicit pod %s (RUNNING)",
                        explicit_id,
                    )
                    return explicit, explicit.id, True

        candidates = [
            p for p in pods if (p.name or "").startswith(_POD_NAME_PREFIX)
        ]
```

### Step 1.6: Run the test, verify it passes

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py::test_find_or_start_pod_uses_explicit_pod_when_running -v
```

Expected: PASS.

### Step 1.7: Run the full test file, verify no regressions

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py -v
```

Expected: All tests pass (previously passing tests are untouched; one new test passes).

### Step 1.8: Commit

- [ ] Run:

```
git add app/services/block_m2_face_swap/face_swap_engine.py tests/test_swapbatch_face_swap_engine.py
git commit -m "feat(face-swap): honor FACE_SWAP_POD_ID for RUNNING pods (#45)"
```

---

## Task 2: Explicit pod STOPPED/EXITED → resume

**Files:**
- Modify: `app/services/block_m2_face_swap/face_swap_engine.py` (extend the new explicit branch in `_find_or_start_pod`)
- Test: `tests/test_swapbatch_face_swap_engine.py` (append new test)

### Step 2.1: Write the failing test

- [ ] Append this test to `tests/test_swapbatch_face_swap_engine.py` (after the Task 1 test):

```python
@pytest.mark.anyio
async def test_find_or_start_pod_resumes_explicit_pod_when_stopped(
    tmp_path, monkeypatch,
):
    """FACE_SWAP_POD_ID set + pod STOPPED → engine resumes it."""
    monkeypatch.setenv("FACE_SWAP_POD_ID", "pod_explicit")

    stopped_pod = PodInfo.model_construct(
        id="pod_explicit", name="some-arbitrary-name",
        desired_status="STOPPED", cost_per_hr=1.59,
    )
    resumed_pod = PodInfo.model_construct(
        id="pod_explicit", name="some-arbitrary-name",
        desired_status="RUNNING", cost_per_hr=1.59,
    )
    client = _mock_runpod_client(pods=[stopped_pod])
    client.resume_pod = AsyncMock(return_value=resumed_pod)
    client.wait_for_ready = AsyncMock(return_value=resumed_pod)

    engine = FaceSwapEngine(
        config=_make_config(), client=client,
        output_dir=tmp_path / "out",
    )
    pod, pod_id, reused = await engine._find_or_start_pod(client)

    assert pod_id == "pod_explicit"
    assert reused is False
    client.resume_pod.assert_awaited_once_with("pod_explicit")
    client.wait_for_ready.assert_awaited_once()
    client.start_pod.assert_not_awaited()
```

### Step 2.2: Run the test, verify it fails

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py::test_find_or_start_pod_resumes_explicit_pod_when_stopped -v
```

Expected: FAIL — without a STOPPED branch the explicit pod falls through to the prefix-search loop. With no `jarvis-m2-*` pods in `list_pods`, the engine reaches the `start_pod` spawn path, but `client.start_pod` returns `_mock_pod()` (id `pod_abc`, RUNNING), so the function returns `("pod_abc", ...)` rather than `("pod_explicit", ...)` — the `assert pod_id == "pod_explicit"` fires. Also `resume_pod.assert_awaited_once_with("pod_explicit")` would fail because nothing called it.

### Step 2.3: Add the STOPPED/EXITED branch

- [ ] Edit `app/services/block_m2_face_swap/face_swap_engine.py`. Inside the `if explicit is not None:` block, immediately after the `if status == "RUNNING":` return, add the STOPPED/EXITED branch:

Locate this code (added in Task 1):

```python
            if explicit is not None:
                status = (explicit.desired_status or "").upper()
                if status == "RUNNING":
                    logger.info(
                        "FaceSwapEngine: using explicit pod %s (RUNNING)",
                        explicit_id,
                    )
                    return explicit, explicit.id, True
```

Replace it with:

```python
            if explicit is not None:
                status = (explicit.desired_status or "").upper()
                if status == "RUNNING":
                    logger.info(
                        "FaceSwapEngine: using explicit pod %s (RUNNING)",
                        explicit_id,
                    )
                    return explicit, explicit.id, True
                if status in {"STOPPED", "EXITED"}:
                    logger.info(
                        "FaceSwapEngine: resuming explicit pod %s (%s)",
                        explicit_id, status,
                    )
                    resumed = await client.resume_pod(explicit_id)
                    ready = await client.wait_for_ready(
                        resumed.id,
                        timeout_sec=self._pod_ready_timeout_sec,
                    )
                    return ready, ready.id, False
```

### Step 2.4: Run the test, verify it passes

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py::test_find_or_start_pod_resumes_explicit_pod_when_stopped -v
```

Expected: PASS.

### Step 2.5: Run the full test file

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py -v
```

Expected: All tests pass.

### Step 2.6: Commit

- [ ] Run:

```
git add app/services/block_m2_face_swap/face_swap_engine.py tests/test_swapbatch_face_swap_engine.py
git commit -m "feat(face-swap): resume explicit FACE_SWAP_POD_ID pod when stopped (#45)"
```

---

## Task 3: Explicit pod not in list / unexpected status → fall back with warning

**Files:**
- Modify: `app/services/block_m2_face_swap/face_swap_engine.py` (add warning logs for both fallback paths)
- Test: `tests/test_swapbatch_face_swap_engine.py` (append new test)

### Step 3.1: Write the failing test

- [ ] Append this test to `tests/test_swapbatch_face_swap_engine.py`:

```python
@pytest.mark.anyio
async def test_find_or_start_pod_falls_back_to_prefix_when_explicit_missing(
    tmp_path, monkeypatch, caplog,
):
    """FACE_SWAP_POD_ID set but id not in list_pods → fall back, warn."""
    monkeypatch.setenv("FACE_SWAP_POD_ID", "pod_does_not_exist")

    prefix_pod = PodInfo.model_construct(
        id="pod_prefix", name="jarvis-m2-other",
        desired_status="RUNNING", cost_per_hr=1.59,
    )
    client = _mock_runpod_client(pods=[prefix_pod])

    engine = FaceSwapEngine(
        config=_make_config(), client=client,
        output_dir=tmp_path / "out",
    )
    with caplog.at_level("WARNING"):
        pod, pod_id, reused = await engine._find_or_start_pod(client)

    assert pod_id == "pod_prefix"  # prefix-search winner
    assert reused is True
    assert "pod_does_not_exist" in caplog.text
    assert "falling back to prefix search" in caplog.text
    client.resume_pod.assert_not_awaited()
    client.start_pod.assert_not_awaited()
```

### Step 3.2: Run the test, verify it fails

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py::test_find_or_start_pod_falls_back_to_prefix_when_explicit_missing -v
```

Expected: FAIL on `assert "pod_does_not_exist" in caplog.text` — after Tasks 1 and 2 the engine silently falls through (correct behavior, but no warning is emitted yet).

### Step 3.3: Add warning logs for the two fallback paths

- [ ] Edit `app/services/block_m2_face_swap/face_swap_engine.py`. Locate this section (added in Tasks 1 and 2):

```python
        explicit_id = os.environ.get(_EXPLICIT_POD_ENV)
        if explicit_id:
            explicit = next(
                (p for p in pods if p.id == explicit_id), None
            )
            if explicit is not None:
                status = (explicit.desired_status or "").upper()
                if status == "RUNNING":
                    logger.info(
                        "FaceSwapEngine: using explicit pod %s (RUNNING)",
                        explicit_id,
                    )
                    return explicit, explicit.id, True
                if status in {"STOPPED", "EXITED"}:
                    logger.info(
                        "FaceSwapEngine: resuming explicit pod %s (%s)",
                        explicit_id, status,
                    )
                    resumed = await client.resume_pod(explicit_id)
                    ready = await client.wait_for_ready(
                        resumed.id,
                        timeout_sec=self._pod_ready_timeout_sec,
                    )
                    return ready, ready.id, False
```

Replace it with:

```python
        explicit_id = os.environ.get(_EXPLICIT_POD_ENV)
        if explicit_id:
            explicit = next(
                (p for p in pods if p.id == explicit_id), None
            )
            if explicit is None:
                logger.warning(
                    "FaceSwapEngine: FACE_SWAP_POD_ID=%s not in list_pods; "
                    "falling back to prefix search",
                    explicit_id,
                )
            else:
                status = (explicit.desired_status or "").upper()
                if status == "RUNNING":
                    logger.info(
                        "FaceSwapEngine: using explicit pod %s (RUNNING)",
                        explicit_id,
                    )
                    return explicit, explicit.id, True
                if status in {"STOPPED", "EXITED"}:
                    logger.info(
                        "FaceSwapEngine: resuming explicit pod %s (%s)",
                        explicit_id, status,
                    )
                    resumed = await client.resume_pod(explicit_id)
                    ready = await client.wait_for_ready(
                        resumed.id,
                        timeout_sec=self._pod_ready_timeout_sec,
                    )
                    return ready, ready.id, False
                logger.warning(
                    "FaceSwapEngine: explicit pod %s in unexpected status "
                    "%s; falling back to prefix search",
                    explicit_id, status,
                )
```

(Two changes: convert `if explicit is not None:` into an `if explicit is None: warn; else: ...` and append a trailing `logger.warning` after the status checks for the "unexpected status" case.)

### Step 3.4: Run the test, verify it passes

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py::test_find_or_start_pod_falls_back_to_prefix_when_explicit_missing -v
```

Expected: PASS.

### Step 3.5: Run the full test file

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py -v
```

Expected: All tests pass.

### Step 3.6: Commit

- [ ] Run:

```
git add app/services/block_m2_face_swap/face_swap_engine.py tests/test_swapbatch_face_swap_engine.py
git commit -m "feat(face-swap): warn and fall back when FACE_SWAP_POD_ID misses (#45)"
```

---

## Task 4: Resume API errors → FaceSwapError (no fallback)

**Files:**
- Modify: `app/services/block_m2_face_swap/face_swap_engine.py` (wrap `resume_pod` + `wait_for_ready` in try/except)
- Modify: `tests/test_swapbatch_face_swap_engine.py` (add `RunpodApiError` import + new test)

### Step 4.1: Add `RunpodApiError` import to the test file

- [ ] Edit `tests/test_swapbatch_face_swap_engine.py`. Locate line 17:

```python
from app.services.block_m2_video.runpod.runpod_client import PodInfo
```

Replace with:

```python
from app.services.block_m2_video.runpod.runpod_client import PodInfo, RunpodApiError
```

### Step 4.2: Write the failing test

- [ ] Append this test to `tests/test_swapbatch_face_swap_engine.py`:

```python
@pytest.mark.anyio
async def test_find_or_start_pod_explicit_resume_api_error_raises_face_swap_error(
    tmp_path, monkeypatch,
):
    """FACE_SWAP_POD_ID set + resume_pod raises RunpodApiError → FaceSwapError.

    Real RunPod API failures during resume must NOT silently fall back to
    prefix search — they signal a real problem (quota, supply, network) that
    should surface to the caller.
    """
    monkeypatch.setenv("FACE_SWAP_POD_ID", "pod_explicit")

    stopped_pod = PodInfo.model_construct(
        id="pod_explicit", name="some-arbitrary-name",
        desired_status="STOPPED", cost_per_hr=1.59,
    )
    # Include a prefix pod to prove the engine does NOT fall back to it.
    prefix_pod = PodInfo.model_construct(
        id="pod_prefix", name="jarvis-m2-other",
        desired_status="RUNNING", cost_per_hr=1.59,
    )
    client = _mock_runpod_client(pods=[stopped_pod, prefix_pod])
    client.resume_pod = AsyncMock(side_effect=RunpodApiError("quota exceeded"))

    engine = FaceSwapEngine(
        config=_make_config(), client=client,
        output_dir=tmp_path / "out",
    )
    with pytest.raises(
        FaceSwapError,
        match="resume of explicit pod pod_explicit failed",
    ):
        await engine._find_or_start_pod(client)

    client.start_pod.assert_not_awaited()
```

### Step 4.3: Run the test, verify it fails

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py::test_find_or_start_pod_explicit_resume_api_error_raises_face_swap_error -v
```

Expected: FAIL — `RunpodApiError` propagates raw out of `_find_or_start_pod` (not wrapped in `FaceSwapError`), so `pytest.raises(FaceSwapError, ...)` doesn't match.

### Step 4.4: Wrap the resume call in try/except

- [ ] Edit `app/services/block_m2_face_swap/face_swap_engine.py`. Locate this section (added in Tasks 2 and 3):

```python
                if status in {"STOPPED", "EXITED"}:
                    logger.info(
                        "FaceSwapEngine: resuming explicit pod %s (%s)",
                        explicit_id, status,
                    )
                    resumed = await client.resume_pod(explicit_id)
                    ready = await client.wait_for_ready(
                        resumed.id,
                        timeout_sec=self._pod_ready_timeout_sec,
                    )
                    return ready, ready.id, False
```

Replace with:

```python
                if status in {"STOPPED", "EXITED"}:
                    logger.info(
                        "FaceSwapEngine: resuming explicit pod %s (%s)",
                        explicit_id, status,
                    )
                    try:
                        resumed = await client.resume_pod(explicit_id)
                        ready = await client.wait_for_ready(
                            resumed.id,
                            timeout_sec=self._pod_ready_timeout_sec,
                        )
                    except RunpodApiError as exc:
                        raise FaceSwapError(
                            f"resume of explicit pod {explicit_id} failed: "
                            f"{exc}"
                        ) from exc
                    return ready, ready.id, False
```

### Step 4.5: Run the test, verify it passes

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py::test_find_or_start_pod_explicit_resume_api_error_raises_face_swap_error -v
```

Expected: PASS.

### Step 4.6: Run the full test file

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py -v
```

Expected: All tests pass (8 pre-existing + 4 new = 12 passing).

### Step 4.7: Run the broader face-swap test suite for regressions

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py tests/test_face_swap.py -v
```

Expected: All tests pass.

### Step 4.8: Commit

- [ ] Run:

```
git add app/services/block_m2_face_swap/face_swap_engine.py tests/test_swapbatch_face_swap_engine.py
git commit -m "feat(face-swap): surface RunpodApiError on explicit-pod resume (#45)"
```

---

## Final verification

### Step F.1: Verify the final state of `_find_or_start_pod`

- [ ] Read `app/services/block_m2_face_swap/face_swap_engine.py` lines ~261-300. The function should now look like:

```python
    async def _find_or_start_pod(
        self, client: RunpodClient
    ) -> tuple[PodInfo, str, bool]:
        try:
            pods = await client.list_pods()
        except RunpodApiError as exc:
            raise FaceSwapError(f"list_pods failed: {exc}") from exc

        explicit_id = os.environ.get(_EXPLICIT_POD_ENV)
        if explicit_id:
            explicit = next(
                (p for p in pods if p.id == explicit_id), None
            )
            if explicit is None:
                logger.warning(
                    "FaceSwapEngine: FACE_SWAP_POD_ID=%s not in list_pods; "
                    "falling back to prefix search",
                    explicit_id,
                )
            else:
                status = (explicit.desired_status or "").upper()
                if status == "RUNNING":
                    logger.info(
                        "FaceSwapEngine: using explicit pod %s (RUNNING)",
                        explicit_id,
                    )
                    return explicit, explicit.id, True
                if status in {"STOPPED", "EXITED"}:
                    logger.info(
                        "FaceSwapEngine: resuming explicit pod %s (%s)",
                        explicit_id, status,
                    )
                    try:
                        resumed = await client.resume_pod(explicit_id)
                        ready = await client.wait_for_ready(
                            resumed.id,
                            timeout_sec=self._pod_ready_timeout_sec,
                        )
                    except RunpodApiError as exc:
                        raise FaceSwapError(
                            f"resume of explicit pod {explicit_id} failed: "
                            f"{exc}"
                        ) from exc
                    return ready, ready.id, False
                logger.warning(
                    "FaceSwapEngine: explicit pod %s in unexpected status "
                    "%s; falling back to prefix search",
                    explicit_id, status,
                )

        candidates = [
            p for p in pods if (p.name or "").startswith(_POD_NAME_PREFIX)
        ]
        # ... existing prefix-search code continues unchanged ...
```

### Step F.2: Verify the diff size matches the spec

- [ ] Run:

```
git diff main -- app/services/block_m2_face_swap/face_swap_engine.py | grep -E "^[+-]" | grep -v "^[+-][+-][+-]" | wc -l
```

Expected: roughly 30-40 lines (added: ~28 net inserts; spec estimated ~22, slight expansion is fine because each Task introduces a few logger calls and the try/except adds 5 lines).

### Step F.3: Verify commit history

- [ ] Run:

```
git log main..HEAD --oneline -- app/services/block_m2_face_swap/face_swap_engine.py tests/test_swapbatch_face_swap_engine.py
```

Expected: 4 new commits, each titled `feat(face-swap): ... (#45)`.

---

## Notes for the implementer

- **No `.env.runpod` change.** `FACE_SWAP_POD_ID` is intentionally read from `os.environ` directly. The user (or `app.core.env_bootstrap.bootstrap_env()` via `.env`) is responsible for putting the value into the process environment. Do not modify `RunpodConfig`.
- **`PodInfo.model_construct(...)`** bypasses pydantic validation, which is the correct pattern in this test file (see existing `_mock_pod()` helper).
- **`monkeypatch.setenv`** is auto-cleaned after each test by pytest, so the explicit env var won't leak into other tests.
- **`caplog`** in Task 3 catches all log records by default; `caplog.at_level("WARNING")` makes the assertion intent explicit.
- **`@pytest.mark.anyio`** uses asyncio by default — no `anyio_backend` fixture needed; existing tests in this file rely on the same default.
- **Don't reorder existing test functions.** Append new tests at the end of the file under a new `# ── explicit pod override ───` section header (introduced in Task 1).
