# -*- coding: utf-8 -*-
"""BrowserJob — the single structured unit every browser run is built from.

One-shot commands (``/browse_check``) build an ephemeral BrowserJob; a saved,
named BrowserJob is a *playbook* (see ``playbook.py``). Defining this up front
means the move to scheduled competitor-monitoring only fills the ``schedule``
slot — the engine never gets re-architected. See plan:
docs/superpowers/plans/2026-07-05-browser-use-arc.md (§5ter).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict, fields
from typing import List, Optional

# defaults are conservative: read-only, no domains, cheap caps.
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_STEPS = 25
DEFAULT_MAX_WALL_S = 180


@dataclass
class BrowserJob:
    urls: List[str]
    task: str
    name: Optional[str] = None
    mode: str = "read"                      # "read" (BU-1) | "act" (BU-2, confirm-gated)
    allowed_domains: List[str] = field(default_factory=list)
    extract: Optional[str] = None
    model: str = DEFAULT_MODEL
    max_steps: int = DEFAULT_MAX_STEPS
    max_wall_s: int = DEFAULT_MAX_WALL_S
    schedule: Optional[str] = None          # slot for BU-2+ scheduled playbooks

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BrowserJob":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in known})
