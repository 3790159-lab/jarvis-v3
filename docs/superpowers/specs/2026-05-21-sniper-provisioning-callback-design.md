# Sniper Provisioning Callback — Design

**Date:** 2026-05-21
**Branch:** `phase-3.0-inventory-stop-reliability`
**Tracker item:** #42 (Sniper provisioning callback, architectural refactor)
**Status:** Draft — awaiting user review

## Problem

The GPU sniper (`scripts/runpod_gpu_sniper.py`) successfully catches scarce A100 supply in EU-RO-1 — yesterday's run caught a pod after 69 attempts and delivered a Telegram alert. The pod it spawns, however, is a bare PyTorch container: no ComfyUI, no models, no env vars. The sniper calls `await rp_client.start_pod(name=name)` with no `image_name`, no `env`, no startup command — so `RunpodClient` falls back to whatever `RUNPOD_DOCKER_IMAGE` points at, with an empty env payload.

Yesterday's manual workaround was a 30-40 minute loop: SSH or RunPod web terminal into the pod, install missing deps, restart ComfyUI, debug import failures, retry. The iterative debug for the reactor-node import chain alone took ~1 hour the first time (onnxruntime → segment_anything → transformers, then a `transformers>=4.45` / `torch==2.4` incompatibility requiring a version pin).

The desired end state: sniper catches → automatic provisioning runs → after it completes (or fails gracefully), Telegram notifies the user "Pod ready" with a working URL. No manual SSH needed in the steady state.

## Goals

1. After a successful sniper catch, the pod becomes ready (ComfyUI serving on port 8188 with all custom-node imports succeeding) without human intervention.
2. The user receives exactly two Telegram alerts per catch: the existing "🎯 caught" the moment supply is grabbed, and a final outcome alert (✅ ready / ❌ failed / ⏰ timeout) once readiness has been determined.
3. Bootstrap failures are visible (Telegram message names the failure class and gives the user a clear pointer for diagnosis), not silent.
4. The refactor is small and reversible: existing `start_pod()` signature is untouched, existing Telegram/notification channel is reused, existing `state/runpod_sniper_status.json` is extended (not replaced).

## Non-Goals (deferred)

