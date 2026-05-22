# FaceSwapEngine #44 collision fix + `FACE_SWAP_KEEP_POD_RUNNING` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) Eliminate ComfyUI filename-collision-driven duplicate outputs (#44) by sending uuid4-prefixed multipart filenames from `_upload_image`. (2) Add a `FACE_SWAP_KEEP_POD_RUNNING` env var that, when truthy, skips the post-batch `stop_pod()` call.

**Architecture:** Inside `FaceSwapEngine._upload_image()` we compute `unique_name = f"{uuid.uuid4().hex}_{image_path.name}"` and send that as the multipart filename. ComfyUI saves under the unique name, eliminating the rename-on-collision path. The existing chain (`_upload_image → _swap_one → _build_workflow`) already uses the server-echoed name, so no callers change. A regression-lock test pins that chain. Independently, the `finally` block in `swap_batch()` now reads `os.environ["FACE_SWAP_KEEP_POD_RUNNING"]` (lenient truthy parsing: `.strip().lower() in {"1", "true"}`) and skips `stop_pod()` when set, logging the decision. Unset (default) preserves bit-for-bit current behavior.

**Tech Stack:** Python 3.11, `httpx`, `pytest` + `pytest-anyio`, `unittest.mock` (existing patterns), `uuid` stdlib.

**Spec:** `docs/superpowers/specs/2026-05-22-faceswap-collision-and-keep-pod-design.md`

---

## File Structure

| File | Role | Change |
|---|---|---|
| `app/services/block_m2_face_swap/face_swap_engine.py` | Face-swap engine implementation | Add `import uuid`; add `_KEEP_POD_RUNNING_ENV` constant; uuid-prefix the multipart filename in `_upload_image`; switch `_upload_image`'s fallback from `image_path.name` to `unique_name`; gate `stop_pod` call in `swap_batch`'s `finally` block on env var |
| `tests/test_swapbatch_face_swap_engine.py` | Unit tests | Add 5 new test cases plus `re` import |

No new files. No other files touched.

**Pre-state of the engine module (relevant lines, from current `main`):**

