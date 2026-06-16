#!/usr/bin/env bash
set -euo pipefail

echo "=== Bootstrap start: $(date -u) ==="

# 1. ffmpeg (fix for VHS_VideoCombine failure)
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq ffmpeg

# 2. ComfyUI deps
COMFY_DIR="${COMFY_DIR:-/workspace/ComfyUI}"
cd "$COMFY_DIR" || { echo "ERROR: $COMFY_DIR not found"; exit 1; }
pip install -q -r requirements.txt
pip install --break-system-packages -r "$COMFY_DIR/requirements.txt"

# 3b. Ensure custom_nodes are present (idempotent)
mkdir -p "$COMFY_DIR/custom_nodes"
cd "$COMFY_DIR/custom_nodes"

_clone_node() {
  local dir="$1"
  local url="$2"
  if [ -z "$url" ]; then
    echo "TODO: canonical git URL unknown for $dir; skipping clone"
    return 0
  fi
  if [ -d "$dir" ]; then
    echo "$dir already present, skipping clone"
  else
    echo "Cloning $dir from $url"
    git clone --depth 1 "$url" "$dir" || {
      echo "WARN: failed to clone $dir from $url"
      return 0
    }
  fi
  if [ -f "$dir/requirements.txt" ]; then
    pip install --break-system-packages -r "$dir/requirements.txt" 2>/dev/null || true
  fi
}

_clone_node ComfyUI-VideoHelperSuite     "https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite"
_clone_node ComfyUI-KJNodes               "https://github.com/kijai/ComfyUI-KJNodes"
# TODO: confirm canonical URL for ComfyUI-PainterI2Vadvanced via web search
_clone_node ComfyUI-PainterI2Vadvanced    "https://github.com/painter-research/ComfyUI-PainterI2Vadvanced"
_clone_node ComfyUI_essentials            "https://github.com/cubiq/ComfyUI_essentials"
# Day-7 verify: live pods serve class "RIFEInterpolation" (model flownet.pkl)
# from custom_nodes/ComfyUI-VFI — NOT Fannovel16's "RIFE VFI" (rife47.pth) that
# this URL would install. The engine's _apply_fps targets the verified live
# schema. Why the installed node differs from this clone URL is unresolved
# (clone may be skipped because the dir pre-exists on the network volume) —
# tracked as a separate bootstrap-drift task; do NOT change this URL without
# re-verifying /object_info on a fresh pod.
_clone_node ComfyUI-VFI                   "https://github.com/Fannovel16/ComfyUI-Frame-Interpolation"
# TODO: canonical URL for ComfyUI-wanBlockswap unknown - skipped
_clone_node ComfyUI-wanBlockswap          ""
_clone_node ComfyUI-Manager                "https://github.com/ltdrdata/ComfyUI-Manager"
_clone_node rgthree-comfy                  "https://github.com/rgthree/rgthree-comfy"

# Block M.2.5 face-swap pipeline: ReActor node + InsightFace.
_clone_node comfyui-reactor-node           "https://github.com/Gourieff/comfyui-reactor-node"
echo "Installing InsightFace + onnxruntime-gpu for ReActor (Block M.2.5)..."
pip install --break-system-packages insightface onnxruntime-gpu 2>/dev/null || \
  pip install --break-system-packages insightface onnxruntime || \
  echo "WARN: insightface install failed - ReActor will not work until fixed"

# Block M.2.6 occlusion: ReActorMaskHelper needs a face-detector (bbox) + SAM so
# objects in front of the face (a hand/food) are restored instead of painted
# over. Filenames MUST match the engine's mask node defaults
# (VideoFaceSwapEngine._build_mask_helper_node): face_yolov8m.pt in
# models/ultralytics/bbox, sam_vit_b_01ec64.pth in models/sams. The node's
# dropdowns read exactly those dirs. URLs are env-overridable with defaults at
# the canonical public sources (face-YOLO from Bingsu/adetailer; SAM from Meta).
# NB: face_yolov8m.pt is a FACE-trained YOLO — NOT the generic COCO yolov8m.
FACE_YOLO_MODEL_URL="${FACE_YOLO_MODEL_URL:-https://huggingface.co/Bingsu/adetailer/resolve/main/face_yolov8m.pt}"
SAM_VIT_B_MODEL_URL="${SAM_VIT_B_MODEL_URL:-https://dl.fbaipublicfiles.com/segment_anything/sam_vit_b_01ec64.pth}"

