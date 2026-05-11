# -*- coding: utf-8 -*-
"""STUB for the RunPod ComfyUI engine. Real implementation lands in Phase B."""
from __future__ import annotations

from .engine_protocol import VideoRequest, VideoResult


class RunpodComfyEngine:
    engine_name = "runpod_comfy"

    async def is_available(self) -> bool:
        # Phase B will probe actual GPU availability; for now report unavailable
        # so the router always falls through to Replicate in ``auto`` mode.
        return False

    async def generate(self, request: VideoRequest) -> VideoResult:
        raise NotImplementedError(
            "RunpodComfyEngine is a stub. Implementation comes in Phase B "
            "after manual Wan 2.2 MP4 capture confirms output JSON structure."
        )
