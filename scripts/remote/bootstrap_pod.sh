#!/usr/bin/env bash
# scripts/remote/bootstrap_pod.sh
#
# Runs on the RunPod pod at container start (via the RunPod template's
# startCmd = `bash -lc '/workspace/bootstrap.sh'`). Repo is source of
# truth; the actual file at /workspace/bootstrap.sh is pasted onto the
# network volume once per environment via the RunPod web terminal.
#
# Contract (matches docs/superpowers/specs/2026-05-21-sniper-provisioning-
# callback-design.md):
#   - Idempotent: safe to run on every container boot.
#   - Versioned-manifest gate: BOOTSTRAP_VERSION constant + on-volume
#     /workspace/.bootstrap_version. Mismatch → slow install path.
#   - Import probe: ALWAYS runs, even on warm cache. Fails fast (exit 1)
#     with MISSING_MODULE log line that the human reads in RunPod
#     console logs.
#   - On success: exec python main.py so ComfyUI becomes PID 1; its
#     exit propagates as container exit → CONTAINER_EXITED to the
#     Python provisioner.

set -euo pipefail

BOOTSTRAP_VERSION="2026.05.21-001"
VOLUME_VERSION_FILE="/workspace/.bootstrap_version"
LOG_FILE="/workspace/.bootstrap_log"

# Mirror stdout/stderr to a log file on the volume for after-the-fact debug.
exec > >(tee -a "$LOG_FILE") 2>&1

echo "[bootstrap] start version=$BOOTSTRAP_VERSION at $(date -Is)"

# 1. Activate venv (created during first cold setup; lives on /workspace)
if [[ ! -f /workspace/venv/bin/activate ]]; then
    echo "[bootstrap] FATAL: /workspace/venv missing — initial setup not done"
    echo "MISSING_VENV=/workspace/venv"
    exit 1
fi
# shellcheck disable=SC1091
source /workspace/venv/bin/activate

# 2. Cache check
cached_version="$(cat "$VOLUME_VERSION_FILE" 2>/dev/null || echo "")"
if [[ "$cached_version" != "$BOOTSTRAP_VERSION" ]]; then
    echo "[bootstrap] version mismatch (cached='$cached_version' wanted='$BOOTSTRAP_VERSION') — running slow install"
    # Slow install path — refine these with the actual install commands
    # from yesterday's manual transcript when the human first builds the
    # on-volume copy. Examples:
    #   pip install -r /workspace/requirements.txt
    #   pip install "transformers<4.45"  # PyTorch 2.4 compat pin
    #   apt-get update && apt-get install -y ffmpeg
    #   git -C /workspace/ComfyUI/custom_nodes/comfyui-reactor-node pull
    echo "[bootstrap] (slow install steps run here — see comments above)"
else
    echo "[bootstrap] cache HIT — skipping slow install"
fi

# 3. Import probe (ALWAYS runs, even on warm cache)
echo "[bootstrap] running import probe"
python - <<'PY' || { echo "MISSING_MODULE=$?"; exit 1; }
import importlib, sys

# Modules whose missing imports broke things in the 2026-05-20 manual fix.
# Extend this list as new failure modes surface.
MODULES = [
    "insightface",
    "segment_anything",
    "onnxruntime",
    "transformers",
]

failed = []
for m in MODULES:
    try:
        importlib.import_module(m)
    except Exception as e:
        failed.append(f"{m}: {type(e).__name__}: {e}")

if failed:
    sys.stderr.write("IMPORT_PROBE_FAILED:\n")
    for line in failed:
        sys.stderr.write(f"  MISSING_MODULE={line}\n")
    sys.exit(1)
print("[bootstrap] import probe OK")
PY

# 4. Mark version on success
echo "$BOOTSTRAP_VERSION" > "$VOLUME_VERSION_FILE"

# 5. Launch ComfyUI as PID 1
echo "[bootstrap] launching ComfyUI"
cd /workspace/ComfyUI
exec python main.py --listen 0.0.0.0 --port 8188
