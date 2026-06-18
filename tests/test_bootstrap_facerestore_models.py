# -*- coding: utf-8 -*-
"""Guard: the pod bootstrap provisions the GPEN-1024 face-restore model.

ReActorFaceSwap's ``face_restore_model`` dropdown is populated by scanning
``models/facerestore_models/`` on the pod. The engine can now emit
``GPEN-BFR-1024.onnx`` for that input (opt-in via ``VIDEO_SWAP_FACE_RESTORE_MODEL``);
if the bootstrap doesn't download that exact file into that exact dir, the
``/prompt`` submit fails on a live pod with ``value_not_in_list``. This test pins
the bootstrap to the name/dir so the two can't drift. No pod required — static
text check, mirroring test_bootstrap_occlusion_models.py.

The bootstrap of record is ``scripts/remote/bootstrap_pod.sh`` (the RunPod
template wgets it from a pinned commit and runs it as /workspace/bootstrap.sh).
"""
from __future__ import annotations

from pathlib import Path

_BOOTSTRAP = Path("scripts/remote/bootstrap_pod.sh")


def test_bootstrap_provisions_gpen_1024():
    txt = _BOOTSTRAP.read_text(encoding="utf-8")
    assert "GPEN-BFR-1024.onnx" in txt, (
        "bootstrap must provision GPEN-BFR-1024.onnx (engine restore-model option)"
    )


def test_bootstrap_targets_facerestore_models_dir():
    # ReActor reads the face_restore_model dropdown from this dir; the download
    # target must land there or the node won't see the model.
    txt = _BOOTSTRAP.read_text(encoding="utf-8")
    assert "models/facerestore_models" in txt


def test_bootstrap_gpen_url_is_env_overridable_with_default():
    # Overridable URL (env with default) so a source change needs no code edit,
    # same convention as FACE_YOLO_MODEL_URL / SAM_VIT_B_MODEL_URL.
    txt = _BOOTSTRAP.read_text(encoding="utf-8")
    assert "GPEN_1024_MODEL_URL" in txt


def test_bootstrap_import_probe_verifies_gpen_file_exists():
    # strict probe-gate: the GPEN download is WARN-only (failed wget removes the
    # partial and continues), so without a file-existence check a silent miss
    # would cache a 'healthy' version and the node would only die at swap time.
    # The DEST path must be exported so the (quoted) probe heredoc reads it, and a
    # missing file must be appended to `missing` so the version-gate trips.
    txt = _BOOTSTRAP.read_text(encoding="utf-8")
    assert "export GPEN_1024_DEST=" in txt, (
        "GPEN DEST path must be exported for the probe heredoc"
    )
    assert "restore_model:" in txt, (
        "a missing GPEN file must be appended to `missing` so the gate trips"
    )
