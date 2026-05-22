# bootstrap_pod.sh probe-before-gate (#49) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop bootstrap.sh from killing the pod when `/workspace/.bootstrap_version` claims an install that no longer matches the volume. Move the import probe **before** the version gate, force `NEED_INSTALL=true` on a failing pre-probe, run a post-install probe gate before writing the version file, and bump `BOOTSTRAP_VERSION` to `2026.05.22-003`.

**Architecture:** Extract the existing python import-probe heredoc into a bash function `run_import_probe()`. Call it twice: (1) inside the version-gate block, **only when the version file matches**, to detect a lying cache and force the slow path; (2) at the tail of the install branch, as a hard precondition for writing the version file. The version-file write moves out of the unconditional bottom and into the install branch behind the post-probe. The python body inside the heredoc is unchanged.

**Tech Stack:** bash 4+, `set -euo pipefail`, `python` (already on the RunPod base image). No shell-test framework in repo — verification is `bash -n` syntax check + manual scenario trace.

**Spec:** `docs/superpowers/specs/2026-05-22-bootstrap-probe-before-gate-design.md`

---

## File Structure

| File | Role | Change |
|---|---|---|
| `scripts/remote/bootstrap_pod.sh` | On-volume pod startup script (lives at `/workspace/bootstrap.sh` on the persistent network volume; invoked by RunPod template `startCmd`) | Extract probe heredoc into `run_import_probe()`; restructure version-gate to call pre-probe and branch; relocate version-file write inside install branch behind post-probe gate; bump `BOOTSTRAP_VERSION` to `2026.05.22-003` |

No new files. No other files touched. No Python module changes. No RunPod template changes.

**Pre-state of `scripts/remote/bootstrap_pod.sh` (line references against current `main`):**

- Line 6: `set -euo pipefail`
- Line 8: `BOOTSTRAP_VERSION="2026.05.21-002"`
- Lines 9–11: cache/log/dir constants
- Lines 13–16: logging setup + start banner
- Lines 18–22: sanity check (`COMFYUI_DIR` exists)
- Lines 24–36: version-gate block (the bug origin)
- Lines 38–76: slow install branch (apt + pip + model checks)
- Lines 78–109: import probe (python heredoc, `sys.exit(2)` on miss)
- Lines 111–113: unconditional version-file write (the bottom)
- Lines 115–118: port 8188 cleanup
- Lines 120–123: `exec python main.py …` (ComfyUI launch)

**Why no automated tests:** the repo has no shell-test framework (no `bats`, no `shellspec`). Per the spec, verification is manual on a fresh sniper catch after deploy. In this plan, per-task verification is **`bash -n`** (syntax) plus **a written logic trace** through the four scenarios (fresh volume, healthy fast path, version mismatch, lying version file).

**Line endings:** the file is LF on the repo (Linux target); Windows checkout may show CRLF on disk via git autocrlf. Edits via the `Edit` tool preserve whatever the file currently has — no special handling required.

---

## Task 1: Extract probe into `run_import_probe()` bash function (refactor only — no behavior change)

**Why first:** the diff in Task 2 is purely structural — moving a function call to new locations. Doing the extraction in its own commit makes Task 2's restructuring diff readable and lets a reviewer verify "no logic change" once and then focus solely on control flow.

**Files:**
- Modify: `scripts/remote/bootstrap_pod.sh`

### Step 1.1: Insert the `run_import_probe()` function definition after the sanity check

- [ ] Edit `scripts/remote/bootstrap_pod.sh`. Locate the end of the sanity-check block (line 22, the closing `fi`):

```bash
# --- Sanity check ---
if [[ ! -d "$COMFYUI_DIR" ]]; then
    echo "ERROR: COMFYUI_NOT_FOUND ($COMFYUI_DIR)" >&2
    exit 1
fi
```

Immediately after this `fi` (i.e. before the blank line preceding `# --- Version gate ---`), insert the function definition + a separator blank line:

