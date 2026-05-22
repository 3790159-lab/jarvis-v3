# bootstrap_pod.sh — probe-before-gate (#49 fix)

**Issue:** #49 — bootstrap.sh fast path skips install when modules are actually missing on the network volume, causing container exit 2 and pod death.
**Date:** 2026-05-22
**Scope:** one source file — `scripts/remote/bootstrap_pod.sh`.

## Problem

`scripts/remote/bootstrap_pod.sh` is the on-volume startup script that runs as the RunPod container's `startCmd` (`bash -lc '/workspace/bootstrap.sh'`). It manages an install-once cache via `/workspace/.bootstrap_version`: if the cached version equals `BOOTSTRAP_VERSION`, the script "fast paths" by skipping `apt`/`pip` and goes straight to launching ComfyUI.

Today (2026-05-22) four pods exhibited the same failure mode within hours of each other:

- `gkggbtglm864pq`, `suc4b6277203nb`, `va2u6buokaljyd`, `d93f33cb6rfcp7`
- Each container exited ~715s after spawn (pod `gkggbtglm864pq` is the timed observation).
- `/workspace/.bootstrap_log` showed:

  ```
  BOOTSTRAP_VERSION=2026.05.21-002
  [fast path] cache version 2026.05.21-002 matches, skipping install
  ...
  MISSING_MODULE=onnxruntime
  MISSING_MODULE=git
  MISSING_MODULE=transformers.pipeline
  ```

The version file said "fully installed at version 002" but the network-volume's `site-packages` was missing the modules the import probe checks. The probe (lines 78–109) runs *unconditionally* after the install branch and exits 2 on missing modules; `set -euo pipefail` propagates that exit; the container dies; the sniper sees `CONTAINER_EXITED` and pages.

**Root cause:** the version file is a *claim* about the volume's state, not a *fact* about it. When the claim and the volume diverge — for any reason: a partial uninstall, a manual `pip uninstall`, a corrupted `site-packages`, a Python interpreter swap, an external script tampering with the volume — the fast path trusts the claim, skips reinstall, and dies on the post-skip probe.

