#!/usr/bin/env bash
# Jarvis V3 Pod Bootstrap Script
# Lives on network volume: /workspace/bootstrap.sh
# Invoked by RunPod template startCmd: bash -lc '/workspace/bootstrap.sh'

set -euo pipefail

BOOTSTRAP_VERSION="2026.06.15-001"
VOLUME_VERSION_FILE="/workspace/.bootstrap_version"
LOG_FILE="/workspace/.bootstrap_log"
COMFYUI_DIR="/workspace/ComfyUI"

# --- Logging ---
exec > >(tee -a "$LOG_FILE") 2>&1
echo "=== bootstrap.sh starting at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "BOOTSTRAP_VERSION=$BOOTSTRAP_VERSION"

# --- Sanity check ---
if [[ ! -d "$COMFYUI_DIR" ]]; then
    echo "ERROR: COMFYUI_NOT_FOUND ($COMFYUI_DIR)" >&2
    exit 1
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

    echo "=== Step: enforce onnxruntime-gpu (drop conflicting CPU build) ==="
    # insightface (and ComfyUI/ReActor) pull the CPU 'onnxruntime' wheel in as a
    # transitive dep, so the earlier `pip install onnxruntime-gpu insightface ...`
    # leaves BOTH installed. The CPU build then wins and CUDAExecutionProvider
    # disappears → ReActor runs on CPU. Remove both, then reinstall ONLY the GPU
    # build, and do it LAST (after every other dep) so nothing re-pulls the CPU
    # wheel. Idempotent: `uninstall -y` is a no-op when a package is absent
    # (guarded with `|| true` so it can't trip `set -e`). CUDA 12.4 / cuDNN 9.1
    # libs are already on the volume, so no extra runtime install is needed.
    pip uninstall -y onnxruntime onnxruntime-gpu || true
    pip install --quiet onnxruntime-gpu

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

    echo "=== [verify] post-install probe... ==="
    if ! run_import_probe; then
        echo "ERROR: post-install probe still failing — refusing to write version file" >&2
        exit 2
    fi

    echo "$BOOTSTRAP_VERSION" > "$VOLUME_VERSION_FILE"
    echo "=== Version cached: $BOOTSTRAP_VERSION ==="
fi

# --- Kill any existing ComfyUI process (port 8188 conflict prevention) ---
echo "=== Step: clean port 8188 ==="
pkill -9 -f "python main.py" 2>/dev/null || true
sleep 2

# --- Launch ComfyUI ---
echo "=== Launching ComfyUI ==="
cd "$COMFYUI_DIR"
exec python main.py --listen 0.0.0.0 --port 8188