```bash

# --- Import probe (function; called pre-gate and post-install in later tasks) ---
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

The python body is **byte-for-byte** the current heredoc (lines 80–109). The single-quoted `'PYEOF'` is preserved (prevents bash expansion of `$mod`, `$e`, `f"…"` braces).

### Step 1.2: Replace the inline probe heredoc at the bottom with a function call

- [ ] In the same file, locate the inline probe block (lines 78–109):

```bash
# --- Import probe (ALWAYS runs, regardless of cache state) ---
echo "=== Import probe ==="
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
```

Replace the entire block above with:

```bash
# --- Import probe (ALWAYS runs, regardless of cache state) ---
echo "=== Import probe ==="
run_import_probe
```

Behavior preservation: `run_import_probe`'s last command is the `python` heredoc; its exit code becomes the function's return value. On miss, `sys.exit(2)` → python exits 2 → function returns 2 → bash top-level command returns 2 → `set -e` aborts the script. Identical to the current behavior, where the heredoc itself returned 2 and `set -e` aborted.

### Step 1.3: Syntax check

- [ ] Run:

```
bash -n scripts/remote/bootstrap_pod.sh
```

Expected: exits 0, no output. If you see a parser error, you broke a quote or a brace — recheck Steps 1.1 and 1.2 against the original.

### Step 1.4: Manual logic trace — confirm no behavior change

- [ ] Read the modified `scripts/remote/bootstrap_pod.sh` end-to-end. Confirm:

1. `BOOTSTRAP_VERSION` is still `"2026.05.21-002"` (unchanged in this task).
2. The function `run_import_probe()` is defined between the sanity check and the version gate.
3. The version-gate block (lines starting `# --- Version gate ---`) is unchanged from `main`.
4. The slow-install block (`if [[ "$NEED_INSTALL" == "true" ]]; then … fi`) is unchanged from `main`.
5. After the install branch, there is exactly one line that triggers the probe: `run_import_probe` (no inline heredoc).
6. After the probe call, the version-file write (`echo "$BOOTSTRAP_VERSION" > "$VOLUME_VERSION_FILE"`) is still present and unconditional.
7. The port-8188 cleanup and ComfyUI launch lines are unchanged.

