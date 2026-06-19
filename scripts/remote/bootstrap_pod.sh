#!/usr/bin/env bash
# Jarvis V3 Pod Bootstrap Script
# Lives on network volume: /workspace/bootstrap.sh
# Invoked by RunPod template startCmd: bash -lc '/workspace/bootstrap.sh'

set -Eeuo pipefail  # -E: ERR trap propagates into functions/subshells (P19)

BOOTSTRAP_VERSION="2026.06.20-001"  # 06-20-001: P19 park-alive — ERR trap → die_park (sleep infinity) on ANY pre-launch failure instead of exit→RunPod container restart-loop (FIXES P17, which was a restart-loop: every step lines 26-285 ran under set -e with NO survival, so any failing apt/pip/unzip/probe killed PID1 → RunPod restarted → loop, sshd never stayed up). Guarded logging redirect so an unmounted/unwritable /workspace no longer crashes boot before the first echo. Sanity-check + post-install-probe now park-alive instead of exit 1/2 (pod stays SSH-inspectable). P29: retry() wrapper (3x + 5s backoff) on apt-get + all pip installs → transient blip retries, genuine failure parks (not loops). 06-19-002: P17 heartbeat — hb() prints a UTC-timestamped marker before every heavy step (apt/pip/wget/probe), visible in the RunPod web-console container logs even before sshd is up (the ONLY visibility window during a boot hang); + wget --timeout=30 --tries=3 on ALL 5 model downloads. ROOT CAUSE of the ~25-min boot hang (P17): bare `wget -q` defaults to read-timeout 900s × up to 20 tries, so a single stalled HF/fbaipublicfiles socket hangs the boot for many minutes with zero log output, then the cost-guardian reaps the pod (pure 404/502, never reached ComfyUI). 06-19-001: P14 supervise ComfyUI with crash-retry (replace `exec python main.py` that killed the container on any crash -> 404/502 loop); stdout+stderr -> /workspace/logs/comfyui.log for get_pod_logs; park pod alive after N fails for inspection. 06-18-002: + provision GPEN-BFR-1024.onnx in models/facerestore_models/ (sharper restore than GFPGAN; strict probe-gate). 06-18-001: + patch ReActorMaskHelper batch bug (move rgba2rgb_tensor+.cpu() OUT of the per-mask loop; crashed video on 2nd frame). 06-17: occlusion models + ultralytics==8.4.69 + probe gates on torch.cuda/onnxruntime-CUDA/occlusion-files
VOLUME_VERSION_FILE="/workspace/.bootstrap_version"
LOG_FILE="/workspace/.bootstrap_log"
COMFYUI_DIR="/workspace/ComfyUI"

# Occlusion model destinations — defined here (not just in the install path) and
# exported so the import probe, which runs BOTH pre-gate and post-install, can
# assert the files actually landed. Their filenames/dirs must match the engine's
# ReActorMaskHelper defaults (see test_bootstrap_occlusion_models.py).
export FACE_YOLO_DEST="$COMFYUI_DIR/models/ultralytics/bbox/face_yolov8m.pt"
export SAM_VIT_B_DEST="$COMFYUI_DIR/models/sams/sam_vit_b_01ec64.pth"
# GPEN-1024 face-restore model. ReActor's face_restore_model dropdown scans
# models/facerestore_models/; the engine emits this name when
# VIDEO_SWAP_FACE_RESTORE_MODEL=GPEN-BFR-1024.onnx (see
# test_bootstrap_facerestore_models.py). Exported so the probe can verify it.
export GPEN_1024_DEST="$COMFYUI_DIR/models/facerestore_models/GPEN-BFR-1024.onnx"

# --- P19: park-alive on ANY failure (fixes the restart-loop) ---
# Defined BEFORE the logging redirect so even a redirect/mount failure parks the
# pod ALIVE (sleep infinity) for SSH inspection instead of exiting → RunPod
# restart loop. Any unhandled non-zero (set -e) hits the ERR trap → die_park.
die_park(){ echo "=== BOOTSTRAP FATAL: $* — parking pod ALIVE (sleep infinity) for SSH inspection ==="; sleep infinity; }
trap 'die_park "unhandled error at line $LINENO (rc=$?)"' ERR