- **Tier 3 failure diagnostics** — pulling RunPod container logs into Telegram on failure. Adds GraphQL schema research. Revisited after we've felt the pain of opening the RunPod console for failed-import cases.
- **runpod_recreate_phase3.py upgrade** — could reuse `wait_for_pod_ready` for free; out of scope for this diff.
- **FaceSwapEngine pod discovery** (tracker item #45) — distinct concern.
- **Automated bootstrap.sh upload** — one-time manual paste via RunPod web terminal is acceptable; automation helper is a follow-up if friction proves real.
- **Heartbeat Telegram alerts** mid-bootstrap. Two alerts (catch + final) is the agreed cadence.
- **env-configurable readiness knobs** (`RUNPOD_READINESS_TIMEOUT_MIN`, etc.). Module-level constants per Q6.

## Architecture

Three layers, each with one clear responsibility. The sniper orchestrates; it does not bootstrap.

```
┌─────────────────────────────────────────────────────────────────────┐
│ Layer 1 — RunPod template (RUNPOD_TEMPLATE_ID, configured in RunPod │
│ console; documented in docs/runpod_template_config.md)              │
│   imageName  : stock e.g. runpod/pytorch:2.4.0-py3.11-cuda12.4...   │
│   startCmd   : bash -lc '/workspace/bootstrap.sh'                   │
│   ports      : 8188/http, 22/tcp                                    │
│   volumeMount: /workspace                                           │
└─────────────────────────────────────────────────────────────────────┘
                              │ pod boots; template injects startCmd
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Layer 2 — /workspace/bootstrap.sh (on network volume, persists)     │
│ Source-of-truth in repo: scripts/remote/bootstrap_pod.sh            │
│   1. read BOOTSTRAP_VERSION constant, compare /workspace/.bootstrap │
│      _version                                                       │
│   2. on mismatch → slow path: pip install / apt / custom-node sync  │
│   3. ALWAYS run import probe (insightface, segment_anything, ...)   │
│   4. on probe fail → log MISSING_MODULE=<name>, exit 1 → container  │
│      dies → ProvisionOutcome.CONTAINER_EXITED                       │
│   5. on probe pass → write .bootstrap_version, exec ComfyUI         │
└─────────────────────────────────────────────────────────────────────┘
                              │ ComfyUI binds 8188 when ready
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Layer 3 — pod_provisioner.py (Python, runs in sniper process)       │
│   wait_for_pod_ready(client, pod) -> ProvisionResult                │
│     interleave: httpx.get(/system_stats)  +  client.get_pod(pod_id) │
│     classify outcome:                                               │
│       READY            → HTTP 200                                   │
│       CONTAINER_EXITED → pod.desired_status == EXITED               │
│       TIMEOUT          → deadline reached, pod still RUNNING        │
└─────────────────────────────────────────────────────────────────────┘
                              │ ProvisionResult returned to sniper
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Sniper (catch + Telegram orchestration; unchanged role)             │
│   • existing "🎯 caught" alert + appended "(waiting for ComfyUI     │
│     bootstrap, will alert when ready)"                              │
│   • new: await wait_for_pod_ready(...) → second alert:              │
│       READY            → "✅ Pod ready: <url>"                      │
│       CONTAINER_EXITED → "❌ Pod bootstrap failed (exited at <ts>,  │
│                            ran <s>s) — check RunPod console logs    │
│                            for pod <id>"                            │
│       TIMEOUT          → "⏰ Pod unresponsive after 25 min — still  │
│                            RUNNING, check web terminal for pod <id>"│
│   • SIGINT during readiness wait → "🛑 readiness aborted, pod       │
│     still RUNNING — pod_id=<id>" (does NOT terminate the pod)       │
└─────────────────────────────────────────────────────────────────────┘
```

### Key invariants

- `RunpodClient.start_pod()` signature is **not modified**. The template carries imageName, startCmd, ports, and volume mount.
- `RunpodConfig` gains **no new fields**. Knobs are hardcoded module constants in `pod_provisioner.py`.
- The provisioner module is **purely passive observability** — it never mutates pod state (no stop/terminate/restart).
- Bootstrap.sh has **zero knowledge** of the sniper or provisioner. It is a standalone, idempotent script.

## Components

### Files added (3 code + 1 doc)

**`app/services/block_m2_video/runpod/pod_provisioner.py`** *(~150 lines)*

```python
DEFAULT_READINESS_TIMEOUT_MIN = 25
DEFAULT_POLL_INTERVAL_SEC = 30
CONSECUTIVE_TRANSIENT_FAILURE_THRESHOLD = 3
SYSTEM_STATS_PATH = "/system_stats"

class ProvisionOutcome(str, Enum):
    READY = "ready"
    CONTAINER_EXITED = "container_exited"
    TIMEOUT = "timeout"

class ProvisionResult(BaseModel):
    outcome: ProvisionOutcome
    pod_id: str
    public_url: str | None
    elapsed_sec: float
    detail: str  # human-readable, suitable for Telegram

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
) -> ProvisionResult: ...
```

`http_client`, `clock`, `sleeper`, `interrupted` are injectable for testability (mirroring the existing sniper's pattern). `interrupted` lets the sniper share its `_interrupted` flag so SIGINT short-circuits the polling loop.

**`tests/test_pod_provisioner.py`** *(~250 lines)*
Flat under `tests/`, matching repo convention. Test list in the Testing section.

**`scripts/remote/bootstrap_pod.sh`** *(~80-120 lines)*
Repo source-of-truth. Pasted onto `/workspace/bootstrap.sh` on the network volume once via web terminal. Shape:

```bash
#!/usr/bin/env bash
set -euo pipefail

BOOTSTRAP_VERSION="2026.05.21-001"   # bump on any meaningful change
VOLUME_VERSION_FILE="/workspace/.bootstrap_version"
LOG_FILE="/workspace/.bootstrap_log"

# 1. Source venv on /workspace (created during first cold setup)
source /workspace/venv/bin/activate

# 2. Cache check (Q3 answer: versioned manifest)
cached_version="$(cat "$VOLUME_VERSION_FILE" 2>/dev/null || echo "")"
if [[ "$cached_version" != "$BOOTSTRAP_VERSION" ]]; then
    # slow path: pip install -r requirements.txt, apt installs,
    #            git pull custom_nodes/*, etc.
    ...
fi

# 3. Import probe (Q3 answer: always runs, even on warm cache)
python - <<'PY' || { echo "MISSING_MODULE=$?"; exit 1; }
import importlib, sys
mods = ["insightface", "segment_anything", "onnxruntime",
        "transformers"]   # extend as needed
failed = []
for m in mods:
    try: importlib.import_module(m)
    except Exception as e:
        failed.append(f"{m}: {type(e).__name__}: {e}")
if failed:
    sys.stderr.write("IMPORT_PROBE_FAILED:\n" + "\n".join(failed) + "\n")
    sys.exit(1)
PY

# 4. Mark version on success
echo "$BOOTSTRAP_VERSION" > "$VOLUME_VERSION_FILE"

# 5. Launch ComfyUI (exec → ComfyUI becomes PID 1; its exit
#    terminates the container, which propagates as CONTAINER_EXITED)
cd /workspace/ComfyUI
exec python main.py --listen 0.0.0.0 --port 8188
```

Exact dep list and install steps will be refined in the implementation plan from yesterday's manual install transcript.

**`docs/runpod_template_config.md`** *(~30-50 lines)*
Captures the RunPod template settings as repo-side backup. Per Q2 answer: if the template gets deleted, the user must be able to recreate it in ~5 min from this doc. Contents: template name, image, startCmd, env defaults, ports, mount path, screenshots optional.

### Files modified (1)

**`scripts/runpod_gpu_sniper.py`** *(~40-line diff)*

- Add import: `from app.services.block_m2_video.runpod.pod_provisioner import wait_for_pod_ready, ProvisionOutcome`.
- After successful catch (current line ~276), call `result = await wait_for_pod_ready(rp_client, pod, interrupted=lambda: _interrupted)`.
- Tweak existing catch alert text (lines ~267-275): append `"\n(waiting for ComfyUI bootstrap, will alert when ready)"`.
- New branch on `result.outcome` → fire one of three new Telegram alerts (text in the Architecture diagram above).
- Extend `_write_status()` payload after readiness completes: add `provisioning_outcome` (string) and `provisioning_detail` (string), so consumers of `state/runpod_sniper_status.json` see the final state.
- SIGINT handling: the `interrupted` callable lets `wait_for_pod_ready` exit early; the sniper detects this by inspecting `result.outcome` *and* `_interrupted`, and sends the dedicated "🛑 readiness aborted" alert instead of the normal outcome message.

### Files NOT touched

| File | Why not |
|---|---|
| `app/services/block_m2_video/runpod/runpod_client.py` | `start_pod()` unchanged. Template carries imageName/startCmd. Tier 3 (logs API) is a separate future diff. |
| `app/services/block_m2_video/runpod/runpod_config.py` | No new env vars per Q6 — knobs are module constants. |
| `scripts/runpod_recreate_phase3.py` | Could pick up `wait_for_pod_ready` for free later; out of scope. |
| `app/services/notifications/*` | Existing `send_alert()` is reused. |
| `tests/test_runpod_gpu_sniper.py` | Light extension only — one test per outcome branch, plus SIGINT-during-readiness. Existing tests should still pass with minimal mocking adjustments. |

## Data flow (happy path + interesting cases)

### Sequence — happy warm-cache catch

```
sniper          RunpodClient        RunPod API     bootstrap.sh    ComfyUI    Telegram
  │                  │                  │              │             │           │
  │ start_pod(name)  │                  │              │             │           │
  ├─────────────────>│  podDeployOnDemand              │             │           │
  │                  ├─────────────────>│              │             │           │
  │                  │<─────────PodInfo─┤              │             │           │
  │<──────PodInfo────┤                  │ (pod boots,template:startCmd)         │
  │                  │                  │              │             │           │
  │ "🎯 caught (waiting for ComfyUI bootstrap...)" ─────────────────────────────>│
  │                  │                  │              │             │           │
  │ wait_for_pod_ready(client, pod)                    │             │           │
  │                  │                  │              │             │           │
  │ get_pod(pod_id) ─┤─────────────────>│              │             │           │
  │                  │<──RUNNING────────┤              │             │           │
  │ GET /system_stats ───────────────────────────────────────HTTP 404 (ComfyUI not up yet)
  │ sleep 30s        │                  │              │             │           │
  │                  │              (.bootstrap_version matches — fast path)     │
  │                  │                  │              │ probe OK    │           │
  │                  │                  │              │ exec ComfyUI│           │
  │                  │                  │              │             │ bind 8188 │
  │ GET /system_stats ───────────────────────────────────────────────HTTP 200    │
  │ ProvisionResult(READY, pod_id, url, elapsed=45s, "ComfyUI ready in 45s")     │
  │                                                                              │
  │ "✅ Pod ready: <url>" ───────────────────────────────────────────────────────>│
```

### Sequence — cold cache (slow install path)

Same as warm cache, but bootstrap.sh's slow path runs first. The provisioner's polling loop runs longer (5-10 min instead of 30-60s), keeps seeing pod RUNNING + HTTP not-yet-200, returns READY when ComfyUI eventually binds. No new Telegram alerts during the wait — per Q5 cadence (ii).

### Sequence — import probe failure (reactor-node missing)

```
sniper                  bootstrap.sh        Provisioner
  │ start_pod(name) →   │
  │                     │ probe: import insightface OK
  │                     │ probe: import segment_anything → ModuleNotFoundError
  │                     │ stderr: IMPORT_PROBE_FAILED: segment_anything: ...
  │                     │ exit 1 → container exits
  │                                          │
  │ get_pod() → EXITED (lastStatusChange = T+45s)
  │ ProvisionResult(CONTAINER_EXITED, pod_id, None, elapsed=45s,
  │                 "Container exited 45s after RUNNING — check RunPod console
  │                  logs for pod <id>")
  │
  │ "❌ Pod bootstrap failed (exited at <ts>, ran 45s) — check RunPod console
  │  logs for pod <id>" → Telegram
```

The user opens the RunPod web console, sees `IMPORT_PROBE_FAILED: segment_anything: ModuleNotFoundError`, fixes bootstrap.sh's slow-install step, bumps `BOOTSTRAP_VERSION`, paste-updates the script on the volume, re-runs the sniper.

### Sniper → start_pod env payload

The sniper continues to call `await rp_client.start_pod(name=name)` with no `env=`, no `image_name=`. The RunPod template carries the imageName and startCmd. Bootstrap.sh does not read any sniper-provided env vars (all configuration is in the script itself or on the volume). This keeps `start_pod()`'s signature untouched and the sniper's call site identical to current.

### Provisioner polling loop (pseudocode)

```python
async def wait_for_pod_ready(client, pod, ...) -> ProvisionResult:
    start = clock()
    deadline = start + timeout_min * 60
    consecutive_failures = 0
    last_pod_state = pod.desired_status

    while clock() < deadline:
        if interrupted():
            return ProvisionResult(
                outcome=TIMEOUT,  # sniper differentiates via _interrupted flag
                detail="SIGINT during readiness wait",
                ...
            )

        # 1. HTTP probe
        try:
            r = await http_client.get(f"{public_url}{SYSTEM_STATS_PATH}",
                                       timeout=10.0)
            if r.status_code == 200:
                return ProvisionResult(outcome=READY, ...)
        except (httpx.ConnectError, httpx.TimeoutException):
            pass  # ComfyUI not bound yet — normal during bootstrap

        # 2. Pod-state check
        try:
            pod_now = await client.get_pod(pod.id)
            consecutive_failures = 0
        except RunpodApiError:
            consecutive_failures += 1
            if consecutive_failures >= CONSECUTIVE_TRANSIENT_FAILURE_THRESHOLD:
                # Treat as timeout — we genuinely don't know pod state
                return ProvisionResult(outcome=TIMEOUT,
                    detail="RunPod API unreachable for too long", ...)
            pod_now = None

        if pod_now and (pod_now.desired_status or "").upper() == "EXITED":
            return ProvisionResult(outcome=CONTAINER_EXITED,
                detail=f"Container exited {elapsed:.0f}s after RUNNING — "
                       f"check RunPod console logs for pod {pod.id}", ...)

        await sleeper(poll_interval_sec)

    return ProvisionResult(outcome=TIMEOUT,
        detail=f"Pod still RUNNING but /system_stats never returned 200 "
               f"in {timeout_min} min — check web terminal for pod {pod.id}", ...)
```

### State file extensions

`state/runpod_sniper_status.json` already exists. After readiness completes, the sniper extends the existing `caught` status payload with:

```jsonc
{
  // existing fields: status, started_at, last_attempt_at, attempts,
  //                  pod_id, pod_public_url, pod_name, error_detail, config
  "status": "caught",                  // existing
  "provisioning_outcome": "ready",     // new
  "provisioning_detail": "ComfyUI ready in 45s",  // new
  "provisioning_elapsed_sec": 45.2,    // new
  "provisioning_completed_at": "..."   // new
}
```

These are additive. Existing readers ignore unknown fields.

## Error handling

### Three ProvisionOutcomes

| Outcome | Trigger | Telegram |
|---|---|---|
| `READY` | `/system_stats` returns HTTP 200 before deadline | `✅ Pod ready: <url>` |
| `CONTAINER_EXITED` | `pod.desired_status == EXITED` observed during polling (takes precedence over continued HTTP 404) | `❌ Pod bootstrap failed (exited at <ts>, ran <s>s) — check RunPod console logs for pod <id>` |
| `TIMEOUT` | Deadline reached, pod still RUNNING but HTTP never 200; OR `consecutive_failures` threshold hit on `get_pod()` | `⏰ Pod unresponsive after <N> min — pod_id=<id>` |

Plus one non-ProvisionOutcome path handled by the sniper:

| Sniper-handled case | Detection | Telegram |
|---|---|---|
| SIGINT during readiness wait | `_interrupted` flag flipped while `wait_for_pod_ready` is running | `🛑 Pod caught but readiness check aborted by SIGINT — pod_id=<id> still RUNNING, check manually` |

### Bootstrap.sh failure modes → ProvisionOutcome mapping

| Bootstrap failure | Container behavior | Observed outcome |
|---|---|---|
| Version mismatch detected, `pip install` fails (network) | `set -e` → exit non-zero → container exits | `CONTAINER_EXITED` |
| Slow-install OK, import probe finds missing module | probe exits 1 → bootstrap exits 1 → container exits | `CONTAINER_EXITED` |
| Slow-install OK, probe OK, ComfyUI raises at startup | `exec python main.py` → process exits → container exits | `CONTAINER_EXITED` |
| Slow-install hangs (e.g., model download) past 25 min | Container still RUNNING, ComfyUI never bound | `TIMEOUT` |
| ComfyUI runs but reactor-node logs ERROR and doesn't break startup | `/system_stats` returns 200 anyway | `READY` (false-positive — probe should have caught this; bump probe coverage if it happens) |

### Provisioner internal errors

Transient network/API failures during polling are absorbed silently up to `CONSECUTIVE_TRANSIENT_FAILURE_THRESHOLD = 3`:

- `httpx.ConnectError` / `httpx.TimeoutException` on `/system_stats` — normal during bootstrap; no counter, just keep polling.
- `RunpodApiError` on `get_pod()` — increment counter; reset on next success; on 3rd consecutive failure, return `TIMEOUT` with `detail="RunPod API unreachable for too long"`.

Any unexpected exception (not in the above two classes) bubbles up. The sniper's existing top-level catch block in `_snipe()` will mark the status as `error` and send `"❌ RunPod sniper crashed"` — same path as today, preserves existing behavior.

## Testing

### `tests/test_pod_provisioner.py` — unit tests with mocked client + httpx

| Test | Asserts |
|---|---|
| `test_outcome_ready_on_first_poll` | `wait_for_pod_ready` returns `READY` when first HTTP call is 200. |
| `test_outcome_ready_after_n_polls` | Initial connection errors → eventual 200 → `READY`. |
| `test_outcome_container_exited_on_pod_exited` | `get_pod` returns `desired_status="EXITED"` → returns `CONTAINER_EXITED` without waiting for full timeout. |
| `test_container_exited_precedence_over_404` | Both 404 *and* EXITED in same iteration → `CONTAINER_EXITED` (pod-state check wins). |
| `test_outcome_timeout_when_pod_running_no_http` | Pod RUNNING for full window, HTTP always 404 → `TIMEOUT`. |
| `test_outcome_timeout_when_pod_pending` | Pod stays `PENDING` throughout → `TIMEOUT`. |
| `test_consecutive_runpod_api_failures_returns_timeout` | 3 consecutive `RunpodApiError` from `get_pod` → `TIMEOUT` with API-unreachable detail. |
| `test_transient_failures_below_threshold_recover` | 2 consecutive failures then success → keeps polling, eventual `READY`. |
| `test_interrupted_short_circuits_loop` | `interrupted` callable returns True mid-loop → exits with TIMEOUT outcome + SIGINT detail. |
| `test_provision_result_carries_pod_id_url_elapsed` | All outcomes populate `pod_id`, `public_url`, `elapsed_sec` correctly. |

All tests use the injectable `clock` / `sleeper` / `http_client` / `interrupted` hooks; zero real network, zero real time. Mirrors the existing testability pattern in `test_runpod_gpu_sniper.py`.

### `tests/test_runpod_gpu_sniper.py` — integration test extensions

Three new tests (or parametrized expansion of one), all using a mocked `wait_for_pod_ready`:

- `test_sniper_calls_wait_for_pod_ready_after_catch` — verify it's called with the right args.
- `test_sniper_sends_correct_telegram_per_outcome` — parametrize over `READY` / `CONTAINER_EXITED` / `TIMEOUT`, assert message content.
- `test_sniper_handles_sigint_during_readiness_wait` — flip `_interrupted` after catch, assert "readiness aborted" alert.

### bootstrap.sh testing

**Not unit-tested.** Bash testing harness overhead (bats / shellspec) is not worth it for an idempotent, manually-edited script. The version manifest + import probe are the validation. Manual smoke tests after deployment:

1. Catch a pod with current `BOOTSTRAP_VERSION` → expect `READY` alert.
2. Bump version on the volume, terminate pod, re-snipe → expect slow path runs.
3. Edit probe to import a non-existent module, redeploy bootstrap → expect `CONTAINER_EXITED` alert.

### Regression coverage

Existing ~2300 tests must continue to pass. The `start_pod()` and `RunpodClient` test suites are unaffected (those files are not modified). Existing sniper tests get minor mock adjustments where they previously asserted on exact `_safe_notify` call count (we add a second alert).

## Manual setup steps (one-time, not in diff)

1. **Create RunPod template** in console. Image: stock pytorch matching existing `RUNPOD_DOCKER_IMAGE` major version. startCmd: `bash -lc '/workspace/bootstrap.sh'`. Ports: `8188/http,22/tcp`. Volume mount: `/workspace`. Note template ID.
2. **Set `RUNPOD_TEMPLATE_ID=<id>`** in `.env.runpod`. This activates the already-wired template path in `RunpodClient._deploy_pod` (`runpod_client.py:392-393`).
3. **Upload bootstrap script.** Paste contents of `scripts/remote/bootstrap_pod.sh` into RunPod web terminal: `cat > /workspace/bootstrap.sh <<'EOF' ... EOF`, then `chmod +x`. (One-time per environment. Updates after this go through bumping `BOOTSTRAP_VERSION` in the repo file, re-pasting to volume, then catching a fresh pod.)
4. **Document template in `docs/runpod_template_config.md`** with the exact settings used, for recovery.
5. **Smoke test** with `python scripts/runpod_gpu_sniper.py --dry-run` (existing flag — no real pod), then a real catch.

## Open questions

None. All six original brainstorm questions are resolved:

- Q1 (callback location) → `app/services/block_m2_video/runpod/pod_provisioner.py`, passive.
- Q2 (invocation mechanism) → RunPod template (already-plumbed `RUNPOD_TEMPLATE_ID`).
- Q3 (cache check) → versioned manifest + import probe.
- Q4 (readiness/failure surfacing) → Tier 2 (HTTP + pod-state); Tier 3 deferred.
- Q5 (Telegram cadence) → two alerts (catch + final).
- Q6 (config knobs) → hardcoded module constants; no env, no CLI.
