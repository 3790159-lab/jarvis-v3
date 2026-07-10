#!/usr/bin/env python3
"""One-off smoke test against pod rs2ccqe4cihx9i (manually bootstrapped).

Calls RunpodComfyEngine helper methods directly with a hardcoded pod_url —
bypasses _find_or_start_pod and the finally:stop_pod cleanup, so the pod
stays alive after success for inspection.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from app.services.block_m2_video.engines.engine_protocol import (
    VideoRequest,
    new_generation_id,
)
from app.services.block_m2_video.engines.runpod_comfy_engine import (
    RunpodComfyEngine,
)
from app.services.media_delivery import MediaDeliveryError, host_media

POD_URL = "https://rs2ccqe4cihx9i-8188.proxy.runpod.net"


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    image_path = _ROOT / "data" / "test_inputs" / "probnik2.jpg"
    request = VideoRequest(
        persona_id="smoke_test",
        persona_name="Smoke",
        input_image_path=image_path,
        prompt="a woman smiling, gentle wind in hair, cinematic lighting",
        seconds=3,
        seed=42,
    )

    gen_id = new_generation_id()
    engine = RunpodComfyEngine()
    engine._validate_request(request)

    print(f"[smoke] gen_id      = {gen_id}")
    print(f"[smoke] pod_url     = {POD_URL}")
    print(f"[smoke] image       = {image_path}")
    print(f"[smoke] prompt      = {request.prompt!r}")
    print(f"[smoke] seconds={request.seconds} seed={request.seed}")

    overall_t0 = time.monotonic()
    try:
        t0 = time.monotonic()
        uploaded = await engine._upload_image(POD_URL, image_path)
        print(f"[smoke] upload OK  ({time.monotonic()-t0:.1f}s) -> {uploaded}")

        workflow = engine._build_workflow(uploaded, request)
        frames = workflow["15"]["inputs"]["length"]
        print(f"[smoke] workflow built; node15.length = {frames} frames")

        t0 = time.monotonic()
        prompt_id = await engine._submit_prompt(POD_URL, workflow)
        print(f"[smoke] submit OK  ({time.monotonic()-t0:.1f}s) prompt_id={prompt_id}")

        t0 = time.monotonic()
        print(f"[smoke] polling /history/{prompt_id} ...")
        history = await engine._poll_until_done(POD_URL, prompt_id)
        print(f"[smoke] generation done ({time.monotonic()-t0:.1f}s)")

        mp4 = engine._extract_mp4_filename(history, prompt_id)
        print(f"[smoke] mp4 filename = {mp4}")

        t0 = time.monotonic()
        output_path = await engine._download_output(POD_URL, mp4, gen_id)
        size = output_path.stat().st_size
        print(
            f"[smoke] download OK ({time.monotonic()-t0:.1f}s) "
            f"-> {output_path} ({size:,} bytes)"
        )

        t0 = time.monotonic()
        try:
            public_url = await host_media(output_path, retention="24h")
            print(
                f"[smoke] media host OK ({time.monotonic()-t0:.1f}s) "
                f"-> {public_url}"
            )
        except MediaDeliveryError as exc:
            print(f"[smoke] media host FAILED: {exc}", file=sys.stderr)
            public_url = None

        total = time.monotonic() - overall_t0
        print()
        print("=" * 60)
        print(f"[smoke] TOTAL TIME    = {total:.1f}s ({total/60:.2f} min)")
        print(f"[smoke] OUTPUT MP4    = {output_path}")
        print(f"[smoke] PUBLIC URL    = {public_url}")
        print("=" * 60)
        return 0
    finally:
        await engine._maybe_close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
