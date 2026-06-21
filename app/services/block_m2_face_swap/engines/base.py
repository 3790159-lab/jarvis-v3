# -*- coding: utf-8 -*-
"""SwapEngine — the one interface the batch pipeline depends on.

The orchestrator/handler call an engine ONLY through ``swap_batch``. Today's
impl is lucataco (Replicate); Path B (our ComfyUI graph) will be a second impl.
Switching engines = change ``SWAP_ENGINE`` env + add a class. Nothing else moves.
"""
from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable, Protocol, runtime_checkable

ProgressCb = Callable[[str, dict], None]
CancelCheck = Callable[[], bool]


@runtime_checkable
class SwapEngine(Protocol):
    name: str
    cost_per_swap_usd: float

    async def swap_batch(
        self,
        source: Path,
        targets: list[Path],
        *,
        progress_cb: ProgressCb | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> list[Path | None]:
        """Swap ``source``'s face into each target. Returns a list aligned to
        ``targets``: a local Path on success, ``None`` on per-target failure."""
        ...
