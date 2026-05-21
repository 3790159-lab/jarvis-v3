#!/usr/bin/env bash
# Jarvis V3 Pod Bootstrap Script
# Lives on network volume: /workspace/bootstrap.sh
# Invoked by RunPod template startCmd: bash -lc '/workspace/bootstrap.sh'

set -euo pipefail

BOOTSTRAP_VERSION="2026.05.21-002"
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

# --- Mark version as installed ---
echo "$BOOTSTRAP_VERSION" > "$VOLUME_VERSION_FILE"
echo "=== Version cached: $BOOTSTRAP_VERSION ==="

# --- Kill any existing ComfyUI process (port 8188 conflict prevention) ---
echo "=== Step: clean port 8188 ==="
pkill -9 -f "python main.py" 2>/dev/null || true
sleep 2

# --- Launch ComfyUI ---
echo "=== Launching ComfyUI ==="
cd "$COMFYUI_DIR"
exec python main.py --listen 0.0.0.0 --port 8188