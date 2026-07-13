# -*- coding: utf-8 -*-
"""Evening error-digest (daily 21:00) — pure log-parsing/dedup/format logic.

Greps fresh ERROR/CRITICAL lines from the bot log (``logs/jarvis_bot.log``)
and the backend log (``logs/jarvis.log``), collapses exact repeats into one
line + a counter, and returns the top-N by count. Empty result -> the caller
must send nothing ("тишина = хорошо" per spec) — this module never decides
whether to send, only what the digest contains.

Pure module: no file I/O, no network. ``scripts/error_digest.py`` (standalone,
mirrors ``scripts/morning_digest.py``) reads the log files and calls
``build_digest_report``/``format_digest_message``.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

__all__ = [
    "DIGEST_WINDOW_HOURS",
    "TOP_N",
    "LEVELS",
    "NOISE_ENV_VAR",
    "parse_log_line",
    "noise_patterns_from_env",
    "filter_known_noise",
    "select_error_records",
    "dedupe_error_records",
    "top_records",
    "build_digest_report",
    "format_digest_message",
]

DIGEST_WINDOW_HOURS = 24
TOP_N = 5
LEVELS: Tuple[str, ...] = ("ERROR", "CRITICAL")

NOISE_ENV_VAR = "ERROR_DIGEST_NOISE_PATTERNS"

# Matches app/core/logging_setup.py's _FMT:
# "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s" (datefmt
# "%Y-%m-%d %H:%M:%S"). Traceback continuation lines have no leading
# timestamp and simply don't match -- dropped rather than guessed at, same
# call app/services/devtask/suggest.py's recent_error_lines() makes.
_LOG_LINE_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \| (\w+)\s*\| (.*?)\s*\| (.*)$"
)


def parse_log_line(line: str) -> Optional[Dict[str, object]]:
    """Parse one log line into ``{timestamp, level, logger, message}``, or
    ``None`` if it doesn't match the app logging format (e.g. a traceback
    continuation line, or a blank/garbage line)."""
    m = _LOG_LINE_RE.match(line.rstrip("\n"))
    if not m:
        return None
    ts_str, level, logger_name, message = m.groups()
    try:
        timestamp = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return {
        "timestamp": timestamp,
        "level": level.strip(),
        "logger": logger_name.strip(),
        "message": message.strip(),
    }


def noise_patterns_from_env(env: Optional[Dict[str, str]] = None) -> List[str]:
    """Known-noise substrings to exclude, comma-separated in the
    ``ERROR_DIGEST_NOISE_PATTERNS`` env var (config filter list per spec).
    Matching is case-insensitive substring containment against the message.
    """
    source = os.environ if env is None else env
    raw = source.get(NOISE_ENV_VAR, "")
    return [p.strip() for p in raw.split(",") if p.strip()]


def filter_known_noise(records: List[Dict[str, object]], patterns: List[str]) -> List[Dict[str, object]]:
    """Drop records whose message contains any of ``patterns`` (case-insensitive)."""
    if not patterns:
        return list(records)
    lowered = [p.lower() for p in patterns]
    return [
        r for r in records
        if not any(p in str(r["message"]).lower() for p in lowered)
    ]


def select_error_records(lines: Optional[List[str]], now: datetime,
                          window_hours: int = DIGEST_WINDOW_HOURS,
                          levels: Tuple[str, ...] = LEVELS) -> List[Dict[str, object]]:
    """Parse ``lines``, keep records at ``levels`` inside ``[now-window, now]``.

    Future-dated lines (clock skew) are dropped too, matching
    ``suggest.recent_error_lines``'s guard.
    """
    since = now - timedelta(hours=window_hours)
    out: List[Dict[str, object]] = []
    for line in lines or []:
        record = parse_log_line(line)
        if record is None:
            continue
        if record["level"] not in levels:
            continue
        if record["timestamp"] < since or record["timestamp"] > now:
            continue
        out.append(record)
    return out


def dedupe_error_records(records: List[Dict[str, object]]) -> List[Dict[str, object]]:
    """Collapse exact repeats (same level+logger+message) into one entry with
    a ``count``, in first-seen order."""
    order: List[Tuple[str, str, str]] = []
    grouped: Dict[Tuple[str, str, str], Dict[str, object]] = {}
    for r in records:
        key = (str(r["level"]), str(r["logger"]), str(r["message"]))
        if key not in grouped:
            grouped[key] = {"level": key[0], "logger": key[1], "message": key[2], "count": 0}
            order.append(key)
        grouped[key]["count"] += 1
    return [grouped[k] for k in order]


def top_records(records: List[Dict[str, object]], n: int = TOP_N) -> List[Dict[str, object]]:
    """Highest ``count`` first (stable: ties keep first-seen order)."""
    return sorted(records, key=lambda r: -int(r["count"]))[:n]


def build_digest_report(*, bot_lines: Optional[List[str]] = None,
                         backend_lines: Optional[List[str]] = None,
                         now: datetime,
                         window_hours: int = DIGEST_WINDOW_HOURS,
                         noise_patterns: Optional[List[str]] = None,
                         top_n: int = TOP_N) -> List[Dict[str, object]]:
    """Pure orchestration: parse both logs, drop known noise, dedupe, return
    the top ``top_n`` records (``[]`` when nothing survived — the caller must
    treat that as "send nothing")."""
    records = (
        select_error_records(bot_lines, now, window_hours)
        + select_error_records(backend_lines, now, window_hours)
    )
    patterns = noise_patterns_from_env() if noise_patterns is None else noise_patterns
    records = filter_known_noise(records, patterns)
    deduped = dedupe_error_records(records)
    return top_records(deduped, n=top_n)


def format_digest_message(records: List[Dict[str, object]]) -> str:
    """Telegram-friendly top-N digest text.

    Caller contract: only call this (and only send its result) when
    ``records`` is non-empty — an empty digest must produce no message at
    all, not a "all clean" line (spec: "тишина = хорошо").
    """
    lines = ["🌙 Вечерний error-дайджест (24ч):", ""]
    for i, r in enumerate(records, start=1):
        count = int(r.get("count", 1))
        suffix = f" (x{count})" if count > 1 else ""
        lines.append(f"{i}. [{r['level']}] {r['logger']}: {r['message']}{suffix}")
    return "\n".join(lines)
