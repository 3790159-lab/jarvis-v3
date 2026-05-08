from __future__ import annotations

"""Per-user conversation history stored as JSON Lines (Phase 10).

File: state/conversations/<chat_id>.jsonl
Each line: {"timestamp": "...", "role": "user|assistant", "content": "...", "intent": "..."}
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(os.getcwd())
CONV_DIR = ROOT / "state" / "conversations"
CONV_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()
MAX_HISTORY = 100
CONTEXT_WINDOW = 10


def _user_path(user_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(user_id))
    return CONV_DIR / f"{safe}.jsonl"


def add_to_history(user_id: str, role: str, content: str, intent: str = "") -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "role": role,
        "content": content[:1000],
        "intent": intent,
    }
    path = _user_path(user_id)
    with _lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    prune_history(user_id)


def get_history(user_id: str, n: int = CONTEXT_WINDOW) -> List[Dict[str, Any]]:
    path = _user_path(user_id)
    if not path.exists():
        return []
    entries: List[Dict[str, Any]] = []
    with _lock:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        pass
    return entries[-n:]


def clear_history(user_id: str) -> int:
    path = _user_path(user_id)
    with _lock:
        if path.exists():
            count = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
            path.unlink()
            return count
    return 0


def prune_history(user_id: str, max_entries: int = MAX_HISTORY) -> None:
    path = _user_path(user_id)
    with _lock:
        if not path.exists():
            return
        lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        if len(lines) > max_entries:
            path.write_text("\n".join(lines[-max_entries:]) + "\n", encoding="utf-8")


def count_history(user_id: str) -> int:
    path = _user_path(user_id)
    if not path.exists():
        return 0
    with _lock:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def format_history_for_display(user_id: str, n: int = 10) -> str:
    entries = get_history(user_id, n=n)
    if not entries:
        return "История пуста."
    lines = []
    for e in entries:
        ts = e.get("timestamp", "")[:16].replace("T", " ")
        role_icon = "👤" if e["role"] == "user" else "🤖"
        content = (e.get("content") or "")[:100]
        intent = f" [{e['intent']}]" if e.get("intent") else ""
        lines.append(f"{role_icon} {ts}{intent}\n   {content}")
    return "\n".join(lines)


def format_history_for_llm(user_id: str, n: int = CONTEXT_WINDOW) -> List[Dict[str, str]]:
    """Return history as OpenAI/Anthropic messages list."""
    entries = get_history(user_id, n=n)
    return [
        {"role": e["role"], "content": e.get("content", "")}
        for e in entries
        if e.get("role") in ("user", "assistant")
    ]
