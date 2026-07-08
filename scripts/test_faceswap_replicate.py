# -*- coding: utf-8 -*-
"""ONE paid test run (~$0.04) of our ComfyUI photo face-swap on Replicate A100.

Usage:
    python scripts/test_faceswap_replicate.py [source.jpg] [target.jpg]

Defaults to test/source.jpg + test/target.jpg. Loads .env (REPLICATE_API_TOKEN)
the same way the bot does, zips the two photos, runs face_swap_only.json on
comfyui/any-comfyui-workflow-a100, prints elapsed time + output URL, and saves
the result to test/output_swap.<ext>.

Step 1 of the RunPod->Replicate pivot — photo swap only, defaults (GFPGAN, vis=1.0).
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import httpx

# Make the project importable when run from the repo root.
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from app.core.env_bootstrap import bootstrap_env  # noqa: E402
from app.services.block_m_common.faceswap_client import FaceSwapProvider  # noqa: E402

_WORKFLOW = _ROOT / "app/services/block_m2_face_swap/workflows/face_swap_only.json"
_TEST_DIR = _ROOT / "test"


def _build_zip(source: Path, target: Path) -> bytes:
    """Zip the two photos under the exact names the workflow's LoadImage expects."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(source, arcname="source.jpg")
        zf.write(target, arcname="target.jpg")
    return buf.getvalue()


async def _main() -> int:
    env_path = bootstrap_env()
    print(f"[env] loaded {env_path}")

    source = Path(sys.argv[1]) if len(sys.argv) > 1 else _TEST_DIR / "source.jpg"
    target = Path(sys.argv[2]) if len(sys.argv) > 2 else _TEST_DIR / "target.jpg"
    for label, p in (("source", source), ("target", target)):
        if not p.exists():
            print(f"[ERROR] {label} photo not found: {p}")
            print(f"        Drop two JPGs at {_TEST_DIR}\\source.jpg and target.jpg")
            return 2

    workflow = json.loads(_WORKFLOW.read_text(encoding="utf-8"))
    print(f"[workflow] {_WORKFLOW.name}: {len(workflow)} nodes "
          f"(classes: {sorted({v.get('class_type') for v in workflow.values() if isinstance(v, dict)} - {None})})")

    input_zip = _build_zip(source, target)
    print(f"[zip] source={source.name} target={target.name} -> {len(input_zip)} bytes")

    provider = FaceSwapProvider()
    print("[run] submitting to comfyui/any-comfyui-workflow-a100 ...")
    t0 = time.perf_counter()
    result = await provider.swap_photo(workflow, input_zip)
    elapsed = time.perf_counter() - t0

    url = result["image_url"]
    print(f"\n[OK] swap succeeded in {elapsed:.1f}s  cost~${result['cost_usd']:.2f}")
    print(f"[OK] output URL: {url}")

    ext = (url.rsplit(".", 1)[-1] or "png").split("?")[0][:4]
    out_path = _TEST_DIR / f"output_swap.{ext}"
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        out_path.write_bytes(resp.content)
    print(f"[OK] saved -> {out_path}  ({len(resp.content)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
