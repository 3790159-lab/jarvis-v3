# -*- coding: utf-8 -*-
"""Headless single-shot video face-swap runner for the occlusion A/B test.

Constructs the real :class:`VideoFaceSwapEngine` and calls ``swap_video`` once.
Pod selection / occlusion / keep-alive are all driven by env vars the engine
already reads (``FACE_SWAP_POD_ID``, ``VIDEO_SWAP_OCCLUSION_MASK``,
``FACE_SWAP_KEEP_POD_RUNNING``) — set them in the shell before launching.

Usage:
    python -m scripts.run_video_swap_occlusion_ab --source FACE.jpg --video CLIP.mp4
"""
from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from app.services.block_m2_face_swap.video_face_swap_engine import (
    VideoFaceSwapEngine,
)


def _progress(event: str, data: dict) -> None:
    print(f"[progress] {event}: {data}", flush=True)


async def _main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="face photo (jpg/png)")
    ap.add_argument("--video", required=True, help="target clip (mp4)")
    ap.add_argument("--max-seconds", type=float, default=60.0)
    args = ap.parse_args()

    print(
        "[config] "
        f"FACE_SWAP_POD_ID={os.environ.get('FACE_SWAP_POD_ID')!r} "
        f"VIDEO_SWAP_OCCLUSION_MASK={os.environ.get('VIDEO_SWAP_OCCLUSION_MASK')!r} "
        f"FACE_SWAP_KEEP_POD_RUNNING={os.environ.get('FACE_SWAP_KEEP_POD_RUNNING')!r}",
        flush=True,
    )

    engine = VideoFaceSwapEngine()
    out = await engine.swap_video(
        Path(args.source),
        Path(args.video),
        progress_cb=_progress,
        max_seconds=args.max_seconds,
    )
    print(f"[done] output: {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
