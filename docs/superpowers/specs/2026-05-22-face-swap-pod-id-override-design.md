# FaceSwapEngine pod discovery via `FACE_SWAP_POD_ID` override

**Issue:** #45
**Date:** 2026-05-22
**Scope:** small refactor — one source file, one test file

## Problem

`FaceSwapEngine._find_or_start_pod()` discovers RunPod pods by scanning `list_pods()` for entries whose `name` starts with `jarvis-m2-`. On 2026-05-21 a usable pod (`jarvis-p32-...`) had to be manually renamed to `jarvis-m2-swap-manual` solely so the engine would find it. That rename was a workaround, not a fix.

We want a way to point `FaceSwapEngine` at a specific pod by id, without changing the existing prefix-search behavior for users who don't set the override.

## Goals

1. New env var `FACE_SWAP_POD_ID` lets an operator pin `FaceSwapEngine` to a specific pod id.
2. When the override is set and the pod is usable (RUNNING or resumable from STOPPED/EXITED), the engine uses it instead of the prefix search.
3. When the override is set but the pod can't be located or is in an unexpected state, the engine logs a warning and falls back to the existing prefix-search path.
4. When the override is set, the pod is found in STOPPED/EXITED, and the RunPod API errors during resume — the engine fails loudly (does **not** silently fall back) so a real API problem is visible.
5. Unset/empty `FACE_SWAP_POD_ID` → existing behavior is bit-for-bit unchanged.

## Non-goals

- No changes to `RunpodConfig` (`app/services/block_m2_video/runpod/runpod_config.py`). The override is intentionally read directly from `os.environ`, matching the sibling `cost_estimator.py` precedent.
- No changes to `RunpodClient`.
- No new dependencies.
- No change to the prefix-search code path itself; the override only adds a new branch before it.
- Not addressing the broader "pod naming conventions across phases" question — that's separate from #45.

## Design

### Code change: `app/services/block_m2_face_swap/face_swap_engine.py`

**Added imports / constants:**

```python
import os  # currently not imported in this module

_EXPLICIT_POD_ENV = "FACE_SWAP_POD_ID"  # next to _POD_NAME_PREFIX
```

**Modified method: `_find_or_start_pod()`** — insert the explicit-pod branch immediately after `list_pods()` and before the existing `candidates = [...]` line.

Pseudocode for the new branch:

```
explicit_id = os.environ.get(_EXPLICIT_POD_ENV)
if explicit_id:
    explicit = next((p for p in pods if p.id == explicit_id), None)
    if explicit is None:
        log warning "FACE_SWAP_POD_ID=<id> not in list_pods; falling back"
        # fall through to existing prefix search
    else:
        status = (explicit.desired_status or "").upper()
        if status == "RUNNING":
            log info "using explicit pod <id>"
            return (explicit, explicit.id, reused=True)
        if status in {"STOPPED", "EXITED"}:
            log info "resuming explicit pod <id> (<status>)"
            try:
                resumed = await client.resume_pod(explicit_id)
                ready = await client.wait_for_ready(
                    resumed.id, timeout_sec=self._pod_ready_timeout_sec,
                )
            except RunpodApiError as exc:
                raise FaceSwapError(
                    f"resume of explicit pod {explicit_id} failed: {exc}"
                ) from exc
            return (ready, ready.id, reused=False)
        # any other status (STARTING, TERMINATING, ERROR, DEAD, ...)
        log warning "explicit pod <id> in unexpected status <status>; falling back"
        # fall through to existing prefix search
# existing prefix-search code follows unchanged
```

**Rationale for inlining (vs. a `_try_explicit_pod` helper):**
The branch is ~20 lines and is called from one place. A helper would need a None-return convention to signal "fall back" and would split the pod-selection logic across two methods. Inline keeps `_find_or_start_pod` readable top-to-bottom.

**Rationale for failure modes:**
- "Not in `list_pods`" and "unexpected status" are non-actionable from `FaceSwapEngine`'s perspective — the user probably has a stale pod id. Falling back to prefix search keeps the bot working while the warning surfaces the misconfiguration.
- A `RunpodApiError` during resume means we successfully identified the pod and asked RunPod to act on it, and RunPod refused or failed. That's a real RunPod-side problem (quota, supply, network) and silently using a different pod would mask it. Fail loudly.