Trace the four scenarios mentally and confirm identical outcomes to current `main`:
- **Fresh volume:** no file → slow path → install → probe → version-file write → launch.
- **Healthy match:** file matches → fast path → probe → version-file write (no-op, same content) → launch.
- **Version mismatch:** mismatch → slow path → install → probe → version-file write (updated) → launch.
- **Lying cache:** file matches → fast path → probe FAILS → set -e exits 2 → container dies. (Same bug as before; that's the point — this task is refactor only.)

### Step 1.5: Commit

- [ ] Run:

```
git add scripts/remote/bootstrap_pod.sh
git commit -m "refactor(runpod): extract bootstrap import probe into run_import_probe() function"
```

---

## Task 2: Probe-before-gate + post-install probe gate + version-file relocation + version bump (#49 fix)

**Why second:** Task 1 left the file structurally clean. This task changes only control flow — adding two call sites for `run_import_probe`, restructuring the version-gate block to branch on the pre-probe, and moving the version-file write inside the install branch behind the post-probe.

**Files:**
- Modify: `scripts/remote/bootstrap_pod.sh`

### Step 2.1: Bump `BOOTSTRAP_VERSION`

- [ ] Edit `scripts/remote/bootstrap_pod.sh`. Locate line 8:

```bash
BOOTSTRAP_VERSION="2026.05.21-002"
```

Replace with:

```bash
BOOTSTRAP_VERSION="2026.05.22-003"
```

### Step 2.2: Restructure the version-gate block to call the pre-probe and branch

- [ ] In the same file, locate the version-gate block (lines 24–36 of pre-Task-1; numbers may have shifted by the function-definition insertion in Task 1 — find by the `# --- Version gate ---` header):

```bash
# --- Version gate ---
NEED_INSTALL=true
if [[ -f "$VOLUME_VERSION_FILE" ]]; then
    CACHED_VERSION=$(cat "$VOLUME_VERSION_FILE")
    if [[ "$CACHED_VERSION" == "$BOOTSTRAP_VERSION" ]]; then
        echo "[fast path] cache version $CACHED_VERSION matches, skipping install"
        NEED_INSTALL=false
    else
        echo "[slow path] cache version $CACHED_VERSION != $BOOTSTRAP_VERSION, reinstalling"
    fi
else
    echo "[slow path] no cached version, full install"
fi
```

Replace the entire block above with:

```bash
# --- Version gate (probe-before-skip) ---
NEED_INSTALL=true
if [[ -f "$VOLUME_VERSION_FILE" ]]; then
    CACHED_VERSION=$(cat "$VOLUME_VERSION_FILE")
    if [[ "$CACHED_VERSION" == "$BOOTSTRAP_VERSION" ]]; then
        echo "[version-gate] cached version $CACHED_VERSION matches; verifying modules..."
        if run_import_probe; then
            echo "[fast path] version match + modules present → skipping install"
            NEED_INSTALL=false
        else
            echo "[forced slow path] version file lies — modules MISSING, reinstalling"
            # NEED_INSTALL stays true
        fi
    else
        echo "[slow path] cache version $CACHED_VERSION != $BOOTSTRAP_VERSION, reinstalling"
    fi
else
    echo "[slow path] no cached version, full install"
fi
```

Notes on what changed and why:
- The fast path now runs `run_import_probe` *before* setting `NEED_INSTALL=false`. The `if run_import_probe; then` idiom suppresses `set -e` for the call, so a failing probe falls through to the `else` instead of aborting the script.
- A failing pre-probe leaves `NEED_INSTALL=true` (the default) and emits the `[forced slow path]` log — the bug's signature.
- The version-mismatch and missing-file branches are unchanged.

### Step 2.3: Add post-install probe gate + relocate version-file write into the install branch

- [ ] In the same file, locate the closing `fi` of the install branch. The install branch currently looks like:

```bash
# --- Slow install path ---
if [[ "$NEED_INSTALL" == "true" ]]; then
    echo "=== Step: apt update + install system deps ==="
    apt-get update -qq
    apt-get install -y unzip ffmpeg

    echo "=== Step: install ComfyUI requirements ==="
    pip install -r "$COMFYUI_DIR/requirements.txt"

    echo "=== Step: install face swap deps ==="
    pip install --quiet \
        onnxruntime-gpu \
        insightface \
        segment_anything \
        gitpython

    echo "=== Step: re-pin transformers (ComfyUI installs newer; reactor needs <4.45 with PyTorch 2.4) ==="
    pip install --force-reinstall --quiet "transformers<4.45"

    echo "=== Step: verify model files on volume ==="
    INSWAPPER="$COMFYUI_DIR/models/insightface/inswapper_128.onnx"
    BUFFALO_DIR="$COMFYUI_DIR/models/insightface/models/buffalo_l"

    if [[ ! -f "$INSWAPPER" ]]; then
        echo "WARNING: $INSWAPPER missing - re-downloading"
        mkdir -p "$COMFYUI_DIR/models/insightface"
        cd "$COMFYUI_DIR/models/insightface"
        wget -q https://huggingface.co/ezioruan/inswapper_128.onnx/resolve/main/inswapper_128.onnx -O inswapper_128.onnx
    fi

    if [[ ! -d "$BUFFALO_DIR" ]] || [[ -z "$(ls -A $BUFFALO_DIR 2>/dev/null)" ]]; then
        echo "WARNING: buffalo_l models missing - re-downloading"
        mkdir -p "$BUFFALO_DIR"
        cd "$COMFYUI_DIR/models/insightface/models"
        wget -q https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip
        unzip -o buffalo_l.zip -d buffalo_l/
        rm -f buffalo_l.zip
    fi
fi
```

Add a post-install probe + version-file write **inside** the `if [[ "$NEED_INSTALL" == "true" ]]; then … fi` block, immediately before the closing `fi`. The body above is unchanged; only the lines between the second `fi` (`buffalo_l` check) and the outer `fi` (install branch) get new content. After editing, the tail of the install branch should read:

```bash
    if [[ ! -d "$BUFFALO_DIR" ]] || [[ -z "$(ls -A $BUFFALO_DIR 2>/dev/null)" ]]; then
        echo "WARNING: buffalo_l models missing - re-downloading"
        mkdir -p "$BUFFALO_DIR"
        cd "$COMFYUI_DIR/models/insightface/models"
        wget -q https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip
        unzip -o buffalo_l.zip -d buffalo_l/
        rm -f buffalo_l.zip
    fi

    echo "=== [verify] post-install probe... ==="
    if ! run_import_probe; then
        echo "ERROR: post-install probe still failing — refusing to write version file" >&2
        exit 2
    fi

    echo "$BOOTSTRAP_VERSION" > "$VOLUME_VERSION_FILE"
    echo "=== Version cached: $BOOTSTRAP_VERSION ==="
fi
```

Notes:
- `if ! run_import_probe; then exit 2; fi` is the standard idiom for "fail loud with explicit message." `set -e` is suppressed inside the `if`, so we control the exit explicitly.
- The version-file write is now inside the `NEED_INSTALL` branch — a failed post-probe `exit 2`s before it.
- The fast path (where `NEED_INSTALL=false`) doesn't enter this branch at all; the version file is left untouched (and was already correct, since we only get to the fast path when the file matches).

### Step 2.4: Remove the unconditional probe + version-file write at the bottom

- [ ] In the same file, locate the block that Task 1 left in place (immediately before the port-8188 cleanup). It currently reads:

```bash
# --- Import probe (ALWAYS runs, regardless of cache state) ---
echo "=== Import probe ==="
run_import_probe

# --- Mark version as installed ---
echo "$BOOTSTRAP_VERSION" > "$VOLUME_VERSION_FILE"
echo "=== Version cached: $BOOTSTRAP_VERSION ==="

# --- Kill any existing ComfyUI process (port 8188 conflict prevention) ---
```

Delete the probe block and the version-file-write block. After editing, the tail of the file (after the install branch's closing `fi`) should read:

```bash
fi

# --- Kill any existing ComfyUI process (port 8188 conflict prevention) ---
echo "=== Step: clean port 8188 ==="
pkill -9 -f "python main.py" 2>/dev/null || true
sleep 2

# --- Launch ComfyUI ---
echo "=== Launching ComfyUI ===" 
cd "$COMFYUI_DIR"
exec python main.py --listen 0.0.0.0 --port 8188
```

(The `fi` above is the install-branch closing brace from Step 2.3. The port-cleanup + launch lines are unchanged from current `main`.)

### Step 2.5: Syntax check

- [ ] Run:

```
bash -n scripts/remote/bootstrap_pod.sh
```

Expected: exits 0, no output.

### Step 2.6: Manual scenario trace

- [ ] Read the modified `scripts/remote/bootstrap_pod.sh` end-to-end. Walk through all four scenarios and confirm the expected outcome.

**Scenario A — Fresh volume (no `.bootstrap_version` file):**
- Sanity check passes.
- Version gate: `[[ -f "$VOLUME_VERSION_FILE" ]]` → false → echoes `[slow path] no cached version, full install`.
- `NEED_INSTALL=true`.
- Install branch runs (apt + pip + model checks).
- Post-install probe runs. If pass: writes `2026.05.22-003` to version file → continues. If fail: `exit 2` (no version file written; container dies; sniper pages).
- On success: port cleanup, `exec python main.py`.
- ✓ Correct.

**Scenario B — Healthy fast path (version file content == `2026.05.22-003`, modules all importable):**
- Sanity check passes.
- Version gate: file matches → echoes `[version-gate] cached version 2026.05.22-003 matches; verifying modules...`
- `run_import_probe` returns 0 → echoes `[fast path] version match + modules present → skipping install`.
- `NEED_INSTALL=false` → install branch skipped → no version-file write (file already correct).
- Port cleanup, `exec python main.py`.
- ✓ Correct, ~3–5s slower than original fast path (the pre-probe cost).

**Scenario C — Version mismatch (file exists with `2026.05.21-002`):**
- Sanity check passes.
- Version gate: file exists but content `"2026.05.21-002"` ≠ `"2026.05.22-003"` → echoes `[slow path] cache version 2026.05.21-002 != 2026.05.22-003, reinstalling`.
- `NEED_INSTALL=true` (default).
- Install branch runs.
- Post-install probe runs → on pass, version file overwritten with `2026.05.22-003`.
- ✓ Correct. This is what every existing pod will hit on its first sniper catch after deploy.

**Scenario D — Lying version file (file content matches `2026.05.22-003` but modules missing — the #49 bug):**
- Sanity check passes.
- Version gate: file matches → echoes `[version-gate] cached version 2026.05.22-003 matches; verifying modules...`
- `run_import_probe` FAILS (prints `MISSING_MODULE=...` to stderr, returns 2). The `if run_import_probe; then` form suppresses `set -e`, so we fall through to the `else`.
- Echoes `[forced slow path] version file lies — modules MISSING, reinstalling`.
- `NEED_INSTALL` stays `true`.
- Install branch runs.
- Post-install probe runs.
  - On pass: version file overwritten with `2026.05.22-003` (now backed by a verified install). Container continues to ComfyUI launch.
  - On fail: `exit 2`, container dies, sniper pages — same outward symptom as today, but the version file is not "blessed" with a fresh write, so the next catch on this volume will hit the same `[forced slow path]` again (no inherited lie).
- ✓ Correct. Bug fixed for the typical scenario (install fixes the problem); fails loud for the pathological scenario (install can't fix it).

If any scenario trace produces a different outcome than the one above, stop and re-read your edits.

### Step 2.7: Commit

- [ ] Run:

```
git add scripts/remote/bootstrap_pod.sh
git commit -m "fix(runpod): probe-before-gate prevents container death on lying version cache (#49)"
```

---

## Final verification

### Step F.1: Read the final state of the script

- [ ] Read the full file. Confirm:
  1. Line 8: `BOOTSTRAP_VERSION="2026.05.22-003"`.
  2. `run_import_probe()` function defined once, after the sanity check, before the version gate.
  3. Version gate calls `run_import_probe` inside `if [[ "$CACHED_VERSION" == "$BOOTSTRAP_VERSION" ]]; then` — and **only** there.
  4. Install branch contains `if ! run_import_probe; then ... exit 2; fi` followed by the version-file write, before its closing `fi`.
  5. No probe call or version-file write outside the install branch.
  6. Port cleanup + `exec python main.py` are the last lines, unchanged from `main`.

### Step F.2: Verify commit history

- [ ] Run:

```
git log main..HEAD --oneline -- scripts/remote/bootstrap_pod.sh
```

Expected (the path filter restricts output to this file, so only the two new commits should appear, oldest to newest):

```
refactor(runpod): extract bootstrap import probe into run_import_probe() function
fix(runpod): probe-before-gate prevents container death on lying version cache (#49)
```

### Step F.3: Final syntax check

- [ ] Run:

```
bash -n scripts/remote/bootstrap_pod.sh
```

Expected: exits 0.

### Step F.4: Confirm the version bump landed

- [ ] Run:

```
grep '^BOOTSTRAP_VERSION=' scripts/remote/bootstrap_pod.sh
```

Expected: `BOOTSTRAP_VERSION="2026.05.22-003"`

### Step F.5: Diff-size sanity check

- [ ] Run:

```
git diff main -- scripts/remote/bootstrap_pod.sh
```

Expected shape:
- ~30 lines added near the top (function definition).
- ~30 lines removed near the bottom (old inline heredoc + unconditional version-file write).
- ~10 lines net change in the version-gate block (added pre-probe call + branch).
- ~6 lines added in the install branch tail (post-probe + version-file write).
- 1 line changed in `BOOTSTRAP_VERSION`.

Roughly +40 / −35 lines. If the diff is wildly different, re-read your edits.

### Step F.6: Optional — explicit shellcheck (if available)

- [ ] If `shellcheck` is installed locally, run:

```
shellcheck scripts/remote/bootstrap_pod.sh
```

Treat new warnings introduced by this plan as worth investigating. Pre-existing warnings (carried over from `main`) are out of scope.

If `shellcheck` is not installed, skip this step — `bash -n` already covers syntax; everything else is the manual scenario trace.

---

## Notes for the implementer

- **The python body inside `run_import_probe()` is byte-for-byte the existing heredoc.** Don't reformat it, don't rewrite the imports, don't change the print statements. Copying changes the bash exit-code semantics in subtle ways; the spec assumed identity.
- **`set -e` interaction with `if`:** `set -e` does NOT abort on a failing command inside `if cond; then ...`. That's why `if run_import_probe; then` and `if ! run_import_probe; then` are safe; they let the script inspect failure rather than dying.
- **`set -e` interaction with a plain function call:** a top-level `run_import_probe` (no `if`) WOULD abort the script on non-zero return. Task 1's intermediate state relies on this; Task 2 removes that call site and replaces it with explicit conditionals.
- **Deployment is out of scope for this plan.** Once both commits are merged, deploy to the live network volume per the "Deployment" section of the spec — `wget` the patched script to `/workspace/bootstrap.sh` and verify via a fresh sniper catch.
- **Don't touch the RunPod template, `runpod_client.py`, `pod_provisioner.py`, or `face_swap_engine.py`.** All out of scope.
- **Don't add tests.** No shell-test framework in this repo. Manual scenario trace + `bash -n` is the established verification practice.
- **If your edit accidentally introduces tabs:** the existing file uses spaces (4 wide). Preserve that.
- **Watch the heredoc carefully.** The closing `PYEOF` must be on its own line, at column 0 (no leading whitespace). The opening `<< 'PYEOF'` must be single-quoted to keep python `$mod` / `f"…"` syntax intact. Both Task 1's function definition and the deleted-bottom block must follow this convention.
- **`exec python main.py`** at the end transfers PID 1 to ComfyUI — anything after `exec` never runs. Don't add code after it.
