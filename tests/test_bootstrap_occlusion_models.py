# -*- coding: utf-8 -*-
"""Guard: the pod bootstrap provisions the occlusion models the engine asks for.

ReActorMaskHelper exposes ``bbox_model_name`` / ``sam_model_name`` dropdowns
populated from ``models/ultralytics/bbox`` and ``models/sams`` on the pod. The
video engine's mask node references specific default filenames; if the bootstrap
downloads different names (or different dirs), the dropdowns won't match and the
graph submit fails on a live pod. This test pins the engine defaults to the
bootstrap so the two can't drift. No pod required — pure text/static check.

The bootstrap of record is ``scripts/remote/bootstrap_pod.sh`` — the script the
RunPod template runs as ``/workspace/bootstrap.sh`` from the network volume (see
docs/runpod_template_config.md). An orphan duplicate (``runpod/bootstrap.sh``)
once existed but was removed, so ``scripts/remote/`` is the only target here.
"""
from __future__ import annotations

from pathlib import Path

from app.services.block_m2_face_swap.video_face_swap_engine import (
    VideoFaceSwapEngine,
)

_BOOTSTRAP = Path("scripts/remote/bootstrap_pod.sh")


def _engine_default_model_names(monkeypatch) -> tuple[str, str]:
    monkeypatch.delenv("VIDEO_SWAP_OCCLUSION_BBOX_MODEL", raising=False)
    monkeypatch.delenv("VIDEO_SWAP_OCCLUSION_SAM_MODEL", raising=False)
    ins = VideoFaceSwapEngine._build_mask_helper_node()["inputs"]
    return ins["bbox_model_name"], ins["sam_model_name"]


def test_bootstrap_provisions_exactly_the_models_the_engine_references(monkeypatch):
    bbox, sam = _engine_default_model_names(monkeypatch)
    txt = _BOOTSTRAP.read_text(encoding="utf-8")
    assert bbox in txt, f"bootstrap does not provision engine bbox model {bbox!r}"
    assert sam in txt, f"bootstrap does not provision engine sam model {sam!r}"


def test_bootstrap_targets_the_dirs_reactor_reads_dropdowns_from():
    txt = _BOOTSTRAP.read_text(encoding="utf-8")
    assert "models/ultralytics/bbox" in txt
    assert "models/sams" in txt


def test_bootstrap_model_urls_are_env_overridable_with_defaults():
    txt = _BOOTSTRAP.read_text(encoding="utf-8")
    # overridable URLs (env with default), so a source change needs no code edit
    assert "FACE_YOLO_MODEL_URL" in txt
    assert "SAM_VIT_B_MODEL_URL" in txt


def test_bootstrap_pip_installs_pinned_ultralytics():
    # ReActorMaskHelper.load_yolo does `from ultralytics import YOLO`; ultralytics
    # is NOT a transitive dep of the other installed packages, so without an
    # explicit install the occlusion mask dies with NameError: YOLO not defined
    # before ComfyUI launches. Pin the version observed installed in B-53 (8.4.69).
    txt = _BOOTSTRAP.read_text(encoding="utf-8")
    assert "ultralytics==8.4.69" in txt, (
        "bootstrap must pip install pinned ultralytics==8.4.69 (B-53)"
    )


def test_bootstrap_import_probe_verifies_ultralytics():
    # The probe's modules_to_check drives both the version-gate reinstall and the
    # post-install fail-loud check; ultralytics must be verified like the others.
    txt = _BOOTSTRAP.read_text(encoding="utf-8")
    assert "'ultralytics'" in txt, (
        "import probe must verify 'ultralytics' so a volume missing it reinstalls"
    )


def test_bootstrap_import_probe_gates_on_torch_cuda():
    # SAM + the YOLO detector run on torch via ComfyUI's get_torch_device(); the
    # onnxruntime CUDA probe does NOT cover torch, so the probe must fail loud when
    # torch can't see CUDA — otherwise the occlusion path silently runs on CPU.
    txt = _BOOTSTRAP.read_text(encoding="utf-8")
    assert "torch.cuda.is_available" in txt, (
        "import probe must check torch.cuda.is_available()"
    )
    assert "torch:cuda" in txt, (
        "a torch-CUDA failure must be appended to `missing` so the gate trips"
    )


