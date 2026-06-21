"""Face Swap Engine — runs OUR ComfyUI graph on Replicate, plus face enhancement.

As of the 2026-06-21 pivot (jarvis-strategy-pivot-replicate), /faceswap runs our
own ComfyUI face_swap_only.json graph on comfyui/any-comfyui-workflow-a100
(ReActor + inswapper_128 + GFPGAN) instead of third-party black-box models. The
old cdingram/codeplugtech path is kept in RESERVE below (off the critical path).
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict

# Single source of truth for the model version + the no-retry billable-failure
# exception. Keeps the sync bot path and the async router/video path in sync.
from app.services.block_m_common.faceswap_client import (
    _FACESWAP_VERSION,
    PredictionFailed,
)

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW_PATH = _ROOT / "app" / "services" / "block_m2_face_swap" / "workflows" / "face_swap_only.json"

_PREDICTIONS_BASE = "https://api.replicate.com/v1/predictions"
_GFPGAN_URL = "https://api.replicate.com/v1/models/tencentarc/gfpgan/predictions"

# Replicate's Cloudflare bans the default "Python-urllib/x.y" User-Agent (403 /
# error 1010). Any non-urllib UA passes. Set one on every urllib request here.
_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# Default face-restore model — matches the validated manual test exactly.
_DEFAULT_RESTORE_MODEL = "GFPGANv1.4.pth"

# Stopgap turnkey model: lucataco/faceswap (bare inswapper, NO NSFW censorship —
# unlike the Path A ComfyUI ReActor SFW fork). ~$0.0001-0.0007/run, accepts image
# URLs directly. See jarvis-replicate-pathA-nsfw-censorship.
_LUCATACO_FACESWAP_VERSION = "9a4298548422074c3f57258c5d544497314ae4112df80d116f0d2109e843d20d"


class NSFWFiltered(RuntimeError):
    """The bundled SFW ReActor's NSFW detector censored the swap to a black image.

    Path A only (any-comfyui-workflow ships the SFW ReActor fork). The detector
    false-positives on benign photos. See jarvis-replicate-pathA-nsfw-censorship.
    """


def _looks_censored(img_bytes: bytes) -> bool:
    """True if the output is ReActor's fixed 512x512 black NSFW placeholder.

    A real swap returns the target's resolution at hundreds of KB; the censored
    placeholder is a ~sub-KB 512x512 black PNG. Detect via PNG IHDR dimensions +
    tiny size, with no hard dependency on Pillow.
    """
    if not img_bytes or len(img_bytes) > 20_000:  # real swaps are far larger
        return False
    if img_bytes[:8] == b"\x89PNG\r\n\x1a\n" and len(img_bytes) >= 24:
        width = int.from_bytes(img_bytes[16:20], "big")
        height = int.from_bytes(img_bytes[20:24], "big")
        return (width, height) == (512, 512)
    return False

# ── RESERVE (off critical path — pivot 2026-06-21) ──────────────────────────────
# Third-party single-purpose models, kept for rollback. NOT used by /faceswap.
# These were the dependency that broke in May 2026 and triggered the RunPod work.
_FACE_SWAP_VERSION = "d1d6ea8c8be89d664a07a457526f7128109dee7030fdac424788d762c71ed111"  # cdingram/face-swap
_FACE_SWAP_REACTOR_VERSION = "278a81e7ebb22db98bcba54de985d22cc1abeead2754eb1f2af717247be69b34"  # codeplugtech/face-swap
# ────────────────────────────────────────────────────────────────────────────────


def _get_api_key() -> str:
    # Standardize on the token persona/FLUX uses; fall back to the legacy name.
    key = (os.getenv("REPLICATE_API_TOKEN", "") or os.getenv("REPLICATE_API_KEY", "")).strip()
    if not key:
        raise ValueError("REPLICATE_API_TOKEN not set")
    return key


def _post_prediction(url: str, payload: Dict[str, Any], api_key: str) -> str:
    """Submit a prediction and return its ID."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Token {api_key}",
            "Content-Type": "application/json",
            "User-Agent": _USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            prediction = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode(errors="replace")[:500]
        except Exception:
            pass
        if exc.code == 404:
            raise RuntimeError(
                "Face swap модель временно недоступна. "
                "Попробуй /enhance вместо /faceswap "
                "или подожди когда модель обновится."
            ) from exc
        raise RuntimeError(f"Replicate submit failed HTTP {exc.code}: {body}") from exc

    pred_id = prediction.get("id")
    if not pred_id:
        raise RuntimeError(f"Replicate returned no prediction id: {prediction}")
    return pred_id


