# -*- coding: utf-8 -*-
"""Playbook persistence — a saved, named BrowserJob (repeatable scenario).

Thin JSON store at ``state/browser_playbooks/<name>.json``. BU-1 only needs
save/load/list to reserve the format; the ``/browse_playbook_*`` commands and
schedule execution land in BU-2+. See plan §5ter.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List

from .job import BrowserJob

_DEFAULT_BASE = Path("state/browser_playbooks")
_SAFE = re.compile(r"[^a-zA-Z0-9_-]")


def _path(name: str, base_dir) -> Path:
    safe = _SAFE.sub("_", name or "").strip("_") or "unnamed"
    return Path(base_dir) / f"{safe}.json"


def save(job: BrowserJob, *, base_dir=_DEFAULT_BASE) -> None:
    p = _path(job.name or "", base_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(job.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def load(name: str, *, base_dir=_DEFAULT_BASE) -> BrowserJob:
    p = _path(name, base_dir)
    return BrowserJob.from_dict(json.loads(p.read_text(encoding="utf-8")))


def list_names(*, base_dir=_DEFAULT_BASE) -> List[str]:
    d = Path(base_dir)
    if not d.exists():
        return []
    return sorted(f.stem for f in d.glob("*.json"))