def test_bootstrap_import_probe_verifies_occlusion_model_files_exist():
    # The occlusion model downloads are WARN-only (a failed wget removes the partial
    # and continues); without a file-existence check the version file could cache a
    # 'healthy' state while a download silently failed, and the node would only die
    # at swap time. The probe must assert the files physically exist, and the DEST
    # paths must be exported so the (quoted) probe heredoc can read them via env.
    txt = _BOOTSTRAP.read_text(encoding="utf-8")
    assert "export FACE_YOLO_DEST=" in txt and "export SAM_VIT_B_DEST=" in txt, (
        "occlusion model DEST paths must be exported for the probe heredoc"
    )
    assert "occlusion_model:" in txt, (
        "a missing occlusion-model file must be appended to `missing` so the gate trips"
    )


# ── ReActorMaskHelper batch-bug patch (extract the embedded patch & run it) ───
# The bootstrap runs a python patch (a `<< 'PATCHEOF'` heredoc) that moves the
# reactor node's `result = rgba2rgb_tensor(result)` + `.cpu()` OUT of the
# per-mask loop (in-loop they corrupt `result` to RGB(3)/CPU and the next frame's
# 4-channel blend crashes — video breaks on the 2nd frame). These tests run the
# REAL embedded patch against a fixture mirroring the on-volume nodes.py.
import os
import re
import subprocess
import sys


def _extract_patch_heredoc() -> str:
    txt = _BOOTSTRAP.read_text(encoding="utf-8")
    m = re.search(r"<< 'PATCHEOF'\n(.*?)\nPATCHEOF", txt, re.DOTALL)
    assert m, "PATCHEOF heredoc not found in bootstrap"
    return m.group(1)


_BUGGY_NODE = (
    "        result = image_base.detach().clone()\n"
    "        for i in range(0, MB):\n"
    "            if is_empty[i]:\n"
    "                pbar.update(1)\n"
    "                continue\n"
    "            else:\n"
    "                result[image_index] = pasting * paste_mask + result[image_index] * (1. - paste_mask)\n"
    "\n"
    "                face_segment = result\n"
    "\n"
    "                face_segment[...,3] = mask[i]\n"
    "\n"
    "                result = rgba2rgb_tensor(result)\n"
    "                result = result.cpu()  # Перемещаем\n"
    "\n"
    "                pbar.update(1)\n"
    "\n"
    "        return (result, combined_mask, mask_blurred, face_segment)\n"
)


def _run_patch(target: Path) -> str:
    code = _extract_patch_heredoc()
    env = {**os.environ, "REACTOR_NODES": str(target)}
    r = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env
    )
    assert r.returncode == 0, f"patch exited {r.returncode}: {r.stderr}"
    return r.stdout


def test_reactor_patch_moves_rgba2rgb_out_of_loop(tmp_path):
    f = tmp_path / "nodes.py"
    f.write_text(_BUGGY_NODE, encoding="utf-8")
    out = _run_patch(f)
    assert "APPLIED" in out
    patched = f.read_text(encoding="utf-8")
    # the in-loop conversion pair is gone
    assert (
        "                result = rgba2rgb_tensor(result)\n"
        "                result = result.cpu()"
    ) not in patched
    # a single post-loop conversion is inserted right before the return, marked
    assert (
        "        result = rgba2rgb_tensor(result).cpu()  "
        "# JARVIS-PATCH rgba2rgb-out-of-loop\n        return (result,"
    ) in patched


def test_reactor_patch_is_idempotent(tmp_path):
    f = tmp_path / "nodes.py"
    f.write_text(_BUGGY_NODE, encoding="utf-8")
    _run_patch(f)
    once = f.read_text(encoding="utf-8")
    out2 = _run_patch(f)
    assert "already applied" in out2
    assert f.read_text(encoding="utf-8") == once  # second run is a no-op


def test_reactor_patch_noop_when_pattern_absent(tmp_path):
    # A future node version without the in-loop pattern must WARN and leave the
    # file untouched — never brick provisioning.
    f = tmp_path / "nodes.py"
    f.write_text("def execute(self):\n    return (result,)\n", encoding="utf-8")
    before = f.read_text(encoding="utf-8")
    out = _run_patch(f)
    assert "WARN" in out
    assert f.read_text(encoding="utf-8") == before
