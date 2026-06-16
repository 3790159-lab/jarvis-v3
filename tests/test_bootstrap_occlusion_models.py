# -*- coding: utf-8 -*-
"""Guard: the pod bootstrap provisions the occlusion models the engine asks for.

ReActorMaskHelper exposes ``bbox_model_name`` / ``sam_model_name`` dropdowns
populated from ``models/ultralytics/bbox`` and ``models/sams`` on the pod. The
video engine's mask node references specific default filenames; if the bootstrap
downloads different names (or different dirs), the dropdowns won't match and the
graph submit fails on a live pod. This test pins the engine defaults to the
bootstrap so the two can't drift. No pod required — pure text/static check.
"""
from __future__ import annotations

from pathlib import Path

from app.services.block_m2_face_swap.video_face_swap_engine import (
    VideoFaceSwapEngine,
)

_BOOTSTRAP = Path("runpod/bootstrap.sh")


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
