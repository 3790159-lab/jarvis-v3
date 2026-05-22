# FaceSwapEngine: filename-collision fix (#44) and `FACE_SWAP_KEEP_POD_RUNNING` override

**Issues:** #44 (duplicate outputs), and a new keep-pod-running flag (no issue number yet)
**Date:** 2026-05-22
**Scope:** small refactor — one source file, one test file

## Problem

### #44 — duplicate face-swap outputs

`FaceSwapEngine` runs N swaps on a persistent RunPod ComfyUI pod whose `/workspace/ComfyUI/input/` directory survives across sessions (it lives on the network volume).

When `_upload_image()` POSTs a target image to `/upload/image`, ComfyUI saves the file as `image_path.name`. If a file of the same name already exists from a previous session, ComfyUI silently renames the new upload to `"<stem> (2).<ext>"` and returns the renamed name in the response payload's `"name"` field.

Forensic evidence from 2026-05-21 outputs: 10 of 22 outputs paired up into 5 size-clusters (e.g. 921313+921233, 1146622+1146565 bytes). The pattern recurred the next day. The explanation: half of the targets were never the *intended* targets — they were stale `<name>.<ext>` files from previous sessions, and the freshly-uploaded `<name> (2).<ext>` files were ignored because the workflow was being submitted against the old name.

**Verification of the existing chain** (done while reading the code, not in the original issue brief): `_upload_image` already returns the server-echoed name (`payload.get("name") or image_path.name`, line 480), and `_swap_one` already passes that return value into `_build_workflow` (lines 167 and 625). The chain is correct as-is. The bug is that we let ComfyUI rename in the first place.

### Keep-pod-running flag

`swap_batch()`'s `finally` block always calls `client.stop_pod(pod_id)`. For development workflow where pod cold-start ("sniper catch") takes 5–90 minutes, this is wasteful when an operator wants to run several batches in quick succession against the same warm pod.

We want an env-var-gated opt-out, defaulting to current auto-stop behavior.

## Goals

1. Eliminate filename collisions on the persistent ComfyUI input directory — once and for all, server-side rename should never occur for our uploads.
2. The fix must be invisible to existing callers: `_upload_image()`'s return contract is unchanged (it still returns the server-echoed name).
3. Add a regression-lock test proving that if the server *does* rename (despite uniqueness, or via some other server quirk), the workflow uses the server-returned name — not the original `image_path.name`.
4. New env var `FACE_SWAP_KEEP_POD_RUNNING` lets an operator skip the post-batch `stop_pod()` call.
5. When unset (default) → current auto-stop behavior is bit-for-bit unchanged.
6. When set to a recognised truthy value, the engine logs the decision and skips `stop_pod()`. HTTP client and RunpodClient cleanup (`_maybe_close`) still run unconditionally.

## Non-goals

- No changes to `RunpodConfig`, `RunpodClient`, or the workflow JSON.
- No changes to the existing `_find_or_start_pod` explicit-pod logic from #45.
- No mass-cleanup of `/workspace/ComfyUI/input/` on the pod — that's a separate housekeeping concern.
- No change to `_swap_one`'s call sites or signature.
- No retry-on-collision logic — uuid4 collisions have ~122 bits of entropy; not worth handling.

## Design

### Change 1: `_upload_image` — send a uuid-prefixed multipart filename

**File:** `app/services/block_m2_face_swap/face_swap_engine.py`

**Added imports:**

```python
import uuid  # next to existing stdlib imports
```

**Modified method:**

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

Two changes vs. the current code:
1. Multipart filename sent to the server is `unique_name`, not `image_path.name`.
2. Fallback when the server's `"name"` field is empty/missing is also `unique_name`, not `image_path.name`. If we send a unique name and the server doesn't echo one back, we should still use the unique name we sent — not the original which would re-introduce the collision risk.

The return contract (server-echoed name or our fallback, as a `str`) is unchanged. `_swap_one` already threads this return value through to `_build_workflow` correctly.