The probe is already in the right place (it's the safety net). The bug is that *failing the safety net is the only signal*, and the only response is exit 2 — there's no remediation step that says "if the cache lied, just reinstall."

## Goals

1. Detect the "version file lies about state" scenario before deciding to skip install.
2. When detected, force the slow install path on the spot — no operator intervention required.
3. Preserve fail-loud behavior: if install runs and the probe *still* fails afterwards, exit 2 (no silent recovery, no retry loops, sniper still gets the page).
4. Never write the version file unless the post-install probe confirms modules are actually present. The cache must never lie again.
5. Zero changes outside `scripts/remote/bootstrap_pod.sh`. No template change, no Python change, no test infrastructure change.

## Non-goals

- No new test framework for shell scripts. Manual verification on a fresh sniper catch is sufficient (matches established practice for #44/#45 bootstrap-adjacent changes).
- No changes to `face_swap_engine.py`, `runpod_client.py`, `pod_provisioner.py`, or any Python module.
- No changes to the RunPod template (`jpzymkuab8`) — same `startCmd`, same `imageName`, same volume mount.
- No retry loop on a failing post-install probe. If install + probe fails, exit 2 and let the operator look at the log.
- No housekeeping of stale model files. Out of scope — separate concern.
- No automation of the deploy step (wget to overwrite the on-volume copy). That ritual stays manual and is documented in a brief Deployment section at the end of this spec.

## Design

### Control flow (current → new)

**Current** (lines 24–113):

```
sanity check ComfyUI dir
version gate:
    if version file exists AND matches → NEED_INSTALL=false
    else → NEED_INSTALL=true
if NEED_INSTALL:
    apt + pip + model checks
import probe (ALWAYS, sys.exit(2) on failure)
write version file (ALWAYS)
kill 8188, exec ComfyUI
```

The probe runs regardless of which branch was taken, but its `sys.exit(2)` is unconditional. The version file write is also unconditional — so after install failure (probe fails), the broken state still gets "blessed" by the version file write, except `set -euo pipefail` causes the exit-2 to abort the script before the write line runs. The current code is *accidentally* correct about not writing the version on failure — but only because the script dies. There's no design intent there.

**New:**

```
sanity check ComfyUI dir
extract probe into a bash function: run_import_probe()
                                    (same python heredoc, but as a function, returning $?)

decide install:
    NEED_INSTALL=true   # default
    if version file exists AND content == $BOOTSTRAP_VERSION:
        echo "[version-gate] version file matches; verifying modules..."
        if run_import_probe:
            echo "[fast path] version match + modules present → skipping install"
            NEED_INSTALL=false
        else:
            echo "[forced slow path] version file lies — modules MISSING, reinstalling"
            # NEED_INSTALL stays true
    else:
        echo "[slow path] version mismatch or no file → installing"

if NEED_INSTALL:
    apt + pip + model checks   (unchanged)
    echo "[verify] post-install probe..."
    if NOT run_import_probe:
        echo "ERROR: post-install probe still failing — refusing to write version file" >&2
        exit 2
    echo "$BOOTSTRAP_VERSION" > "$VOLUME_VERSION_FILE"
    echo "=== Version cached: $BOOTSTRAP_VERSION ==="

kill 8188, exec ComfyUI   (unchanged)
```

Key shape changes vs. the current script:

1. **Probe is a function**, called twice with explicit branching on its exit code. Same Python body; no behavior change inside the heredoc.
2. **Pre-gate probe runs only when the version file matches** — there's no benefit to probing on a fresh volume or after a version bump, since both go to install anyway.
3. **Post-install probe is the gate on writing the version file.** The version-file write moves *inside* the `if NEED_INSTALL` block, after the post-probe check, so a failed install can never leave a "blessed" lie behind.
4. **Probe exit code is checked with `if run_import_probe; then ...; fi`** — that idiom suppresses `set -e` for the function call, which is exactly what we need (we want to inspect the failure, not abort on it).

### `BOOTSTRAP_VERSION` bump

`"2026.05.21-002"` → `"2026.05.22-003"`.

**Why bump:** existing healthy pods have `.bootstrap_version` content `"2026.05.21-002"`. After this fix lands, the version mismatch alone forces them down the slow install path (independent of whether their modules are healthy). This is belt-and-suspenders — the new pre-probe would catch broken pods anyway, but the bump makes the rollout deterministic: every pod re-installs once on its next sniper catch, and from that point onward every pod's version file is the post-fix `"003"` written only after a successful probe.

### Probe function shape

```bash
run_import_probe() {
    python << 'PYEOF'
import sys
modules_to_check = [
    'insightface',
    'segment_anything',
    'onnxruntime',
    'git',
]
missing = []
for mod in modules_to_check:
    try:
        __import__(mod)
        print(f"  OK {mod}")
    except ImportError as e:
        missing.append(mod)
        print(f"  FAIL {mod}: {e}", file=sys.stderr)

try:
    from transformers import pipeline
    print(f"  OK transformers.pipeline")
except (ImportError, RuntimeError) as e:
    missing.append('transformers.pipeline')
    print(f"  FAIL transformers.pipeline: {e}", file=sys.stderr)

if missing:
    for mod in missing:
        print(f"MISSING_MODULE={mod}", file=sys.stderr)
    sys.exit(2)
print("Import probe: ALL OK")
PYEOF
}
```

This is verbatim the existing heredoc body (lines 80–109 of the current script), wrapped in a bash function. No logic change inside. Function returns whatever `python` returned: 0 on success, 2 on failure.

Single-quoted `'PYEOF'` (already used by the current script) prevents bash from expanding `$mod` etc. inside the heredoc.

### Logging contract

The bootstrap log is the primary diagnostic surface; the sniper, pod-provisioner, and a human inspecting `/workspace/.bootstrap_log` all read it. The new log lines (in order of expected occurrence on a healthy fast path):

```
BOOTSTRAP_VERSION=2026.05.22-003
[version-gate] version file matches; verifying modules...
  OK insightface
  OK segment_anything
  OK onnxruntime
  OK git
  OK transformers.pipeline
Import probe: ALL OK
[fast path] version match + modules present → skipping install
=== Launching ComfyUI ===
```

On a forced-slow-path (the bug scenario), the log adds:

```
[version-gate] version file matches; verifying modules...
  FAIL onnxruntime: ...
  FAIL git: ...
MISSING_MODULE=onnxruntime
MISSING_MODULE=git
[forced slow path] version file lies — modules MISSING, reinstalling
=== Step: apt update + install system deps ===
...
[verify] post-install probe...
  OK insightface
  ...
Import probe: ALL OK
=== Version cached: 2026.05.22-003 ===
=== Launching ComfyUI ===
```

On a still-broken-after-install (unexpected; loud failure), the log adds:

```
[verify] post-install probe...
  FAIL <something>: ...
MISSING_MODULE=<something>
ERROR: post-install probe still failing — refusing to write version file
```

…followed by the container exiting 2 — same outward symptom as today, but with `.bootstrap_version` left at its prior content (or absent), so the next pod doesn't inherit a lie.

### What about the existing log line `[fast path] cache version $CACHED_VERSION matches, skipping install`?

That line goes away as-is. The new "[fast path] version match + modules present → skipping install" line is just as greppable and conveys more information. No automated scraping of the old line is known (the sniper greps for `CONTAINER_EXITED` / `READY` markers from RunPod, not bootstrap-log content).

## File-by-file change summary

| File | Change | Approx. lines |
|---|---|---|
| `scripts/remote/bootstrap_pod.sh` | Bump `BOOTSTRAP_VERSION` to `2026.05.22-003`; extract import probe into `run_import_probe()` function; restructure version-gate block to call probe before deciding `NEED_INSTALL`; move version-file write inside `NEED_INSTALL` branch behind a post-install probe gate; update log strings. | +30 / −20 lines net |

No other files modified. No new files created.

## Risks & mitigations

- **Risk:** the pre-probe takes measurable time on the fast path (extra ~3–5s for `import insightface, segment_anything, onnxruntime, git, transformers`). **Mitigation:** acceptable — fast path total is dominated by ComfyUI startup (~20–60s), and the probe is the safety net that prevents container death. Trading 3–5s for not-dying is correct.
- **Risk:** the probe imports succeed but the modules are still subtly broken (wrong version, missing `.so`, etc.) — pre-probe gives a false-positive "healthy" verdict. **Mitigation:** out of scope. The existing probe defines "healthy" as "imports succeed." Strengthening that definition (e.g. calling a function, checking versions) is a separate concern and not what bug #49 is about.
- **Risk:** post-install probe fails because the install actually didn't fix the problem (e.g., network volume corruption, disk full, pip can't resolve deps). **Mitigation:** intended outcome — exit 2, container dies, sniper pages, operator inspects the log. No retry loop; the install ran exactly once and we trust the operator to debug.
- **Risk:** `set -e` interacting badly with `if run_import_probe; then` and the probe's `sys.exit(2)`. **Mitigation:** standard bash idiom — `if cmd; then` disables `set -e` for `cmd` and inspects its exit code. Verified mentally; will confirm during implementation by reading the executed log.
- **Risk:** `[forced slow path]` reinstall fails for the same reason the modules went missing in the first place (root cause unknown — could be a recurring failure mode). **Mitigation:** out of scope for this fix. The current investigation has no theory for *why* modules disappeared from the volume; this fix makes the system resilient to the symptom while leaving root-cause investigation as future work. If the same failure mode recurs many times post-fix, that's a signal to investigate further.

## Deployment

Repository fix only; deploying the patched script to the live network volume is a manual one-time ritual after merge.

After this spec's commit lands on `main` (or its branch), to deploy:

```
# On the running pod (or any pod that mounts the same network volume):
cd /workspace
sha256sum bootstrap.sh                                   # before-state, for the log
wget -q https://raw.githubusercontent.com/3790159-lab/jarvis-v3/<commit-sha>/scripts/remote/bootstrap_pod.sh \
     -O bootstrap.sh.new
sha256sum bootstrap.sh.new                               # confirm matches repo file
mv bootstrap.sh.new bootstrap.sh
chmod +x bootstrap.sh
```

Optional cache reset (forces the next pod through the slow path even before the version-bump-triggered mismatch):
```
rm /workspace/.bootstrap_version
```

Verification: trigger a fresh sniper catch and read `/workspace/.bootstrap_log`. Expected lines (in order): `BOOTSTRAP_VERSION=2026.05.22-003`, the new `[version-gate]` / `[fast path]` or `[forced slow path]` / `[verify]` markers, `Import probe: ALL OK`, `Launching ComfyUI`. No `CONTAINER_EXITED` in the sniper's alert stream.

## Out of scope / future work

- Root-cause investigation of *why* modules went missing from the volume in the first place (network volume tooling, manual operator action, something in another script). If post-fix the `[forced slow path]` log line appears regularly, that is the signal to investigate.
- Strengthening the probe definition of "healthy" beyond `__import__` success (version checks, smoke calls).
- Automating the wget deployment ritual (cron, GitHub Action that writes via SSH, etc.).
- Periodic cleanup of `/workspace/.bootstrap_log` (currently grows unbounded).