# retry CMD... — run a heavy/network step up to 3x with 5s backoff. On final
# failure return non-zero so the ERR trap parks the pod (no silent restart loop).
# Used on apt/pip (which, unlike the 5 wgets, had no timeout/retry). Calls inside
# this function are part of `&&`/tested context, so set -e doesn't fire mid-retry.
retry(){ local n=1 max=3; while true; do "$@" && return 0; if [ "$n" -ge "$max" ]; then echo "FAIL after $max tries: $*"; return 1; fi; echo "[retry $n/$max failed] $* — sleeping 5s"; n=$((n+1)); sleep 5; done; }

# --- Logging (guarded: an unmounted/unwritable /workspace must NOT crash boot) ---
mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || true
if (: >> "$LOG_FILE") 2>/dev/null; then
    exec > >(tee -a "$LOG_FILE") 2>&1
else
    echo "WARN: $LOG_FILE not writable (network volume not mounted?) — logging to container stdout only"
fi
echo "=== bootstrap.sh starting at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "BOOTSTRAP_VERSION=$BOOTSTRAP_VERSION"

# --- P17 heartbeat helper (timestamped marker before each heavy step) ---
# Visible in the RunPod web-console container logs even before sshd comes up —
# the ONLY visibility window during a boot hang. Pair "start"/"done" around each
# heavy step (apt, pip, wget) so a stall pins the EXACT step instead of leaving a
# silent multi-minute gap. Cheap (one echo), runs on every path.
hb(){ echo "[HB $(date -u +%H:%M:%SZ)] $*"; }

# --- Sanity check ---
if [[ ! -d "$COMFYUI_DIR" ]]; then
    die_park "COMFYUI_NOT_FOUND ($COMFYUI_DIR) — network volume not mounted?"
fi