### Env var loading

`os.environ.get("FACE_SWAP_POD_ID")` is read at each call to `_find_or_start_pod()` (no caching) — matches `cost_estimator.py`'s pattern of reading at every call so tests can monkey-patch between calls.

For the value to reach `os.environ` in production:
- **Recommended:** add `FACE_SWAP_POD_ID=<pod-id>` to `.env` at the project root. `app.core.env_bootstrap.bootstrap_env()` is called at app startup (from `app/main.py`) and does `load_dotenv(.env)`, which loads it into `os.environ`.
- **Alternative:** shell-export it before launching the bot.

**Do not put it in `.env.runpod`** — that file is only read by `pydantic-settings` inside `RunpodConfig` and never reaches `os.environ`. The module-level comment next to `_EXPLICIT_POD_ENV` will state this explicitly.

### Tests: `tests/test_swapbatch_face_swap_engine.py`

Note: the file path mentioned in the original issue brief (`tests/test_face_swap_engine.py`) does not exist. The correct file — which already exercises `_find_or_start_pod` with a fake `RunpodClient` — is `tests/test_swapbatch_face_swap_engine.py`.

Add four new test cases. Each uses `monkeypatch.setenv("FACE_SWAP_POD_ID", ...)` and reuses the existing fake-client fixtures.

| # | Scenario | Setup | Assertions |
|---|---|---|---|
| 1 | Explicit pod RUNNING | env set to id `X`; `list_pods` returns one pod with `id=X, desired_status=RUNNING` (plus possibly other unrelated pods) | `list_pods` called once; `resume_pod`, `start_pod`, `wait_for_ready` never called; engine proceeds with pod `X`'s URL |
| 2 | Explicit pod STOPPED → resume succeeds | env set to id `X`; `list_pods` returns pod with `id=X, desired_status=STOPPED`; fake `resume_pod` returns a pod, fake `wait_for_ready` returns RUNNING | `resume_pod("X")` and `wait_for_ready` called; `start_pod` never called; engine proceeds with the resumed pod |
| 3 | Explicit pod not in list → fallback | env set to id `MISSING`; `list_pods` returns one or more `jarvis-m2-*` pods, none with id `MISSING` | engine selects a `jarvis-m2-*` pod from the existing prefix path (existing behavior preserved); warning logged about the missing id |
| 4 | Explicit pod STOPPED, `resume_pod` raises `RunpodApiError` | env set to id `X`; pod found STOPPED; fake `resume_pod` raises `RunpodApiError` | `FaceSwapError` propagates out of `_find_or_start_pod`; `start_pod` never called; prefix-search path never entered |

Tests run via the existing `pytest` setup; no new fixtures or dependencies.

## File-by-file change summary

| File | Change | Approx. lines |
|---|---|---|
| `app/services/block_m2_face_swap/face_swap_engine.py` | `import os`; add `_EXPLICIT_POD_ENV` constant; insert explicit-pod branch in `_find_or_start_pod` | +22 lines |
| `tests/test_swapbatch_face_swap_engine.py` | Add 4 test cases (likely reusing existing fixtures) | +80–120 lines |

No other files modified. No new files created.

## Risks & mitigations

- **Risk:** `desired_status` comparison string drift — RunPod could return casing other than upper. **Mitigation:** existing prefix-search code already does `(p.desired_status or "").upper()`; the new branch follows the same pattern.
- **Risk:** explicit-pod path skips the prefix search entirely when status is RUNNING, so a stale RUNNING pod id would be used instead of a fresher candidate. **Mitigation:** that's the intended behavior — the override is explicit operator intent.
- **Risk:** `RunpodApiError` raised by `list_pods()` is already wrapped in `FaceSwapError` by the existing code at the top of the method, so the explicit-pod branch never sees that failure mode. No new handling needed.

## Out of scope / future work

- Standardizing pod naming across phases so the prefix workaround becomes unnecessary.
- Supporting comma-separated multiple explicit pod ids with priority order.
- Exposing the override via `RunpodConfig` (would require touching that file; out of scope per non-goals).
