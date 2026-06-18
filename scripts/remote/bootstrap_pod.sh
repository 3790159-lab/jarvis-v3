#!/usr/bin/env bash
# Jarvis V3 Pod Bootstrap Script
# Lives on network volume: /workspace/bootstrap.sh
# Invoked by RunPod template startCmd: bash -lc '/workspace/bootstrap.sh'

set -euo pipefail

BOOTSTRAP_VERSION="2026.06.18-002"  # 06-18-002: + provision GPEN-BFR-1024.onnx in models/facerestore_models/ (sharper restore than GFPGAN; strict probe-gate). 06-18-001: + patch ReActorMaskHelper batch bug (move rgba2rgb_tensor+.cpu() OUT of the per-mask loop; crashed video on 2nd frame). 06-17: occlusion models + ultralytics==8.4.69 + probe gates on torch.cuda/onnxruntime-CUDA/occlusion-files
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
    # FACE_YOLO_DEST / SAM_VIT_B_DEST are exported at the top of the script so the
    # import probe can verify the files; the download targets reuse them here.

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

    echo "=== Step: GPEN-1024 face-restore model (sharper restore than GFPGAN) ==="
    # GPEN-BFR-1024.onnx into models/facerestore_models/ — the dir ReActor scans
    # for the face_restore_model dropdown. Engine emits this name when
    # VIDEO_SWAP_FACE_RESTORE_MODEL=GPEN-BFR-1024.onnx. URL env-overridable with the
    # canonical Gourieff/ReActor dataset default (HEAD-verified live, 200). Same
    # WARN-on-fail + rm-partial pattern as the occlusion models; GPEN_1024_DEST is
    # exported at the top so the probe can verify the file landed.
    GPEN_1024_MODEL_URL="${GPEN_1024_MODEL_URL:-https://huggingface.co/datasets/Gourieff/ReActor/resolve/main/models/facerestore_models/GPEN-BFR-1024.onnx}"
    if [[ ! -s "$GPEN_1024_DEST" ]]; then
        echo "Downloading GPEN-BFR-1024.onnx -> $GPEN_1024_DEST"
        mkdir -p "$(dirname "$GPEN_1024_DEST")"
        wget -q -O "$GPEN_1024_DEST" "$GPEN_1024_MODEL_URL" \
            || { echo "WARN: GPEN-BFR-1024.onnx download failed"; rm -f "$GPEN_1024_DEST"; }
    fi

    echo "=== [verify] post-install probe... ==="
    if ! run_import_probe; then
        echo "ERROR: post-install probe still failing — refusing to write version file" >&2
        exit 2
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
echo "=== Step: patch ReActorMaskHelper batch bug (rgba2rgb out of loop) ==="
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
echo "=== Step: clean port 8188 ==="
pkill -9 -f "python main.py" 2>/dev/null || true
sleep 2

# --- Launch ComfyUI ---
echo "=== Launching ComfyUI ==="
cd "$COMFYUI_DIR"
exec python main.py --listen 0.0.0.0 --port 8188