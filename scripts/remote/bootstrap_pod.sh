#!/usr/bin/env bash
# Jarvis V3 Pod Bootstrap Script
# Lives on network volume: /workspace/bootstrap.sh
# Invoked by RunPod template startCmd: bash -lc '/workspace/bootstrap.sh'

set -euo pipefail

BOOTSTRAP_VERSION="2026.06.17-001"  # bump forces reinstall: pulls occlusion models (face_yolov8m + sam_vit_b) + pins ultralytics==8.4.69 for ReActorMaskHelper YOLO
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

    echo "=== Step: ultralytics (YOLO for ReActorMaskHelper occlusion) ==="
    # Pin the version observed working in B-53; fall back to latest if that exact
    # version is unavailable on PyPI so a yanked pin can't brick provisioning
    # (this step runs under set -e). ultralytics' torch/torchvision deps are
    # lower-bound (>=), so an already-present torch 2.4 is not upgraded.
    pip install --quiet "ultralytics==8.4.69" || pip install --quiet ultralytics

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

    echo "=== Step: occlusion models for ReActorMaskHelper (Block M.2.6) ==="
    # Filenames MUST match the engine's mask node defaults
    # (VideoFaceSwapEngine._build_mask_helper_node): face_yolov8m.pt in
    # models/ultralytics/bbox, sam_vit_b_01ec64.pth in models/sams — the node's
    # bbox/sam dropdowns read exactly those dirs. URLs are env-overridable with
    # canonical public defaults (face-YOLO from Bingsu/adetailer; SAM from Meta).
    # NB: face_yolov8m.pt is a FACE-trained YOLO — NOT generic COCO yolov8m.
    FACE_YOLO_MODEL_URL="${FACE_YOLO_MODEL_URL:-https://huggingface.co/Bingsu/adetailer/resolve/main/face_yolov8m.pt}"
    SAM_VIT_B_MODEL_URL="${SAM_VIT_B_MODEL_URL:-https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth}"
    FACE_YOLO_DEST="$COMFYUI_DIR/models/ultralytics/bbox/face_yolov8m.pt"
    SAM_VIT_B_DEST="$COMFYUI_DIR/models/sams/sam_vit_b_01ec64.pth"

    if [[ ! -s "$FACE_YOLO_DEST" ]]; then
        echo "Downloading face_yolov8m.pt -> $FACE_YOLO_DEST"
        mkdir -p "$(dirname "$FACE_YOLO_DEST")"
        wget -q -O "$FACE_YOLO_DEST" "$FACE_YOLO_MODEL_URL" \
            || { echo "WARN: face_yolov8m.pt download failed"; rm -f "$FACE_YOLO_DEST"; }
    fi
    if [[ ! -s "$SAM_VIT_B_DEST" ]]; then
        echo "Downloading sam_vit_b_01ec64.pth -> $SAM_VIT_B_DEST"
        mkdir -p "$(dirname "$SAM_VIT_B_DEST")"
        wget -q -O "$SAM_VIT_B_DEST" "$SAM_VIT_B_MODEL_URL" \
            || { echo "WARN: sam_vit_b_01ec64.pth download failed"; rm -f "$SAM_VIT_B_DEST"; }
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