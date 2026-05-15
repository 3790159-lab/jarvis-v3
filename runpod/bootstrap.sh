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
pip install -q opencv-python gitpython toml imageio-ffmpeg sqlalchemy

# 3. Custom node (idempotent)
cd custom_nodes
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

echo ""
echo "=== Bootstrap complete: $(date -u) ==="