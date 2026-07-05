# -*- coding: utf-8 -*-
"""Browser session artifacts (screenshots / DOM / steps) + rotation.

Artifacts live LOCALLY under ``state/browser_sessions/<id>/`` (gitignored) and
are NEVER shipped to Telegram (see ``pii.build_report``). Rotation keeps the
store bounded, like worktree cleanup after a merge. See plan §5bis.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import List

_DEFAULT_BASE = Path("state/browser_sessions")
KEEP = int(os.getenv("BROWSER_SESSIONS_KEEP", "20"))
MAX_AGE_DAYS = int(os.getenv("BROWSER_SESSIONS_MAX_AGE_DAYS", "7"))


def open_session(session_id: str, *, base_dir=_DEFAULT_BASE) -> Path:
    p = Path(base_dir) / session_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_artifact(session_path: Path, rel: str, data) -> None:
    dst = Path(session_path) / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, (bytes, bytearray)):
        dst.write_bytes(data)
    else:
        dst.write_text(str(data), encoding="utf-8")


def list_sessions(*, base_dir=_DEFAULT_BASE) -> List[str]:
    d = Path(base_dir)
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir())


def rotate(*, base_dir=_DEFAULT_BASE, keep: int = KEEP,
           max_age_days: int = MAX_AGE_DAYS, now: float = None) -> List[str]:
    """Remove sessions beyond ``keep`` (oldest by sortable id) and older than
    ``max_age_days``. Best-effort: never raises (cleanup must not block a run)."""
    removed: List[str] = []
    try:
        d = Path(base_dir)
        if not d.exists():
            return removed
        names = list_sessions(base_dir=base_dir)
        now = time.time() if now is None else now
        doomed = set(names[:-keep] if keep > 0 else names)     # over the count cap
        for name in names[-keep:] if keep > 0 else []:
            try:
                age_days = (now - (d / name).stat().st_mtime) / 86400.0
                if age_days > max_age_days:
                    doomed.add(name)
            except OSError:
                pass
        for name in sorted(doomed):
            try:
                shutil.rmtree(d / name)
                removed.append(name)
            except OSError:
                pass
    except Exception:
        pass
    return removed
