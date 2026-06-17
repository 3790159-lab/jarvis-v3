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
