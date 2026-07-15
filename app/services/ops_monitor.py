# -*- coding: utf-8 -*-
"""DEV-17 monitoring & alerts: infra checks Uptime Kuma polls over HTTP.

15.07 the backend died at 03:14 and nobody found out — the only thing that
would have noticed was the bot's own heartbeat loop, which shares fate with
the process it would need to alert about. These checks back the new
``/api/jarvis/ops/*`` endpoints (``app.routers.jarvis_ops_health_router``) so
an independent monitor (Uptime Kuma, a separate always-on process/container)
can detect backend/bot/cloudflared/disk trouble over plain HTTP, without
depending on the bot process being alive to notice its own death.

Every check is a pure, fully injectable function (same DI style as
``app.services.infra_control``: ``run``/``now``/``usage`` are always fakes in
tests) — none of them ever raises, all degrade to ``{"ok": False}`` on error.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

from app.services import infra_control as _infra

DEFAULT_MIN_DISK_GB = 4.0
DEFAULT_HEARTBEAT_MAX_AGE_SEC = 180
DEFAULT_RESTART_WINDOW_SEC = 3600
DEFAULT_RESTART_THRESHOLD = 3

_RESTART_LINE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\|.*restarting", re.IGNORECASE
)


def check_disk(path: Path = Path("C:/"), min_gb: float = DEFAULT_MIN_DISK_GB,
                usage: Optional[Callable] = None) -> Dict[str, object]:
    usage_fn = usage or shutil.disk_usage
    try:
        _total, _used, free = usage_fn(str(path))
        free_gb = free / (1024 ** 3)
    except Exception as exc:
        return {"ok": False, "free_gb": None, "detail": "disk check failed: %s" % exc}
    return {"ok": free_gb >= min_gb, "free_gb": round(free_gb, 2),
            "detail": "%.1fGB free (min %.1fGB)" % (free_gb, min_gb)}


def check_bot_heartbeat(heartbeat_path: Path,
                         max_age_sec: int = DEFAULT_HEARTBEAT_MAX_AGE_SEC,
                         now: Callable[[], float] = time.time) -> Dict[str, object]:
    try:
        last = int(Path(heartbeat_path).read_text(encoding="utf-8").strip())
    except Exception as exc:
        return {"ok": False, "age_sec": None, "detail": "heartbeat unreadable: %s" % exc}
    age = now() - last
    return {"ok": age <= max_age_sec, "age_sec": round(age, 1),
            "detail": "%.0fs old (max %ds)" % (age, max_age_sec)}


def check_cloudflared(run: Callable = subprocess.run) -> Dict[str, object]:
    status = _infra.cloudflared_status(run=run)
    return {"ok": status == "Running", "status": status, "detail": status}


def count_recent_restarts(log_path: Path, window_sec: int = DEFAULT_RESTART_WINDOW_SEC,
                           now: Optional[datetime] = None) -> int:
    if now is None:
        now = datetime.now()
    try:
        lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return 0
    count = 0
    for line in lines:
        m = _RESTART_LINE_RE.match(line)
        if not m:
            continue
        try:
            ts = datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if 0 <= (now - ts).total_seconds() <= window_sec:
            count += 1
    return count


def check_restart_storm(log_path: Path, threshold: int = DEFAULT_RESTART_THRESHOLD,
                         window_sec: int = DEFAULT_RESTART_WINDOW_SEC,
                         now: Optional[datetime] = None) -> Dict[str, object]:
    count = count_recent_restarts(log_path, window_sec=window_sec, now=now)
    return {"ok": count <= threshold, "count": count, "threshold": threshold,
            "detail": "%d restarts in last %ds (threshold %d)" % (count, window_sec, threshold)}