def poll_replicate(prediction_id: str, api_key: str, max_wait: int = 120) -> str:
    """Poll Replicate until prediction succeeds. Returns output URL.

    Raises PredictionFailed (a RuntimeError) on failed/canceled. There is no
    retry loop around predictions in this module, so a billable failure raises
    once and is never re-run — do not wrap this in a retry.
    """
    for _ in range(max_wait):
        time.sleep(1)
        req = urllib.request.Request(
            f"{_PREDICTIONS_BASE}/{prediction_id}",
            headers={"Authorization": f"Token {api_key}", "User-Agent": _USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = json.loads(resp.read())
        state = status.get("status", "")
        if state == "succeeded":
            output = status.get("output")
            return output[0] if isinstance(output, list) else output
        if state in ("failed", "canceled"):
            raise PredictionFailed(
                f"Replicate prediction {prediction_id} {state}: {status.get('error')}"
            )
    raise TimeoutError(f"Replicate prediction {prediction_id} timed out after {max_wait}s")


def _load_swap_graph(restore_model: str = _DEFAULT_RESTORE_MODEL) -> Dict[str, Any]:
    """Load our ComfyUI face_swap_only.json graph, ready for submission.

    Drops non-node top-level keys (e.g. "_comment", which crashes cog-comfyui
    with 'str' object has no attribute 'get') and sets face_restore_model on the
    ReActor node so callers can pick GFPGAN (default) or GPEN later.
    """
    raw = json.loads(_WORKFLOW_PATH.read_text(encoding="utf-8"))
    graph = {k: v for k, v in raw.items() if isinstance(v, dict)}
    for node in graph.values():
        if node.get("class_type") in ("ReActorFaceSwap", "ReActorFaceSwapOpt"):
            node.setdefault("inputs", {})["face_restore_model"] = restore_model
    return graph


def _zip_two(source_bytes: bytes, target_bytes: bytes) -> bytes:
    """Zip two images under the exact names the graph's LoadImage nodes expect."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("source.jpg", source_bytes)
        zf.writestr("target.jpg", target_bytes)
    return buf.getvalue()


def _build_swap_payload(graph: Dict[str, Any], zip_bytes: bytes) -> Dict[str, Any]:
    """Build the version-based /v1/predictions payload for any-comfyui-workflow."""
    data_uri = "data:application/zip;base64," + base64.b64encode(zip_bytes).decode()
    return {
        "version": _FACESWAP_VERSION,
        "input": {
            "workflow_json": json.dumps(graph),
            "input_file": data_uri,
            "output_format": "png",
            "return_temp_files": False,
        },
    }


def _download_bytes(url: str) -> bytes:
    """Fetch a URL's bytes (sync). Sets a UA — replicate.delivery is Cloudflare-fronted."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def face_swap_comfyui(
    source_image_url: str,
    target_image_url: str,
    restore_model: str = _DEFAULT_RESTORE_MODEL,
) -> str:
    """Run OUR ComfyUI face-swap graph on Replicate. Returns output image URL.

    Puts the source face onto the target image using face_swap_only.json
    (ReActor + inswapper_128 + face restore) on comfyui/any-comfyui-workflow-a100.
    With the default restore model this matches the validated manual test exactly.
    """
    api_key = _get_api_key()
    graph = _load_swap_graph(restore_model)
    zip_bytes = _zip_two(
        _download_bytes(source_image_url),
        _download_bytes(target_image_url),
    )
    payload = _build_swap_payload(graph, zip_bytes)
    pred_id = _post_prediction(_PREDICTIONS_BASE, payload, api_key)
    logger.info("Face swap (ComfyUI graph): prediction %s submitted", pred_id)
    # Our A100 graph can cold-start beyond the default 120s budget.
    result_url = poll_replicate(pred_id, api_key, max_wait=600)
    # Band-aid: the SFW ReActor censors flagged content to a 512x512 black image.
    # Surface a clear message instead of returning a black square to the user.
    if _looks_censored(_download_bytes(result_url)):
        logger.warning("Face swap %s censored by ReActor NSFW filter", pred_id)
        raise NSFWFiltered("Фото отклонено NSFW-фильтром модели")
    return result_url


def face_swap_lucataco(source_image_url: str, target_image_url: str) -> str:
    """Uncensored turnkey face swap via lucataco/faceswap (bare inswapper).

    Stopgap while Path B (our own NSFW-free ComfyUI graph) is built. lucataco
    takes image URLs directly (no zip/download needed) and has NO NSFW black-out,
    so it handles revealing-but-benign content that the Path A ReActor censored.

    Args:
        source_image_url: URL of the face to transplant (source).
        target_image_url: URL of the scene to swap the face into (target).

    Returns:
        Output image URL.

    Raises:
        PredictionFailed: On a billable failed/canceled prediction (never retried).
    """
    api_key = _get_api_key()
    payload = {
        "version": _LUCATACO_FACESWAP_VERSION,
        "input": {
            "swap_image": source_image_url,
            "target_image": target_image_url,
        },
    }
    pred_id = _post_prediction(_PREDICTIONS_BASE, payload, api_key)
    logger.info("Face swap (lucataco): prediction %s submitted", pred_id)
    # No retry loop here → a billable PredictionFailed raises once, never re-run.
    return poll_replicate(pred_id, api_key, max_wait=300)


def face_swap_basic(source_image_url: str, target_image_url: str) -> str:
    """Active /faceswap backend → uncensored lucataco stopgap.

    Switched off our Path A ComfyUI graph (face_swap_comfyui), which is kept in
    reserve for Path B — its NSFW filter false-positived on benign photos.
    """
    return face_swap_lucataco(source_image_url, target_image_url)


def face_swap_reactor(source_image_url: str, target_image_url: str) -> str:
    """Alias kept for callers — same ComfyUI graph as face_swap_basic."""
    return face_swap_comfyui(source_image_url, target_image_url, _DEFAULT_RESTORE_MODEL)


# ── RESERVE (off critical path — pivot 2026-06-21) ──────────────────────────────
# Original third-party implementations, kept for rollback. Restore by pointing
# face_swap_basic/face_swap_reactor back at these bodies and re-adding the swap_*
# payloads with _FACE_SWAP_VERSION / _FACE_SWAP_REACTOR_VERSION.
#
# def _face_swap_basic_cdingram(source_image_url, target_image_url):
#     api_key = _get_api_key()
#     payload = {"version": _FACE_SWAP_VERSION,
#                "input": {"swap_image": source_image_url, "input_image": target_image_url}}
#     pred_id = _post_prediction(_PREDICTIONS_BASE, payload, api_key)
#     return poll_replicate(pred_id, api_key)
#
# def _face_swap_reactor_codeplugtech(source_image_url, target_image_url):
#     api_key = _get_api_key()
#     payload = {"version": _FACE_SWAP_REACTOR_VERSION,
#                "input": {"swap_image": source_image_url, "input_image": target_image_url}}
#     pred_id = _post_prediction(_PREDICTIONS_BASE, payload, api_key)
#     return poll_replicate(pred_id, api_key)
# ────────────────────────────────────────────────────────────────────────────────


def enhance_face(image_url: str, scale: int = 2) -> str:
    """Apply GFPGAN v1.4 to enhance face quality."""
    api_key = _get_api_key()
    payload = {
        "input": {
            "img": image_url,
            "version": "v1.4",
            "scale": scale,
        }
    }
    pred_id = _post_prediction(_GFPGAN_URL, payload, api_key)
    logger.info("GFPGAN enhance: prediction %s submitted", pred_id)
    return poll_replicate(pred_id, api_key)


def face_swap_with_polish(source_url: str, target_url: str) -> str:
    """Polished tier — currently identical to basic (our graph already restores).

    Dormant: no UI button routes here until GPEN-BFR-1024 lands. The GPEN upgrade
    is a one-field change: face_swap_comfyui(..., "GPEN-BFR-1024.onnx").
    """
    return face_swap_comfyui(source_url, target_url, _DEFAULT_RESTORE_MODEL)


def estimate_swap_cost(polished: bool = False) -> float:
    """Return estimated cost for the swap pipeline."""
    basic = 0.005
    gfpgan = 0.002
    return round(basic + (gfpgan if polished else 0.0), 4)
