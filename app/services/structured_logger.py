from __future__ import annotations

"""Structured JSON Lines logger for Jarvis events.

Each log entry is a JSON line written to:
  state/logs/jarvis_daily_YYYYMMDD.jsonl

Fields per entry:
  timestamp, user_id, intent, query, result_summary, latency_ms, error

Usage:
  logger = get_logger()
  logger.log(user_id="123", intent="table", query="топ AI", result_summary="ok", latency_ms=1200)
"""

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(os.getcwd())
LOGS_DIR = ROOT / "state" / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()


def _today_path() -> Path:
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    return LOGS_DIR / f"jarvis_daily_{date_str}.jsonl"


def log_event(
    intent: str,
    query: str = "",
    user_id: str = "",
    result_summary: str = "",
    latency_ms: int = 0,
    error: str = "",
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "intent": intent,
        "query": query[:200],
        "result_summary": result_summary[:300],
        "latency_ms": latency_ms,
        "error": error[:300],
    }
    line = json.dumps(entry, ensure_ascii=False)
    with _lock:
        path = _today_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def read_today_log() -> list[Dict[str, Any]]:
    path = _today_path()
    if not path.exists():
        return []
    entries = []
    with _lock:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        pass
    return entries


def build_daily_report(entries: Optional[list[Dict[str, Any]]] = None) -> Dict[str, Any]:
    if entries is None:
        entries = read_today_log()

    total = len(entries)
    intents: Dict[str, int] = {}
    errors = 0
    total_latency = 0

    for e in entries:
        intent = e.get("intent", "unknown")
        intents[intent] = intents.get(intent, 0) + 1
        if e.get("error"):
            errors += 1
        total_latency += e.get("latency_ms", 0)

    avg_latency = (total_latency // total) if total else 0

    return {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "total_events": total,
        "errors": errors,
        "avg_latency_ms": avg_latency,
        "intents": intents,
    }
