# -*- coding: utf-8 -*-
"""Crash-loop guard markers for the merge→restart flow (plan Допущение 11).

Two durable, git-ignored files under ``state/dev_tasks/``:
- ``pending_restart.json`` — set on [Мердж], read+cleared once on healthy boot to
  send the "merged+restarted" confirmation.
- ``boot_watch.json`` — set on [Мердж] with a deadline + ready-made rollback
  commands; cleared on healthy boot. If the merged code crash-loops past the
  deadline, the standalone ``scripts/boot_watch_check.py`` (invoked by the
  guardian) reads this and alerts the admin. Kept dependency-light on purpose.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

PROD_REPO = "C:/jarvis"
BOOT_WATCH_S = int(__import__("os").getenv("DEVTASK_BOOT_WATCH_S", "180"))


def _watch_path(base_dir) -> Path:
    return Path(base_dir) / "boot_watch.json"


def _pending_path(base_dir) -> Path:
    return Path(base_dir) / "pending_restart.json"


def rollback_text(old_head: str, root: str = PROD_REPO) -> str:
    return (
        f"git -C {root} reset --hard {old_head}\n"
        f"# затем перезапусти бота через гардиан (или дождись респавна), при "
        f"необходимости re-register JarvisBotGuardian"
    )


def write_boot_watch(base_dir, task_id: str, old_head: str, new_head: str,
                     now: Optional[float] = None, watch_s: int = BOOT_WATCH_S) -> None:
    Path(base_dir).mkdir(parents=True, exist_ok=True)
    now = time.time() if now is None else now
    data = {
        "task_id": task_id,
        "old_head": old_head,
        "new_head": new_head,
        "deadline_epoch": int(now) + int(watch_s),
        "rollback_cmds": rollback_text(old_head),
        "alerted": False,
    }
    _watch_path(base_dir).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def clear_boot_watch(base_dir) -> None:
    p = _watch_path(base_dir)
    if p.exists():
        p.unlink()


def write_pending_restart(base_dir, task_id: str, old_head: str, new_head: str) -> None:
    Path(base_dir).mkdir(parents=True, exist_ok=True)
    data = {"task_id": task_id, "old_head": old_head, "new_head": new_head}
    _pending_path(base_dir).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def read_pending_restart(base_dir) -> Optional[dict]:
    p = _pending_path(base_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def clear_pending_restart(base_dir) -> None:
    p = _pending_path(base_dir)
    if p.exists():
        p.unlink()
