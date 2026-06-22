# -*- coding: utf-8 -*-
"""Router that picks the right engine based on mode + availability."""
from __future__ import annotations

import logging

from .engine_protocol import GenerationMode, VideoGenerator
from .replicate_engine import ReplicateEngine
from .runpod_comfy_engine import RunpodComfyEngine

logger = logging.getLogger(__name__)


class EngineRouter:
    """Selects an engine for a request based on the requested mode."""

    def __init__(
        self,
        *,
        replicate: VideoGenerator | None = None,
        runpod: VideoGenerator | None = None,
        wavespeed: VideoGenerator | None = None,
    ) -> None:
        # Engines are lazy-instantiated by default so importing the router
        # does not require REPLICATE_API_TOKEN at module load time.
        self._replicate = replicate
        self._runpod = runpod
        self._wavespeed = wavespeed

    def _get_replicate(self) -> VideoGenerator:
        if self._replicate is None:
            self._replicate = ReplicateEngine()
        return self._replicate

    def _get_runpod(self) -> VideoGenerator:
        if self._runpod is None:
            self._runpod = RunpodComfyEngine()
        return self._runpod

    def _get_wavespeed(self) -> VideoGenerator:
        if self._wavespeed is None:
            from .wavespeed_spicy_engine import WaveSpeedSpicyEngine
            self._wavespeed = WaveSpeedSpicyEngine()
        return self._wavespeed

    async def select(self, mode: GenerationMode) -> VideoGenerator:
        """Pick an engine for ``mode``.

        - ``"fast"`` → always Replicate.
        - ``"hq"``   → always RunPod (which raises NotImplementedError until Phase B).
        - ``"auto"`` → RunPod if available right now, else Replicate.
        """
        if mode == "spicy":
            logger.info("EngineRouter: mode=spicy -> WaveSpeedSpicyEngine")
            return self._get_wavespeed()
        if mode == "fast":
            logger.info("EngineRouter: mode=fast -> ReplicateEngine")
            return self._get_replicate()
        if mode == "hq":
            logger.info("EngineRouter: mode=hq -> RunpodComfyEngine")
            return self._get_runpod()

        runpod = self._get_runpod()
        if await runpod.is_available():
            logger.info("EngineRouter: mode=auto, RunPod available -> RunpodComfyEngine")
            return runpod
        logger.info("EngineRouter: mode=auto, RunPod unavailable -> ReplicateEngine")
        return self._get_replicate()
