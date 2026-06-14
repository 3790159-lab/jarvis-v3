# -*- coding: utf-8 -*-
"""Structural tests for the video face-swap ComfyUI workflow.

The graph is ``VHS_LoadVideo -> ReActorFaceSwap -> VHS_VideoCombine`` with the
source face from a ``LoadImage`` node, audio passed through from the loaded
video to the combiner. These tests pin the node wiring and that the ReActor
parameters match the proven still-image workflow (``face_swap_only.json``) — no
pod required.
"""
from __future__ import annotations

import json
from pathlib import Path

_WF_DIR = Path("app/services/block_m2_face_swap/workflows")
_VIDEO_WF = _WF_DIR / "video_face_swap.json"
_IMAGE_WF = _WF_DIR / "face_swap_only.json"


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def test_workflow_file_exists_and_is_valid_json():
    wf = _load(_VIDEO_WF)
    assert isinstance(wf, dict) and wf


def test_workflow_has_the_four_required_node_classes():
    wf = _load(_VIDEO_WF)
    classes = {n["class_type"] for n in wf.values() if isinstance(n, dict) and "class_type" in n}
    assert {"VHS_LoadVideo", "LoadImage", "ReActorFaceSwap", "VHS_VideoCombine"} <= classes


def _node_of_class(wf: dict, cls: str) -> tuple[str, dict]:
    for nid, node in wf.items():
        if isinstance(node, dict) and node.get("class_type") == cls:
            return nid, node
    raise AssertionError(f"no node of class {cls}")


def test_reactor_consumes_loaded_video_frames_and_source_face():
    wf = _load(_VIDEO_WF)
    lv_id, _ = _node_of_class(wf, "VHS_LoadVideo")
    li_id, _ = _node_of_class(wf, "LoadImage")
    _, reactor = _node_of_class(wf, "ReActorFaceSwap")
    # frames (IMAGE, output 0) feed ReActor.input_image
    assert reactor["inputs"]["input_image"] == [lv_id, 0]
    # the still source face feeds ReActor.source_image
    assert reactor["inputs"]["source_image"] == [li_id, 0]


def test_video_combine_consumes_reactor_output_and_passes_audio_through():
    wf = _load(_VIDEO_WF)
    lv_id, _ = _node_of_class(wf, "VHS_LoadVideo")
    reactor_id, _ = _node_of_class(wf, "ReActorFaceSwap")
    _, combine = _node_of_class(wf, "VHS_VideoCombine")
    assert combine["inputs"]["images"] == [reactor_id, 0]
    # ⚠️ ASSUMPTION (pod-verify): VHS_LoadVideo exposes audio as output index 2.
    # The audio MUST originate from the loaded video so the output keeps sound.
    assert combine["inputs"]["audio"][0] == lv_id


def test_reactor_params_match_proven_image_workflow():
    video = _load(_VIDEO_WF)
    image = _load(_IMAGE_WF)
    _, v_reactor = _node_of_class(video, "ReActorFaceSwap")
    _, i_reactor = _node_of_class(image, "ReActorFaceSwap")
    for key in ("swap_model", "facedetection", "face_restore_model"):
        assert v_reactor["inputs"][key] == i_reactor["inputs"][key]
    assert v_reactor["inputs"]["swap_model"] == "inswapper_128.onnx"
    assert v_reactor["inputs"]["face_restore_model"] == "GFPGANv1.4.pth"


def test_video_combine_outputs_mp4():
    wf = _load(_VIDEO_WF)
    _, combine = _node_of_class(wf, "VHS_VideoCombine")
    assert "mp4" in combine["inputs"]["format"]
    assert combine["inputs"]["save_output"] is True
