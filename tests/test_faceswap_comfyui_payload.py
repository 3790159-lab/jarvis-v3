# -*- coding: utf-8 -*-
"""Unit tests for the ComfyUI face-swap payload assembly (no network, free).

Guards the pivot-2026-06-21 backend: /faceswap must build a payload identical to
the validated manual run (our face_swap_only.json, version 82c95ab…, inswapper +
GFPGAN, vis=1.0), with the "_comment" key stripped so cog-comfyui doesn't crash.
"""
from __future__ import annotations

import base64
import io
import json
import zipfile

from app.services.block_m_common.faceswap_client import _FACESWAP_VERSION
import app.services.face_swap as fs
from app.services.face_swap import (
    _LUCATACO_FACESWAP_VERSION,
    _build_swap_payload,
    _load_swap_graph,
    _looks_censored,
    _zip_two,
    face_swap_basic,
    face_swap_lucataco,
)


def test_lucataco_payload_uses_urls_directly(monkeypatch):
    captured = {}

    def fake_post(url, payload, api_key):
        captured["url"] = url
        captured["payload"] = payload
        return "pred_123"

    monkeypatch.setattr(fs, "_get_api_key", lambda: "tok")
    monkeypatch.setattr(fs, "_post_prediction", fake_post)
    monkeypatch.setattr(fs, "poll_replicate", lambda pid, key, **kw: "https://out/result.jpg")

    out = face_swap_lucataco("https://tg/source.jpg", "https://tg/target.jpg")
    assert out == "https://out/result.jpg"
    assert captured["url"] == fs._PREDICTIONS_BASE
    assert captured["payload"]["version"] == _LUCATACO_FACESWAP_VERSION
    inp = captured["payload"]["input"]
    # URLs passed through directly — no zip/data-uri.
    assert inp["swap_image"] == "https://tg/source.jpg"
    assert inp["target_image"] == "https://tg/target.jpg"


def test_face_swap_basic_routes_to_lucataco(monkeypatch):
    seen = {}
    monkeypatch.setattr(fs, "face_swap_lucataco",
                        lambda s, t: seen.update(s=s, t=t) or "ok")
    assert face_swap_basic("S", "T") == "ok"
    assert seen == {"s": "S", "t": "T"}


def _png(width: int, height: int, size: int) -> bytes:
    """Minimal bytes mimicking a PNG with the given IHDR dimensions and length."""
    head = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR"
    head += width.to_bytes(4, "big") + height.to_bytes(4, "big")
    return head + b"\x00" * max(0, size - len(head))


def test_looks_censored_detects_512_black_placeholder():
    # ReActor's NSFW placeholder: tiny 512x512 PNG.
    assert _looks_censored(_png(512, 512, 842)) is True


def test_looks_censored_passes_real_swap():
    # Real swaps are large and not 512x512.
    assert _looks_censored(_png(955, 1280, 1_254_291)) is False
    # Even a 512x512 image that is large (not the sub-KB placeholder) is allowed.
    assert _looks_censored(_png(512, 512, 200_000)) is False
    assert _looks_censored(b"") is False


def test_load_swap_graph_strips_comment_and_keeps_nodes():
    graph = _load_swap_graph()
    # "_comment" (a string value) must be gone — it crashes cog-comfyui.
    assert "_comment" not in graph
    assert all(isinstance(v, dict) for v in graph.values())
    # The 4 real nodes survive: LoadImage x2, ReActorFaceSwap, SaveImage.
    classes = sorted(n.get("class_type") for n in graph.values())
    assert classes == ["LoadImage", "LoadImage", "ReActorFaceSwap", "SaveImage"]


def test_load_swap_graph_default_restore_matches_validated_run():
    graph = _load_swap_graph()
    reactor = next(n for n in graph.values() if n["class_type"] == "ReActorFaceSwap")
    assert reactor["inputs"]["face_restore_model"] == "GFPGANv1.4.pth"
    assert reactor["inputs"]["swap_model"] == "inswapper_128.onnx"
    assert reactor["inputs"]["face_restore_visibility"] == 1.0


def test_load_swap_graph_sets_custom_restore_model():
    graph = _load_swap_graph("GPEN-BFR-1024.onnx")
    reactor = next(n for n in graph.values() if n["class_type"] == "ReActorFaceSwap")
    assert reactor["inputs"]["face_restore_model"] == "GPEN-BFR-1024.onnx"


def test_zip_two_contains_both_named_images():
    data = _zip_two(b"AAAA", b"BBBB")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert sorted(zf.namelist()) == ["source.jpg", "target.jpg"]
        assert zf.read("source.jpg") == b"AAAA"
        assert zf.read("target.jpg") == b"BBBB"


def test_build_swap_payload_shape_and_version():
    graph = _load_swap_graph()
    payload = _build_swap_payload(graph, _zip_two(b"AAAA", b"BBBB"))

    assert payload["version"] == _FACESWAP_VERSION
    inp = payload["input"]
    assert inp["output_format"] == "png"
    assert inp["return_temp_files"] is False
    assert inp["input_file"].startswith("data:application/zip;base64,")

    # input_file decodes back to a valid zip with our two images.
    b64 = inp["input_file"].split(",", 1)[1]
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(b64))) as zf:
        assert sorted(zf.namelist()) == ["source.jpg", "target.jpg"]

    # workflow_json is a JSON string parsing to the node graph (no _comment).
    wf = json.loads(inp["workflow_json"])
    assert "_comment" not in wf
    assert any(n.get("class_type") == "ReActorFaceSwap" for n in wf.values())