- Line 27: `import os` — **already imported** (added in #45)
- Line 28: `import time`
- Line 54: `_POD_NAME_PREFIX = "jarvis-m2-"`
- Line 55: `_EXPLICIT_POD_ENV = "FACE_SWAP_POD_ID"`
- Lines 205–216: `finally` block of `swap_batch` (the `stop_pod` call lives here)
- Lines 461–482: `_upload_image`

---

## Task 1: Regression-lock the chain — workflow uses server-returned name

**Why first:** The spec verified by code-read that the chain (`_upload_image → _swap_one → _build_workflow`) already uses the server-echoed name. We commit this guard test **before** any production-code change so it (a) protects against a future implementer accidentally using `image_path.name` in `_build_workflow`, and (b) proves green at HEAD so subsequent tasks don't accidentally regress it.

This is a characterization test, not a red/green TDD step — note it explicitly when announcing the task.

**Files:**
- Test: `tests/test_swapbatch_face_swap_engine.py` (append new test case)

### Step 1.1: Write the regression-lock test

- [ ] Append this test to `tests/test_swapbatch_face_swap_engine.py` at the end of the file:

```python
# ── #44 collision fix + keep-pod-running flag ────────────────────────────────


@pytest.mark.anyio
async def test_swap_batch_workflow_uses_server_returned_name_when_renamed(
    tmp_path,
):
    """If ComfyUI echoes a renamed filename, the workflow must use the echo.

    Locks in the existing chain: _upload_image returns payload['name'] which
    flows through _swap_one into _build_workflow as the node-2 image input.
    A future regression that uses image_path.name in _build_workflow would
    break this test.
    """
    src = _make_image(tmp_path, "src.jpg")
    t1 = _make_image(tmp_path, "t1.jpg")

    submitted_workflow: dict = {}
    renamed_target = "deadbeefcafebabe1234567890abcdef_t1 (2).jpg"

    async def fake_post(url, *args, **kwargs):
        if "/upload/image" in url:
            files = kwargs.get("files") or {}
            # Source upload echoes its own name; target upload returns the
            # renamed name to simulate a server-side collision rename.
            name_field = files.get("image", (None,))[0] or ""
            if "src" in name_field:
                return _json_response({"name": "src_echo.jpg"})
            return _json_response({"name": renamed_target})
        if "/prompt" in url:
            submitted_workflow.update(
                kwargs.get("json", {}).get("prompt", {})
            )
            return _json_response({"prompt_id": "p1"})
        return _json_response({})

    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.post = AsyncMock(side_effect=fake_post)
    http.get = AsyncMock(side_effect=[
        _json_response({"system": "ok"}),
        _json_response({"ReActorFaceSwap": {}}),
        _json_response(_success_history("p1", "swap_out.png")),
        _bytes_response(b"\x89PNG" + b"\x00" * 20_000),
    ])

    engine = FaceSwapEngine(
        config=_make_config(), client=_mock_runpod_client(),
        output_dir=tmp_path / "out", http_client=http,
        poll_interval_sec=0.0,
    )
    results = await engine.swap_batch(src, [t1])
    assert results[0] is not None
    assert submitted_workflow["2"]["inputs"]["image"] == renamed_target
    assert submitted_workflow["1"]["inputs"]["image"] == "src_echo.jpg"
```

### Step 1.2: Run the test, verify it passes (characterization test)

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py::test_swap_batch_workflow_uses_server_returned_name_when_renamed -v
```

Expected: PASS at HEAD. The chain is already correct — this test characterizes existing behavior so future regressions break it.

If the test FAILS at this step, stop and investigate before proceeding — the code-read assumption in the spec is wrong, which would change the rest of the plan.

### Step 1.3: Run the full test file, verify no regressions

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py -v
```

Expected: All tests pass (previously passing + 1 new = N+1 passing).

### Step 1.4: Commit

- [ ] Run:

```
git add tests/test_swapbatch_face_swap_engine.py
git commit -m "test(face-swap): lock in workflow uses server-returned upload name (#44)"
```

---

## Task 2: uuid-prefix the multipart filename in `_upload_image` (Issue #44 fix)

**Files:**
- Modify: `app/services/block_m2_face_swap/face_swap_engine.py` (insert `import uuid`; rewrite `_upload_image` body)
- Modify: `tests/test_swapbatch_face_swap_engine.py` (add `import re`; append 2 new tests)

### Step 2.1: Add the `re` import to the test file

- [ ] Edit `tests/test_swapbatch_face_swap_engine.py`. Locate line 5:

```python
import json
```

Insert `import re` immediately after it, so the top of the file reads:

```python
import json
import re
from pathlib import Path
```

(Keeps stdlib imports alphabetical.)

### Step 2.2: Write the failing test for the uuid-prefixed multipart filename

- [ ] Append this test to `tests/test_swapbatch_face_swap_engine.py` (after the test added in Task 1):

```python
@pytest.mark.anyio
async def test_upload_image_sends_uuid_prefixed_filename(tmp_path):
    """_upload_image must send a uuid4-prefixed filename, not image_path.name.

    Prevents ComfyUI from renaming on collisions in the persistent
    /workspace/ComfyUI/input/ directory. Format: <32 hex chars>_<original>.
    """
    src = _make_image(tmp_path, "src.jpg")

    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.post = AsyncMock(return_value=_json_response({"name": "src.jpg"}))

    engine = FaceSwapEngine(
        config=_make_config(), client=_mock_runpod_client(),
        output_dir=tmp_path / "out", http_client=http,
    )
    await engine._upload_image("http://test:8188", src)

    files = http.post.call_args.kwargs["files"]
    sent_filename = files["image"][0]
    assert re.match(r"^[0-9a-f]{32}_src\.jpg$", sent_filename), (
        f"expected uuid-prefixed filename, got: {sent_filename!r}"
    )
```

### Step 2.3: Run the test, verify it fails

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py::test_upload_image_sends_uuid_prefixed_filename -v
```

Expected: FAIL — `assert re.match(...)` returns `None` because the current code sends `image_path.name = "src.jpg"`, not a uuid-prefixed name.

### Step 2.4: Add `import uuid` to the engine module

- [ ] Edit `app/services/block_m2_face_swap/face_swap_engine.py`. Locate the stdlib import block (lines 23–31):

```python
import asyncio
import copy
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
```

Insert `import uuid` after `import time`:

```python
import asyncio
import copy
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
```

(Keeps stdlib imports alphabetical: `time` < `uuid` < `datetime` is fine because `from`-imports form a separate block below.)

### Step 2.5: Rewrite `_upload_image` to send the uuid-prefixed name

- [ ] Edit `app/services/block_m2_face_swap/face_swap_engine.py`. Locate `_upload_image` (lines 461–482):

```python
    async def _upload_image(self, pod_url: str, image_path: Path) -> str:
        http = self._get_http()
        with image_path.open("rb") as fh:
            files = {"image": (image_path.name, fh, "application/octet-stream")}
            data = {"type": "input"}
            r = await http.post(
                f"{pod_url}/upload/image",
                files=files,
                data=data,
                timeout=_UPLOAD_TIMEOUT,
            )
        if r.status_code >= 400:
            raise FaceSwapError(
                f"/upload/image HTTP {r.status_code}: {r.text}"
            )
        try:
            payload = r.json()
        except ValueError as exc:
            raise FaceSwapError(f"/upload/image non-JSON: {exc}") from exc
        uploaded = payload.get("name") or image_path.name
        logger.info("FaceSwapEngine: uploaded %s -> %s", image_path.name, uploaded)
        return str(uploaded)
```

Replace it with:

```python
    async def _upload_image(self, pod_url: str, image_path: Path) -> str:
        http = self._get_http()
        unique_name = f"{uuid.uuid4().hex}_{image_path.name}"
        with image_path.open("rb") as fh:
            files = {"image": (unique_name, fh, "application/octet-stream")}
            data = {"type": "input"}
            r = await http.post(
                f"{pod_url}/upload/image",
                files=files,
                data=data,
                timeout=_UPLOAD_TIMEOUT,
            )
        if r.status_code >= 400:
            raise FaceSwapError(
                f"/upload/image HTTP {r.status_code}: {r.text}"
            )
        try:
            payload = r.json()
        except ValueError as exc:
            raise FaceSwapError(f"/upload/image non-JSON: {exc}") from exc
        uploaded = payload.get("name") or unique_name
        logger.info(
            "FaceSwapEngine: uploaded %s as %s -> %s",
            image_path.name, unique_name, uploaded,
        )
        return str(uploaded)
```

Two functional changes vs. the original:
1. The multipart filename sent to the server is `unique_name`, not `image_path.name`.
2. The fallback when the server's `"name"` field is empty/missing is now `unique_name`, not `image_path.name`. (If we send a unique name and the server doesn't echo, we still must not regress to the original name — that would re-introduce the collision risk.)

The log line gets an extra `%s` for the unique name so operators can correlate sent vs. received in production logs.

### Step 2.6: Run the test, verify it passes

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py::test_upload_image_sends_uuid_prefixed_filename -v
```

Expected: PASS.

### Step 2.7: Add the second test — uniqueness across two calls

- [ ] Append this test to `tests/test_swapbatch_face_swap_engine.py` (after the previous test):

```python
@pytest.mark.anyio
async def test_upload_image_two_calls_same_path_produce_different_sent_names(
    tmp_path,
):
    """Two uploads of the same source file must send different multipart
    filenames. Ensures uniqueness is per-call, not per-path."""
    src = _make_image(tmp_path, "src.jpg")

    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.post = AsyncMock(return_value=_json_response({"name": "src.jpg"}))

    engine = FaceSwapEngine(
        config=_make_config(), client=_mock_runpod_client(),
        output_dir=tmp_path / "out", http_client=http,
    )
    await engine._upload_image("http://test:8188", src)
    await engine._upload_image("http://test:8188", src)

    sent_names = [
        call.kwargs["files"]["image"][0]
        for call in http.post.call_args_list
    ]
    assert len(sent_names) == 2
    assert sent_names[0] != sent_names[1], (
        f"both uploads sent identical filename: {sent_names[0]!r}"
    )
```

### Step 2.8: Run the new test, verify it passes

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py::test_upload_image_two_calls_same_path_produce_different_sent_names -v
```

Expected: PASS — `uuid.uuid4().hex` returns a fresh 32-hex-char value per call.

### Step 2.9: Run the full test file, verify no regressions

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py -v
```

Expected: All tests pass. Of particular interest:
- `test_swap_batch_reuses_pod_and_uploads_source_once` — uses `{"name": "src.jpg"}` mock responses; the engine's chain still works (server's response is what's used in the workflow).
- `test_swap_batch_workflow_uses_server_returned_name_when_renamed` (Task 1) — still passes.

### Step 2.10: Commit

- [ ] Run:

```
git add app/services/block_m2_face_swap/face_swap_engine.py tests/test_swapbatch_face_swap_engine.py
git commit -m "fix(face-swap): uuid-prefix uploads to prevent ComfyUI rename collisions (#44)"
```

---

## Task 3: `FACE_SWAP_KEEP_POD_RUNNING` env var to skip post-batch `stop_pod`

**Files:**
- Modify: `app/services/block_m2_face_swap/face_swap_engine.py` (add `_KEEP_POD_RUNNING_ENV` constant; gate `stop_pod` call in `swap_batch`'s `finally` block)
- Modify: `tests/test_swapbatch_face_swap_engine.py` (append 2 new tests)

### Step 3.1: Write the failing test for the `"1"` truthy value

- [ ] Append this test to `tests/test_swapbatch_face_swap_engine.py`:

```python
@pytest.mark.anyio
async def test_swap_batch_skips_stop_pod_when_keep_running_env_set(
    tmp_path, monkeypatch,
):
    """FACE_SWAP_KEEP_POD_RUNNING=1 → stop_pod is not called."""
    monkeypatch.setenv("FACE_SWAP_KEEP_POD_RUNNING", "1")

    src = _make_image(tmp_path, "src.jpg")
    t1 = _make_image(tmp_path, "t1.jpg")

    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.get = AsyncMock(side_effect=[
        _json_response({"system": "ok"}),
        _json_response({"ReActorFaceSwap": {}}),
        _json_response(_success_history("p1", "swap_out.png")),
        _bytes_response(b"\x89PNG" + b"\x00" * 20_000),
    ])
    http.post = AsyncMock(side_effect=[
        _json_response({"name": "src.jpg"}),
        _json_response({"name": "t1.jpg"}),
        _json_response({"prompt_id": "p1"}),
    ])

    client = _mock_runpod_client()
    engine = FaceSwapEngine(
        config=_make_config(), client=client,
        output_dir=tmp_path / "out", http_client=http,
        poll_interval_sec=0.0,
    )
    results = await engine.swap_batch(src, [t1])

    assert results[0] is not None  # batch still succeeds
    client.stop_pod.assert_not_awaited()
```

### Step 3.2: Run the test, verify it fails

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py::test_swap_batch_skips_stop_pod_when_keep_running_env_set -v
```

Expected: FAIL on `client.stop_pod.assert_not_awaited()` — current code unconditionally calls `stop_pod` in the `finally` block.

### Step 3.3: Add the `_KEEP_POD_RUNNING_ENV` constant

- [ ] Edit `app/services/block_m2_face_swap/face_swap_engine.py`. Locate line 55:

```python
_EXPLICIT_POD_ENV = "FACE_SWAP_POD_ID"  # set in .env (NOT .env.runpod — that file is read only by pydantic-settings and never reaches os.environ)
```

Immediately after it, insert:

```python
_KEEP_POD_RUNNING_ENV = "FACE_SWAP_KEEP_POD_RUNNING"  # truthy values: "1" or "true" (case-insensitive, stripped). Skips post-batch stop_pod. Same .env caveat as _EXPLICIT_POD_ENV.
```

### Step 3.4: Gate the `stop_pod` call in the `finally` block

- [ ] Edit `app/services/block_m2_face_swap/face_swap_engine.py`. Locate the `finally` block in `swap_batch` (lines 205–216):

```python
        finally:
            if pod_id is not None:
                try:
                    await client.stop_pod(pod_id)
                    logger.info(
                        "FaceSwapEngine: stopped pod %s after batch", pod_id
                    )
                except Exception as exc:  # noqa: BLE001 - cleanup
                    logger.warning(
                        "FaceSwapEngine: stop_pod(%s) failed: %s", pod_id, exc
                    )
            await self._maybe_close()
```

Replace it with:

```python
        finally:
            if pod_id is not None:
                keep = os.environ.get(_KEEP_POD_RUNNING_ENV, "").strip().lower()
                if keep in {"1", "true"}:
                    logger.info(
                        "FaceSwapEngine: %s=%s; leaving pod %s running",
                        _KEEP_POD_RUNNING_ENV, keep, pod_id,
                    )
                else:
                    try:
                        await client.stop_pod(pod_id)
                        logger.info(
                            "FaceSwapEngine: stopped pod %s after batch",
                            pod_id,
                        )
                    except Exception as exc:  # noqa: BLE001 - cleanup
                        logger.warning(
                            "FaceSwapEngine: stop_pod(%s) failed: %s",
                            pod_id, exc,
                        )
            await self._maybe_close()
```

Notes:
- `_maybe_close()` continues to run unconditionally — local HTTP/RunpodClient cleanup is independent of pod lifecycle.
- `os.environ.get(..., "")` then `.strip().lower()` is the lenient parser the spec calls for.
- Empty/unset env var → `keep == ""` → falls through to the `else` (current behavior).

### Step 3.5: Run the test, verify it passes

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py::test_swap_batch_skips_stop_pod_when_keep_running_env_set -v
```

Expected: PASS.

### Step 3.6: Add the case-insensitive test

- [ ] Append this test to `tests/test_swapbatch_face_swap_engine.py`:

```python
@pytest.mark.anyio
async def test_swap_batch_keep_running_accepts_true_case_insensitive(
    tmp_path, monkeypatch,
):
    """FACE_SWAP_KEEP_POD_RUNNING parsing is lenient: "TRUE" with whitespace
    must be honored just like "1"."""
    monkeypatch.setenv("FACE_SWAP_KEEP_POD_RUNNING", "  TRUE  ")

    src = _make_image(tmp_path, "src.jpg")
    t1 = _make_image(tmp_path, "t1.jpg")

    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock()
    http.get = AsyncMock(side_effect=[
        _json_response({"system": "ok"}),
        _json_response({"ReActorFaceSwap": {}}),
        _json_response(_success_history("p1", "swap_out.png")),
        _bytes_response(b"\x89PNG" + b"\x00" * 20_000),
    ])
    http.post = AsyncMock(side_effect=[
        _json_response({"name": "src.jpg"}),
        _json_response({"name": "t1.jpg"}),
        _json_response({"prompt_id": "p1"}),
    ])

    client = _mock_runpod_client()
    engine = FaceSwapEngine(
        config=_make_config(), client=client,
        output_dir=tmp_path / "out", http_client=http,
        poll_interval_sec=0.0,
    )
    await engine.swap_batch(src, [t1])

    client.stop_pod.assert_not_awaited()
```

### Step 3.7: Run the new test, verify it passes

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py::test_swap_batch_keep_running_accepts_true_case_insensitive -v
```

Expected: PASS — `.strip().lower()` produces `"true"`, which is in `{"1", "true"}`.

### Step 3.8: Run the full test file, verify no regressions

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py -v
```

Expected: All tests pass — in particular:
- `test_swap_batch_reuses_pod_and_uploads_source_once` (env unset → `stop_pod` IS called) — still passes.
- `test_swap_batch_stops_pod_even_when_no_targets_succeed` (env unset → `stop_pod` still called on the failure path) — still passes.

### Step 3.9: Commit

- [ ] Run:

```
git add app/services/block_m2_face_swap/face_swap_engine.py tests/test_swapbatch_face_swap_engine.py
git commit -m "feat(face-swap): FACE_SWAP_KEEP_POD_RUNNING skips post-batch stop_pod"
```

---

## Final verification

### Step F.1: Inspect the final state of `_upload_image`

- [ ] Read `app/services/block_m2_face_swap/face_swap_engine.py` lines ~461–485. The function should now read:

```python
    async def _upload_image(self, pod_url: str, image_path: Path) -> str:
        http = self._get_http()
        unique_name = f"{uuid.uuid4().hex}_{image_path.name}"
        with image_path.open("rb") as fh:
            files = {"image": (unique_name, fh, "application/octet-stream")}
            data = {"type": "input"}
            r = await http.post(
                f"{pod_url}/upload/image",
                files=files,
                data=data,
                timeout=_UPLOAD_TIMEOUT,
            )
        if r.status_code >= 400:
            raise FaceSwapError(
                f"/upload/image HTTP {r.status_code}: {r.text}"
            )
        try:
            payload = r.json()
        except ValueError as exc:
            raise FaceSwapError(f"/upload/image non-JSON: {exc}") from exc
        uploaded = payload.get("name") or unique_name
        logger.info(
            "FaceSwapEngine: uploaded %s as %s -> %s",
            image_path.name, unique_name, uploaded,
        )
        return str(uploaded)
```

### Step F.2: Inspect the final state of the `swap_batch` finally block

- [ ] Read `app/services/block_m2_face_swap/face_swap_engine.py` around the `finally` block in `swap_batch`. It should now read:

```python
        finally:
            if pod_id is not None:
                keep = os.environ.get(_KEEP_POD_RUNNING_ENV, "").strip().lower()
                if keep in {"1", "true"}:
                    logger.info(
                        "FaceSwapEngine: %s=%s; leaving pod %s running",
                        _KEEP_POD_RUNNING_ENV, keep, pod_id,
                    )
                else:
                    try:
                        await client.stop_pod(pod_id)
                        logger.info(
                            "FaceSwapEngine: stopped pod %s after batch",
                            pod_id,
                        )
                    except Exception as exc:  # noqa: BLE001 - cleanup
                        logger.warning(
                            "FaceSwapEngine: stop_pod(%s) failed: %s",
                            pod_id, exc,
                        )
            await self._maybe_close()
```

### Step F.3: Run the full file one more time

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py -v
```

Expected: All tests pass (existing tests + 5 new).

### Step F.4: Run the broader face-swap test surface for regressions

- [ ] Run:

```
python -m pytest tests/test_swapbatch_face_swap_engine.py tests/test_face_swap.py -v
```

Expected: All tests pass. If `tests/test_face_swap.py` does not exist, the command will report no items for it — that's fine; the swapbatch file is the relevant target. (Yesterday's #45 plan referenced the same dual command.)

### Step F.5: Verify commit history

- [ ] Run:

```
git log main..HEAD --oneline -- app/services/block_m2_face_swap/face_swap_engine.py tests/test_swapbatch_face_swap_engine.py
```

Expected: 3 new commits in this order from oldest to newest:

```
test(face-swap): lock in workflow uses server-returned upload name (#44)
fix(face-swap): uuid-prefix uploads to prevent ComfyUI rename collisions (#44)
feat(face-swap): FACE_SWAP_KEEP_POD_RUNNING skips post-batch stop_pod
```

### Step F.6: Diff-size sanity check

- [ ] Run:

```
git diff main -- app/services/block_m2_face_swap/face_swap_engine.py | grep -E "^[+-]" | grep -v "^[+-][+-][+-]" | wc -l
```

Expected: roughly 18–28 lines (engine module). The spec estimated +12/−3 net.

```
git diff main -- tests/test_swapbatch_face_swap_engine.py | grep -E "^[+-]" | grep -v "^[+-][+-][+-]" | wc -l
```

Expected: roughly 140–200 lines (5 new tests + 1 new import).

---

## Notes for the implementer

- **`uuid.uuid4().hex`** returns a lowercase 32-character hex string (e.g. `"a1b2c3d4..."`). It has 122 bits of entropy — collision probability per pair of uploads is ~1 in 2¹²². No retry-on-collision needed.
- **The chain is already correct.** `_upload_image` returns the server-echoed name; `_swap_one` (line 625) passes that to `_build_workflow`; source path (line 167) does the same. Don't "fix" the chain — you'll break Task 1's regression test.
- **Don't touch `RunpodConfig`, `RunpodClient`, or the workflow JSON.** Out of scope per the spec.
- **`monkeypatch.setenv`** is auto-cleaned per-test by pytest, so env vars don't leak between tests.
- **Existing tests use `{"name": "src.jpg"}` mock responses.** Because the engine uses `payload.get("name") or unique_name`, and the mock returns `"src.jpg"`, the engine returns `"src.jpg"` for the rest of the chain — existing assertions about workflow contents (e.g. `test_build_workflow_injects_source_and_target_filenames`) still pass without modification.
- **`@pytest.mark.anyio`** in this file uses asyncio backend by default — no `anyio_backend` fixture needed.
- **Don't reorder existing test functions.** Append all new tests at the end of the file under the `# ── #44 collision fix + keep-pod-running flag ───` header introduced in Task 1.
- **Log line cardinality.** The new log line in `_upload_image` includes `unique_name`, which is high-cardinality. If you have a log-aggregation system that buckets by message template, this is fine (the template is fixed); if it buckets by full text, expect many unique lines — that's intended for forensic debugging.