_fetch_model() {
  local url="$1" dest="$2"
  if [ -f "$dest" ]; then
    echo "$(basename "$dest") already present, skipping download"
    return 0
  fi
  mkdir -p "$(dirname "$dest")"
  echo "Downloading $(basename "$dest") from $url"
  if command -v wget >/dev/null 2>&1; then
    wget -q -O "$dest" "$url" || { echo "WARN: failed to download $dest"; rm -f "$dest"; return 0; }
  else
    curl -fsSL -o "$dest" "$url" || { echo "WARN: failed to download $dest"; rm -f "$dest"; return 0; }
  fi
}

echo "Provisioning Block M.2.6 occlusion models (face bbox + SAM)..."
_fetch_model "$FACE_YOLO_MODEL_URL" "$COMFY_DIR/models/ultralytics/bbox/face_yolov8m.pt"
_fetch_model "$SAM_VIT_B_MODEL_URL" "$COMFY_DIR/models/sams/sam_vit_b_01ec64.pth"

# 3. Custom node (idempotent) - kept for backward compatibility with prior bootstrap
cd "$COMFY_DIR/custom_nodes"
if [ ! -d ComfyUI-PainterI2Vadvanced ]; then
  git clone --depth 1 https://github.com/princepainter/ComfyUI-PainterI2Vadvanced
else
  echo "ComfyUI-PainterI2Vadvanced already present, skipping clone"
fi

# 4. Verification
echo ""
echo "=== Verification ==="
echo "GPU:"
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
echo "ffmpeg:"
ffmpeg -version | head -1
echo "PyTorch + CUDA:"
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available(), torch.cuda.device_count())"
echo "ComfyUI critical deps:"
python -c "import sqlalchemy; print('sqlalchemy', sqlalchemy.__version__)"
python -c "import cv2; print('opencv-python', cv2.__version__)"
python -c "import git; print('gitpython', git.__version__)"
python -c "import imageio_ffmpeg; print('imageio-ffmpeg', imageio_ffmpeg.__version__)"
echo "Workflow file on volume:"
ls -la /workspace/workflow_v20.json 2>/dev/null || ls -la /workspace/*.json 2>/dev/null || echo "WARN: workflow_v20.json not found at /workspace/"

echo "Verifying custom nodes..."
if ls -d "$COMFY_DIR/custom_nodes/ComfyUI-VideoHelperSuite" > /dev/null 2>&1; then
  echo "OK: VHS_VideoCombine available"
else
  echo "WARN: ComfyUI-VideoHelperSuite missing - VHS_VideoCombine node will fail"
fi
if ls -d "$COMFY_DIR/custom_nodes/comfyui-reactor-node" > /dev/null 2>&1; then
  echo "OK: ReActorFaceSwap available (Block M.2.5)"
else
  echo "WARN: comfyui-reactor-node missing - ReActorFaceSwap node will fail"
fi
for _m in "models/ultralytics/bbox/face_yolov8m.pt" "models/sams/sam_vit_b_01ec64.pth"; do
  if [ -s "$COMFY_DIR/$_m" ]; then
    echo "OK: occlusion model present ($_m, $(du -h "$COMFY_DIR/$_m" | cut -f1))"
  else
    echo "WARN: occlusion model missing ($_m) - ReActorMaskHelper dropdown will be empty"
  fi
done
python -c "import insightface; print('insightface', insightface.__version__)" 2>/dev/null \
  || echo "WARN: insightface not importable - face-swap pipeline will fail"

echo ""
echo "=== Bootstrap complete: $(date -u) ==="
