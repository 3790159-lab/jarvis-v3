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
python -c "import insightface; print('insightface', insightface.__version__)" 2>/dev/null \
  || echo "WARN: insightface not importable - face-swap pipeline will fail"

echo ""
echo "=== Bootstrap complete: $(date -u) ==="
