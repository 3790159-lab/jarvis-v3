from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict


DEFAULT_HOOK_LOG = Path("jarvis_stage3_artifacts/hooks/hook_events.jsonl")


def run_hooks(stage: str, context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    Lightweight hook runner for Jarvis lifecycle stages.

    Safe stages:
    - before_plan
    - after_plan
    - before_execute
    - after_execute
    - before_file_write
    - after_file_write
    - before_backend_restart
    - after_backend_restart
    - before_night_mode_commit
    - after_night_mode_commit
    """
    context = context or {}

    event = {
        "ts": datetime.utcnow().isoformat(),
        "stage": stage,
        "context": context,
        "status": "ok",
    }

    DEFAULT_HOOK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with DEFAULT_HOOK_LOG.open("a", encoding="utf-8") as f:
        import json
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

    return event