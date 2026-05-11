# -*- coding: utf-8 -*-
"""Block M.2 Phase A engines: protocol, router, history, and concrete engines.

This subpackage sits alongside the M.2.1 orchestrator (``video_generator.py``
in the parent directory) and provides the new Phase A foundation:

* :mod:`engine_protocol` — typing/dataclass surface for engines
* :mod:`replicate_engine` — fast cloud engine via Replicate
* :mod:`runpod_comfy_engine` — stub for the HQ on-prem engine (Phase B)
* :mod:`router` — :class:`EngineRouter` selecting the right engine
* :mod:`history` — generation persistence and redo support
"""
from .engine_protocol import (
    GenerationMode,
    VideoGenerator,
    VideoRequest,
    VideoResult,
    new_generation_id,
)

__all__ = [
    "GenerationMode",
    "VideoGenerator",
    "VideoRequest",
    "VideoResult",
    "new_generation_id",
]
