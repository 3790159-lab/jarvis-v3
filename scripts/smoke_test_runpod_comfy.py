"""Block M.2 Phase B.3 - Smoke test for RunpodComfyEngine.

Environment variables (all optional):
    SMOKE_TEST_IMAGE          - path to the input image (default: probnik2.jpg under data/test_inputs)
    SMOKE_TEST_PROMPT         - generation prompt
    SMOKE_TEST_SECONDS        - clip length in seconds (int)
    SMOKE_TEST_SEED           - RNG seed (int)
    SMOKE_TEST_PERSONA_ID     - persona_id passed to the engine
    SMOKE_TEST_PERSONA_NAME   - persona_name passed to the engine

Without any env vars the script runs the original Phase B.3 probnik2 smoke test.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from app.services.block_m2_video.engines.engine_protocol import VideoRequest
from app.services.block_m2_video.engines.runpod_comfy_engine import (
    RunpodComfyEngine,
    RunpodComfyError,
)


LOCAL_VALIDATION_IMAGE = Path(
    os.environ.get("SMOKE_TEST_IMAGE", r"C:\jarvis\data\test_inputs\probnik2.jpg")
)
PROMPT = os.environ.get(
    "SMOKE_TEST_PROMPT",
    "static cinematic portrait, gentle natural motion, soft window light, "
    "subtle wind in hair, photorealistic, ultra-detailed",
)
SECONDS = int(os.environ.get("SMOKE_TEST_SECONDS", "5"))
SEED = int(os.environ.get("SMOKE_TEST_SEED", "42"))
PERSONA_ID = os.environ.get("SMOKE_TEST_PERSONA_ID", "smoketest_probnik2")
PERSONA_NAME = os.environ.get("SMOKE_TEST_PERSONA_NAME", "Probnik 2 (smoke test)")


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )
    log = logging.getLogger("smoke_test")

    if not LOCAL_VALIDATION_IMAGE.exists():
        log.error("Local validation image missing: %s", LOCAL_VALIDATION_IMAGE)
        return 1

    request = VideoRequest(
        persona_id=PERSONA_ID,
        persona_name=PERSONA_NAME,
        input_image_path=LOCAL_VALIDATION_IMAGE,
        prompt=PROMPT,
        seconds=SECONDS,
        seed=SEED,
    )
    log.info("=== Phase B.3 smoke test starting ===")
    log.info("persona_id=%s seed=%d seconds=%d", request.persona_id, SEED, SECONDS)

    engine = RunpodComfyEngine()

    available = await engine.is_available()
    log.info("engine.is_available() -> %s", available)
    if not available:
        log.error("Engine reports unavailable. Aborting.")
        return 2

    try:
        result = await engine.generate(request)
    except RunpodComfyError as exc:
        log.error("RunpodComfyError: %s", exc)
        return 3
    except Exception:
        log.exception("Unexpected exception during generate()")
        return 4

    log.info("=== SMOKE TEST SUCCESS ===")
    log.info("generation_id : %s", result.generation_id)
    log.info("output_path   : %s", result.output_path)
    log.info("cost_usd      : $%.4f", result.cost_usd)
    log.info("duration_sec  : %.1f", result.duration_sec)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