**Rationale for uuid4.hex (32 chars) as the prefix:**
Compact, alphanumeric (`/upload/image` is safe with it), and astronomically collision-resistant. Keeping the original `image_path.name` as a suffix preserves the suffix for ComfyUI's internal type-sniffing and keeps forensic debuggability — an operator inspecting `/workspace/ComfyUI/input/` can still recognise which original file each upload came from.

**Both source and target benefit:** `_upload_image` is the single method used for both the once-per-batch source upload (line 167) and the per-target uploads inside `_swap_one` (line 625). Adding the prefix inside `_upload_image` covers both with no call-site changes.

### Change 2: `swap_batch` finally block — env-gated stop

**File:** same.

**Added constant:**

```python
_KEEP_POD_RUNNING_ENV = "FACE_SWAP_KEEP_POD_RUNNING"  # near other env-var constants
```

**Modified `finally` block in `swap_batch`:**

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
                    "FaceSwapEngine: stopped pod %s after batch", pod_id
                )
            except Exception as exc:  # noqa: BLE001 - cleanup
                logger.warning(
                    "FaceSwapEngine: stop_pod(%s) failed: %s", pod_id, exc
                )
    await self._maybe_close()
```

`_maybe_close()` continues to run unconditionally — keeping the pod alive does **not** mean keeping our owned HTTP client / RunpodClient open for some hypothetical next call. Subsequent invocations of `swap_batch` instantiate their own resources.

**Truthy parsing (lenient):** `.strip().lower() in {"1", "true"}`. Matches `"1"`, `"true"`, `"True"`, `"TRUE"`, `" true "`, etc. Anything else (including unset/empty) preserves current auto-stop behavior. This is friendlier to humans editing `.env` than strict literal matching, and consistent with how `FACE_SWAP_POD_ID` from #45 treats its value as a non-empty string rather than a strict literal.

### Env var loading

For `FACE_SWAP_KEEP_POD_RUNNING` to reach `os.environ` in production:

- **Recommended:** add `FACE_SWAP_KEEP_POD_RUNNING=1` to `.env` at the project root. `app.core.env_bootstrap.bootstrap_env()` (called from `app/main.py`) does `load_dotenv(.env)`, which loads it into `os.environ`.
- **Alternative:** shell-export it before launching the bot. Useful for ad-hoc dev sessions without touching `.env`.

`os.environ.get(...)` is read inside the `finally` block, not cached at `__init__` time — same pattern as `FACE_SWAP_POD_ID` in `_find_or_start_pod`, lets tests monkey-patch the env between calls and lets an operator toggle behavior between batches without restarting the process.

**Do not put it in `.env.runpod`** — same caveat as `FACE_SWAP_POD_ID`: that file is read only by `pydantic-settings` inside `RunpodConfig` and never reaches `os.environ`.

### Tests: `tests/test_swapbatch_face_swap_engine.py`

Five new test cases. All use existing helpers (`_make_image`, `_make_config`, `_mock_runpod_client`, `_json_response`, `_bytes_response`, `_success_history`) and existing mocking patterns. No new fixtures, no new dependencies.

| # | Test name | Purpose | Key assertions |
|---|---|---|---|
| 1 | `test_upload_image_sends_uuid_prefixed_filename` | Direct unit test of `_upload_image`. Stub `http.post` to return `{"name": "echoed.jpg"}`. Call `_upload_image(pod_url, src.jpg)`. | Inspect `http.post.call_args.kwargs["files"]["image"][0]` (the multipart filename) — assert it matches regex `^[0-9a-f]{32}_src\.jpg$`. Confirms we send a uuid-prefixed name to the server. |
| 2 | `test_swap_batch_workflow_uses_server_returned_name_when_renamed` | Regression lock on the chain `_upload_image → _swap_one → _build_workflow`. Server echoes a renamed name like `"abc...def_t1 (2).jpg"` for the target upload. Capture the workflow submitted to `/prompt` via the `submitted_workflow` pattern already used in `test_swap_batch_uses_reactor_opt_fallback_when_primary_missing`. | `submitted_workflow["2"]["inputs"]["image"] == "<the renamed name>"`. If a future implementer breaks the chain by using `image_path.name` in `_build_workflow`, this test catches it. |
| 3 | `test_upload_image_two_calls_same_path_produce_different_sent_names` | Two sequential calls to `_upload_image` with the same source path. | The two captured multipart filenames differ. Locks in the per-call uniqueness property. |
| 4 | `test_swap_batch_skips_stop_pod_when_keep_running_env_set` | `monkeypatch.setenv("FACE_SWAP_KEEP_POD_RUNNING", "1")`. Run a normal happy-path batch (mirroring `test_swap_batch_reuses_pod_and_uploads_source_once` but with one target). | `client.stop_pod.assert_not_awaited()`. Batch completes without exception (proving the `finally` block — including `_maybe_close` — still runs cleanly). |
| 5 | `test_swap_batch_keep_running_accepts_true_case_insensitive` | `monkeypatch.setenv("FACE_SWAP_KEEP_POD_RUNNING", "TRUE")` (or `" True "`). Same batch shape as #4. | Same: `client.stop_pod.assert_not_awaited()`. Locks in the lenient parsing decision. |

**Existing tests preserved:** `test_swap_batch_reuses_pod_and_uploads_source_once`, `test_swap_batch_stops_pod_even_when_no_targets_succeed`, etc., all use mock `/upload/image` responses like `{"name": "src.jpg"}`. Because the engine's chain uses `payload.get("name") or unique_name`, returning `"src.jpg"` from the mock still causes `_upload_image` to return `"src.jpg"`, and the rest of the test sees no behavioral change. Existing assertions about `stop_pod` being awaited continue to pass because `FACE_SWAP_KEEP_POD_RUNNING` is unset in those tests.

## File-by-file change summary

| File | Change | Approx. lines |
|---|---|---|
| `app/services/block_m2_face_swap/face_swap_engine.py` | `import uuid`; add `_KEEP_POD_RUNNING_ENV` constant; uuid-prefix the multipart filename inside `_upload_image`; switch `_upload_image`'s fallback from `image_path.name` to `unique_name`; gate `stop_pod` call in `swap_batch`'s `finally` block | +12 / −3 lines |
| `tests/test_swapbatch_face_swap_engine.py` | Add 5 new test cases | +130–180 lines |

No other files modified. No new files created.

## Risks & mitigations

- **Risk:** ComfyUI's `/upload/image` strips or rejects the uuid prefix on some unknown server quirk. **Mitigation:** the chain uses the server's returned name, so even if ComfyUI normalises our filename, we use whatever it gives back. Test #2 covers this scenario explicitly.
- **Risk:** uuid prefix makes `/workspace/ComfyUI/input/` accumulate stale files forever. **Mitigation:** acknowledged — separate housekeeping concern (cron / periodic clean-up). Not in scope for #44.
- **Risk:** `_maybe_close` running while the pod stays alive somehow corrupts the warm pod. **Mitigation:** `_maybe_close` only closes our local HTTP client and RunpodClient SDK — both are stateless w.r.t. the pod itself. The pod doesn't know or care.
- **Risk:** truthy parsing too lenient — operator types `"yes"` and is surprised it's not recognised. **Mitigation:** documented in the module-level constant comment that only `"1"` and `"true"` (case-insensitive) are honored.
- **Risk:** existing logs grep for the exact string `"stopped pod %s after batch"` and break when keep-running is on. **Mitigation:** no automated log scraping known in this repo for that string; the new "leaving pod %s running" log is just as greppable.

## Out of scope / future work

- Housekeeping cron to prune `/workspace/ComfyUI/input/` periodically.
- Exposing `FACE_SWAP_KEEP_POD_RUNNING` via `RunpodConfig` (would require touching that file).
- Pod-warmth heuristics (e.g. auto-stop after N minutes idle even if the env var is set). The env var is intentionally a blunt operator-controlled switch for now.
