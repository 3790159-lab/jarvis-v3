"""Smart Schedule Manager — Block H5.6.

Analyzes user activity patterns to auto-schedule tasks at optimal times.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent.parent
_DECISIONS_PATH = _ROOT / "state" / "decisions.jsonl"
_PATTERNS_PATH = _ROOT / "state" / "user_patterns.json"


def _read_decisions(days: int = 30) -> List[Dict[str, Any]]:
    """Read decisions from the last N days."""
    if not _DECISIONS_PATH.exists():
        return []
    cutoff = datetime.now() - timedelta(days=days)
    records = []
    try:
        for line in _DECISIONS_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                ts_str = rec.get("timestamp") or rec.get("ts") or ""
                try:
                    if ts_str and datetime.fromisoformat(ts_str[:19]) < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass
                records.append(rec)
            except json.JSONDecodeError:
                continue
    except Exception as exc:
        logger.warning("[smart_schedule] read error: %s", exc)
    return records


def analyze_user_patterns(days: int = 30) -> Dict[str, Any]:
    """Study when user is active based on decision timestamps."""
    decisions = _read_decisions(days)

    if not decisions:
        return {
            "active_hours": list(range(9, 23)),
            "peak_hours": [10, 14, 19],
            "quiet_hours_start": 1,
            "quiet_hours_end": 8,
            "avg_sessions_per_day": 0,
            "data_points": 0,
        }

    hour_counts: Counter = Counter()
    day_sessions: Dict[str, int] = {}

    for d in decisions:
        ts_str = d.get("timestamp") or d.get("ts") or ""
        try:
            ts = datetime.fromisoformat(ts_str[:19])
            hour_counts[ts.hour] += 1
            day_key = ts.date().isoformat()
            day_sessions[day_key] = day_sessions.get(day_key, 0) + 1
        except (ValueError, TypeError):
            continue

    # Find top active hours
    sorted_hours = sorted(hour_counts, key=lambda h: -hour_counts[h])
    active_hours = sorted(sorted_hours[:12])
    peak_hours = sorted(sorted_hours[:3])

    # Detect quiet hours (lowest activity)
    quiet_start, quiet_end = detect_quiet_hours(hour_counts)

    avg_per_day = (
        sum(day_sessions.values()) / len(day_sessions)
        if day_sessions else 0
    )

    patterns = {
        "active_hours": active_hours,
        "peak_hours": peak_hours,
        "quiet_hours_start": quiet_start,
        "quiet_hours_end": quiet_end,
        "avg_sessions_per_day": round(avg_per_day, 1),
        "data_points": len(decisions),
        "analyzed_at": datetime.now().isoformat(),
    }

    # Persist patterns
    _PATTERNS_PATH.write_text(
        json.dumps(patterns, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return patterns


def detect_quiet_hours(
    hour_counts: Optional[Dict[int, int]] = None,
) -> Tuple[int, int]:
    """Detect when user is usually asleep/inactive."""
    if hour_counts is None:
        # Load from patterns if available
        if _PATTERNS_PATH.exists():
            try:
                p = json.loads(_PATTERNS_PATH.read_text(encoding="utf-8"))
                return p.get("quiet_hours_start", 1), p.get("quiet_hours_end", 8)
            except Exception:
                pass
        return 1, 8  # default: 01:00–08:00

    # Find longest consecutive stretch of low activity
    if not hour_counts:
        return 1, 8

    threshold = max(hour_counts.values()) * 0.1 if hour_counts else 0
    quiet_hours = [h for h in range(24) if hour_counts.get(h, 0) <= threshold]

    if not quiet_hours:
        return 1, 8

    # Find longest consecutive run in quiet_hours (wrapping around midnight)
    best_start, best_len = quiet_hours[0], 1
    curr_start, curr_len = quiet_hours[0], 1

    for i in range(1, len(quiet_hours)):
        prev = quiet_hours[i - 1]
        curr = quiet_hours[i]
        if curr == (prev + 1) % 24:
            curr_len += 1
        else:
            if curr_len > best_len:
                best_start, best_len = curr_start, curr_len
            curr_start, curr_len = curr, 1

    if curr_len > best_len:
        best_start, best_len = curr_start, curr_len

    quiet_end = (best_start + best_len) % 24
    return best_start, quiet_end


def get_optimal_brief_time(patterns: Optional[Dict[str, Any]] = None) -> str:
    """Return time to send morning brief (30 min before user's usual wakeup)."""
    if patterns is None:
        if _PATTERNS_PATH.exists():
            try:
                patterns = json.loads(_PATTERNS_PATH.read_text(encoding="utf-8"))
            except Exception:
                patterns = {}

    quiet_end = (patterns or {}).get("quiet_hours_end", 8)
    # 30 minutes before usual wake-up
    brief_hour = max(0, quiet_end - 1)
    return f"{brief_hour:02d}:30"


def auto_schedule_tasks(scheduler: Any = None) -> List[str]:
    """Create smart scheduled tasks based on user patterns."""
    patterns = analyze_user_patterns()
    task_ids: List[str] = []

    if scheduler is None:
        try:
            import sys
            root = str(_ROOT)
            if root not in sys.path:
                sys.path.insert(0, root)
            from app.services.scheduler import JarvisScheduler
            scheduler = JarvisScheduler()
        except Exception as exc:
            logger.warning("[smart_schedule] no scheduler: %s", exc)
            return []

    brief_time = get_optimal_brief_time(patterns)
    brief_hour, brief_min = map(int, brief_time.split(":"))

    try:
        tid = scheduler.add_task(
            action="morning_brief",
            params={"auto_scheduled": True},
            cron=f"{brief_min} {brief_hour} * * *",
            chat_id=None,
        )
        task_ids.append(tid)
    except Exception as exc:
        logger.warning("[smart_schedule] brief task error: %s", exc)

    # Schedule activity reminders at peak hours
    for peak_hour in patterns.get("peak_hours", [])[:2]:
        try:
            tid = scheduler.add_task(
                action="activity_reminder",
                params={"hour": peak_hour},
                cron=f"0 {peak_hour} * * *",
                chat_id=None,
            )
            task_ids.append(tid)
        except Exception:
            pass

    return task_ids


def load_patterns() -> Dict[str, Any]:
    """Load saved user patterns."""
    if _PATTERNS_PATH.exists():
        try:
            return json.loads(_PATTERNS_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}
