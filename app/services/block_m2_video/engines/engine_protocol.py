# -*- coding: utf-8 -*-
"""Protocol and types shared by all Phase A video engines."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

GenerationMode = Literal["fast", "hq", "auto"]


@dataclass
class VideoRequest:
    """Input for video generation."""

    persona_id: str
    persona_name: str
    input_image_path: Path
    prompt: str
    seconds: int = 5
    seed: int | None = None
    mode: GenerationMode = "auto"
    # For redo: reuse an existing generation_id when overwriting is desired.
    generation_id: str | None = None


@dataclass
class VideoResult:
    """Output of a successful video generation."""

    generation_id: str
    persona_id: str
    output_path: Path
    engine: str
    model: str
    seed: int
    cost_usd: float
    duration_sec: float
    timestamp: datetime
    prompt: str
    seconds: int
    extra: dict = field(default_factory=dict)


@runtime_checkable
class VideoGenerator(Protocol):
    """Interface every engine must implement."""

    @property
    def engine_name(self) -> str: ...

    async def is_available(self) -> bool: ...

    async def generate(self, request: VideoRequest) -> VideoResult: ...


def new_generation_id() -> str:
    """Return a fresh ``gen_<8 hex>`` identifier."""
    return f"gen_{uuid.uuid4().hex[:8]}"