# --- Import probe (callable; invoked pre-gate and post-install) ---
run_import_probe() {
    python << 'PYEOF'
import sys
modules_to_check = [
    'insightface',
    'segment_anything',
    'onnxruntime',
    'git',
    'ultralytics',
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

# When both onnxruntime (CPU) and onnxruntime-gpu are installed, the CPU build
# shadows CUDA and get_available_providers() drops CUDAExecutionProvider — so
# ReActor (inswapper/GFPGAN/retinaface) silently runs on CPU (hours per video).
# A bare `import onnxruntime` can't catch this, so probe the provider list:
# treating a missing CUDA provider as "unhealthy" makes the version-gate force a
# reinstall on existing volumes AND makes the post-install probe fail loud
# instead of leaving the pod to grind on CPU.
try:
    import onnxruntime as _ort
    _providers = _ort.get_available_providers()
    if 'CUDAExecutionProvider' in _providers:
        print(f"  OK onnxruntime CUDAExecutionProvider ({_providers})")
    else:
        missing.append('onnxruntime:CUDAExecutionProvider')
        print(
            f"  FAIL onnxruntime CUDAExecutionProvider absent; "
            f"providers={_providers}",
            file=sys.stderr,
        )
except Exception as e:
    missing.append('onnxruntime:CUDAExecutionProvider')
    print(f"  FAIL onnxruntime provider check: {e}", file=sys.stderr)

# torch CUDA gate: SAM (occlusion segmentation) and the YOLO face detector run on
# torch via ComfyUI's model_management.get_torch_device(); the ReActorMaskHelper
# node exposes NO device input, so if torch can't see CUDA the whole occlusion
# path silently runs on CPU (seconds per frame → a short clip times out). A bare
# `import onnxruntime` CUDA check does NOT cover torch, so probe it explicitly and
# fail loud at boot instead of grinding on CPU at swap time on the Nth node.
try:
    import torch
    if torch.cuda.is_available():
        print(f"  OK torch.cuda ({torch.cuda.get_device_name(0)})")
    else:
        missing.append('torch:cuda')
        print(
            "  FAIL torch.cuda.is_available()==False — SAM/YOLO would run on CPU",
            file=sys.stderr,
        )
except Exception as e:
    missing.append('torch:cuda')
    print(f"  FAIL torch import/cuda check: {e}", file=sys.stderr)

# Occlusion model FILES must physically exist. The download steps below are
# WARN-only (a failed wget removes the partial and continues), so without this
# check a silent download miss would cache a "healthy" version and the node would
# only die at swap time. Verifying here makes the version-gate force a reinstall
# on a volume that's missing them, and makes the post-install probe refuse to
# write the version file — fail loud, immediately.
import os
for _label, _path in (
    ("face_yolov8m.pt", os.environ.get("FACE_YOLO_DEST", "")),
    ("sam_vit_b_01ec64.pth", os.environ.get("SAM_VIT_B_DEST", "")),
):
    if _path and os.path.isfile(_path) and os.path.getsize(_path) > 0:
        print(f"  OK occlusion model {_label}")
    else:
        missing.append(f"occlusion_model:{_label}")
        print(
            f"  FAIL occlusion model {_label} missing/empty at {_path!r}",
            file=sys.stderr,
        )

# GPEN-1024 restore model FILE must physically exist (strict gate, same rationale
# as the occlusion files: the download below is WARN-only, so without this check a
# silent miss would cache a 'healthy' version and the node would die at swap time
# with value_not_in_list). Separate from the occlusion loop so it tags `missing`
# with a restore_model: prefix.
_gpen = os.environ.get("GPEN_1024_DEST", "")
if _gpen and os.path.isfile(_gpen) and os.path.getsize(_gpen) > 0:
    print("  OK restore model GPEN-BFR-1024.onnx")
else:
    missing.append("restore_model:GPEN-BFR-1024.onnx")
    print(
        f"  FAIL restore model GPEN-BFR-1024.onnx missing/empty at {_gpen!r}",
        file=sys.stderr,
    )

if missing:
    for mod in missing:
        print(f"MISSING_MODULE={mod}", file=sys.stderr)
    sys.exit(2)
print("Import probe: ALL OK")
PYEOF
}

# --- Version gate (probe-before-skip) ---
NEED_INSTALL=true
if [[ -f "$VOLUME_VERSION_FILE" ]]; then
    CACHED_VERSION=$(cat "$VOLUME_VERSION_FILE")
    if [[ "$CACHED_VERSION" == "$BOOTSTRAP_VERSION" ]]; then
        hb "version-gate: cached $CACHED_VERSION matches — running import probe (imports torch/insightface/onnxruntime; can take 30-90s)..."
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

# --- Slow install path ---
if [[ "$NEED_INSTALL" == "true" ]]; then
    hb "Step: apt update + install system deps (apt-get update + ffmpeg/unzip)"
    retry apt-get update -qq
    retry apt-get install -y unzip ffmpeg

    hb "Step: pip install ComfyUI requirements.txt (heavy; resolver can backtrack silently)"
    retry pip install -r "$COMFYUI_DIR/requirements.txt"

    hb "Step: pip install face swap deps (onnxruntime-gpu/insightface/segment_anything/gitpython)"
    retry pip install --quiet \
        onnxruntime-gpu \
        insightface \
        segment_anything \
        gitpython

    hb "Step: pip install ultralytics==8.4.69 (YOLO for ReActorMaskHelper occlusion)"
    # Pin the version observed working in B-53; fall back to latest if that exact
    # version is unavailable on PyPI so a yanked pin can't brick provisioning
    # (this step runs under set -e). ultralytics' torch/torchvision deps are
    # lower-bound (>=), so an already-present torch 2.4 is not upgraded.
    pip install --quiet "ultralytics==8.4.69" || pip install --quiet ultralytics

    hb "Step: pip force-reinstall transformers<4.45 (re-downloads the wheel)"
    retry pip install --force-reinstall --quiet "transformers<4.45"

    hb "Step: enforce onnxruntime-gpu (uninstall both + reinstall GPU-only; re-downloads ~200M wheel)"
    # insightface (and ComfyUI/ReActor) pull the CPU 'onnxruntime' wheel in as a
    # transitive dep, so the earlier `pip install onnxruntime-gpu insightface ...`
    # leaves BOTH installed. The CPU build then wins and CUDAExecutionProvider
    # disappears → ReActor runs on CPU. Remove both, then reinstall ONLY the GPU
    # build, and do it LAST (after every other dep) so nothing re-pulls the CPU
    # wheel. Idempotent: `uninstall -y` is a no-op when a package is absent
    # (guarded with `|| true` so it can't trip `set -e`). CUDA 12.4 / cuDNN 9.1
    # libs are already on the volume, so no extra runtime install is needed.
    pip uninstall -y onnxruntime onnxruntime-gpu || true
    retry pip install --quiet onnxruntime-gpu

    hb "Step: verify model files on volume (inswapper + buffalo_l)"
    INSWAPPER="$COMFYUI_DIR/models/insightface/inswapper_128.onnx"
    BUFFALO_DIR="$COMFYUI_DIR/models/insightface/models/buffalo_l"

    if [[ ! -f "$INSWAPPER" ]]; then
        echo "WARNING: $INSWAPPER missing - re-downloading"
        mkdir -p "$COMFYUI_DIR/models/insightface"
        cd "$COMFYUI_DIR/models/insightface"
        hb "download inswapper_128.onnx (~530M, huggingface) start"
        wget -q --timeout=30 --tries=3 https://huggingface.co/ezioruan/inswapper_128.onnx/resolve/main/inswapper_128.onnx -O inswapper_128.onnx
        hb "download inswapper_128.onnx done"
    fi

    if [[ ! -d "$BUFFALO_DIR" ]] || [[ -z "$(ls -A $BUFFALO_DIR 2>/dev/null)" ]]; then
        echo "WARNING: buffalo_l models missing - re-downloading"
        mkdir -p "$BUFFALO_DIR"
        cd "$COMFYUI_DIR/models/insightface/models"
        hb "download buffalo_l.zip (~280M, github releases) start"
        wget -q --timeout=30 --tries=3 https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip
        hb "download buffalo_l.zip done; unzipping"
        unzip -o buffalo_l.zip -d buffalo_l/
        rm -f buffalo_l.zip
    fi

    hb "Step: occlusion models for ReActorMaskHelper (face_yolov8m.pt + sam_vit_b)"
    # Filenames MUST match the engine's mask node defaults
    # (VideoFaceSwapEngine._build_mask_helper_node): face_yolov8m.pt in
    # models/ultralytics/bbox, sam_vit_b_01ec64.pth in models/sams — the node's
    # bbox/sam dropdowns read exactly those dirs. URLs are env-overridable with
    # canonical public defaults (face-YOLO from Bingsu/adetailer; SAM from Meta).
    # NB: face_yolov8m.pt is a FACE-trained YOLO — NOT generic COCO yolov8m.
    FACE_YOLO_MODEL_URL="${FACE_YOLO_MODEL_URL:-https://huggingface.co/Bingsu/adetailer/resolve/main/face_yolov8m.pt}"
    SAM_VIT_B_MODEL_URL="${SAM_VIT_B_MODEL_URL:-https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth}"
    # FACE_YOLO_DEST / SAM_VIT_B_DEST are exported at the top of the script so the
    # import probe can verify the files; the download targets reuse them here.

    if [[ ! -s "$FACE_YOLO_DEST" ]]; then
        hb "download face_yolov8m.pt (~50M, huggingface) start -> $FACE_YOLO_DEST"
        mkdir -p "$(dirname "$FACE_YOLO_DEST")"
        wget -q --timeout=30 --tries=3 -O "$FACE_YOLO_DEST" "$FACE_YOLO_MODEL_URL" \
            || { echo "WARN: face_yolov8m.pt download failed"; rm -f "$FACE_YOLO_DEST"; }
        hb "download face_yolov8m.pt done"
    fi
    if [[ ! -s "$SAM_VIT_B_DEST" ]]; then
        hb "download sam_vit_b_01ec64.pth (~375M, dl.fbaipublicfiles — historically flaky) start -> $SAM_VIT_B_DEST"
        mkdir -p "$(dirname "$SAM_VIT_B_DEST")"
        wget -q --timeout=30 --tries=3 -O "$SAM_VIT_B_DEST" "$SAM_VIT_B_MODEL_URL" \
            || { echo "WARN: sam_vit_b_01ec64.pth download failed"; rm -f "$SAM_VIT_B_DEST"; }
        hb "download sam_vit_b_01ec64.pth done"
    fi

    hb "Step: GPEN-1024 face-restore model (sharper restore than GFPGAN)"
    # GPEN-BFR-1024.onnx into models/facerestore_models/ — the dir ReActor scans
    # for the face_restore_model dropdown. Engine emits this name when
    # VIDEO_SWAP_FACE_RESTORE_MODEL=GPEN-BFR-1024.onnx. URL env-overridable with the
    # canonical Gourieff/ReActor dataset default (HEAD-verified live, 200). Same
    # WARN-on-fail + rm-partial pattern as the occlusion models; GPEN_1024_DEST is
    # exported at the top so the probe can verify the file landed.
    GPEN_1024_MODEL_URL="${GPEN_1024_MODEL_URL:-https://huggingface.co/datasets/Gourieff/ReActor/resolve/main/models/facerestore_models/GPEN-BFR-1024.onnx}"
    if [[ ! -s "$GPEN_1024_DEST" ]]; then
        hb "download GPEN-BFR-1024.onnx (~280M, huggingface) start -> $GPEN_1024_DEST"
        mkdir -p "$(dirname "$GPEN_1024_DEST")"
        wget -q --timeout=30 --tries=3 -O "$GPEN_1024_DEST" "$GPEN_1024_MODEL_URL" \
            || { echo "WARN: GPEN-BFR-1024.onnx download failed"; rm -f "$GPEN_1024_DEST"; }
        hb "download GPEN-BFR-1024.onnx done"
    fi

    hb "[verify] post-install import probe (torch/insightface/onnxruntime + model-file gates)..."
    if ! run_import_probe; then
        echo "ERROR: post-install probe still failing — refusing to write version file" >&2
        die_park "post-install import probe failed (missing module or model file)"
    fi

    echo "$BOOTSTRAP_VERSION" > "$VOLUME_VERSION_FILE"
    echo "=== Version cached: $BOOTSTRAP_VERSION ==="
fi

# --- Patch ReActorMaskHelper batch bug (runs EVERY boot, not just install) ---
# Gourieff/ComfyUI-ReActor @6ad6b35 does `result = rgba2rgb_tensor(result)` +
# `result = result.cpu()` INSIDE the per-mask paste loop, so after the first
# frame `result` is RGB(3)/CPU and the next frame's 4-channel blend crashes with
# "tensor a (3) must match b (4)". Fine for a single image (loop runs once),
# broken for video (MB>1). Move both conversions to AFTER the loop. Idempotent
# (marker-guarded) and non-fatal (a node-structure change just warns) so a future
# upstream fix can't brick provisioning. Runs outside the install gate because the
# node lives on the volume and must be patched even on the fast path.
hb "Step: patch ReActorMaskHelper batch bug (rgba2rgb out of loop)"
REACTOR_NODES="$COMFYUI_DIR/custom_nodes/comfyui-reactor-node/nodes.py"
if [[ -f "$REACTOR_NODES" ]]; then
    REACTOR_NODES="$REACTOR_NODES" python << 'PATCHEOF'
import os, re, sys
p = os.environ["REACTOR_NODES"]
src = open(p, encoding="utf-8").read()
MARK = "# JARVIS-PATCH rgba2rgb-out-of-loop"
if MARK in src:
    print("  reactor batch patch already applied")
    sys.exit(0)
# Remove the two in-loop conversion lines (16-space indent, inside the per-mask
# loop). The .cpu() line carries a trailing comment, so match to end-of-line.
pat = re.compile(
    r"\n[ ]{16}result = rgba2rgb_tensor\(result\)\n"
    r"[ ]{16}result = result\.cpu\(\)[^\n]*\n"
)
new, n = pat.subn("\n", src)
if n != 1:
    print(f"  WARN reactor batch patch: in-loop pattern not found (n={n}) — "
          f"node changed? leaving unpatched")
    sys.exit(0)
ret = "        return (result, combined_mask, mask_blurred, face_segment)"
if ret not in new:
    print("  WARN reactor batch patch: return anchor missing; leaving unpatched")
    sys.exit(0)
new = new.replace(
    ret,
    f"        result = rgba2rgb_tensor(result).cpu()  {MARK}\n{ret}",
    1,
)
open(p, "w", encoding="utf-8").write(new)
print("  reactor batch patch APPLIED")
PATCHEOF
else
    echo "  (reactor node not present yet — skipping patch)"
fi

# --- Kill any existing ComfyUI process (port 8188 conflict prevention) ---
hb "Step: clean port 8188 (pkill old ComfyUI)"
pkill -9 -f "python main.py" 2>/dev/null || true
# Belt-and-braces: free the port even if a non-'python main.py' holder lingers.
if command -v fuser >/dev/null 2>&1; then fuser -k 8188/tcp 2>/dev/null || true; fi
sleep 2

# --- Pre-launch model sanity (logged; non-fatal — the probe gate above already
# enforced presence. This just makes the log show what's on disk so a ComfyUI
# node-import failure is easy to correlate). ---
hb "Step: pre-launch model check (du -h each model file)"
for f in "$GPEN_1024_DEST" "$FACE_YOLO_DEST" "$SAM_VIT_B_DEST"; do
    if [ -f "$f" ]; then
        echo "  [ok] $(du -h "$f" 2>/dev/null | cut -f1)  $f"
    else
        echo "  [MISSING] $f"
    fi
done

# --- Launch ComfyUI under a crash-retry supervisor (P14) ---
# Was `exec python main.py ...`, which REPLACED this shell: any ComfyUI crash
# killed the container's main process with no restart, so RunPod cycled the pod
# 404<->502 forever and re-ran the whole bootstrap on each container restart.
# Now we supervise: capture stdout+stderr to a volume log that get_pod_logs()
# reads, retry a few times, then park the pod ALIVE for inspection rather than
# crash-looping (the cost guardian, RUNPOD_MAX_POD_LIFETIME_MIN, caps lifetime).
mkdir -p /workspace/logs
COMFY_LOG="/workspace/logs/comfyui.log"
cd "$COMFYUI_DIR"
set +e  # ComfyUI's exit code is handled manually below; don't let -e kill us
max_attempts=5
attempt=1
while [ "$attempt" -le "$max_attempts" ]; do
    echo "=== Launching ComfyUI (attempt $attempt/$max_attempts) at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
    echo "----- ComfyUI start attempt $attempt $(date -u +%Y-%m-%dT%H:%M:%SZ) -----" >> "$COMFY_LOG"
    python main.py --listen 0.0.0.0 --port 8188 >> "$COMFY_LOG" 2>&1
    code=$?
    echo "=== ComfyUI exited code=$code (attempt $attempt/$max_attempts) ==="
    echo "----- ComfyUI exited code=$code at $(date -u +%Y-%m-%dT%H:%M:%SZ) -----" >> "$COMFY_LOG"
    # Clean stop (0) or SIGTERM (143, deliberate pod stop) -> do not retry.
    if [ "$code" -eq 0 ] || [ "$code" -eq 143 ]; then
        echo "ComfyUI stopped cleanly (code $code); not retrying."
        break
    fi
    attempt=$((attempt + 1))
    if [ "$attempt" -le "$max_attempts" ]; then
        echo "--- crash tail (last 30 lines of $COMFY_LOG) ---"
        tail -n 30 "$COMFY_LOG"
        pkill -9 -f "python main.py" 2>/dev/null || true  # re-free port
        echo "Restarting ComfyUI in 10s..."
        sleep 10
    fi
done

if [ "$attempt" -gt "$max_attempts" ]; then
    echo "!!! ComfyUI failed $max_attempts times — parking pod ALIVE for inspection."
    echo "!!! Logs: $COMFY_LOG and $LOG_FILE (read via get_pod_logs or web console)."
    echo "!!! Cost guardian (RUNPOD_MAX_POD_LIFETIME_MIN) will cap this pod."
    tail -n 60 "$COMFY_LOG"
    sleep infinity
fi