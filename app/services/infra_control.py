# -*- coding: utf-8 -*-
"""DEV-12 infra escape hatch: allowlisted service control for ``/infra_restart``.

Exactly three targets — ``cloudflared``, ``backend``, ``bot`` — no arbitrary
shell or free-form input ever reaches a subprocess call from here; every entry
point validates against :data:`ALLOWED_TARGETS` before doing anything.

Privilege model (verified live on the host before writing this module):
``cloudflared`` is a real Windows Service — Start-Service/Stop-Service on it
needs an elevated token our bot process is not guaranteed to hold. Rather than
assume elevation, the actual Stop-Process(if StopPending)+Start-Service dance
runs inside a PRE-REGISTERED one-shot Scheduled Task
(``JarvisInfraRestartCloudflared``, RunLevel Highest — see
``scripts/register_infra_restart_tasks.ps1`` and
``scripts/infra_restart_cloudflared_action.ps1``) that this module only
*triggers* via ``schtasks /Run``; Task Scheduler supplies the elevation, not
the caller. Reading service status needs no elevation, so polling after the
trigger happens directly here, unprivileged.

``backend`` and ``bot`` are plain user-owned ``python.exe`` processes (no
Windows Service involved), each already watched by its own guardian Scheduled
Task (``JarvisBackendGuardian`` / ``JarvisBotGuardian``, both confirmed
RunLevel Highest). Killing/relaunching a process you already own needs no
extra elevation, so those two restart via small standalone action scripts run
directly — no Scheduled-Task indirection needed for them.

``bot`` is the one self-referential case: the action kills the very process
issuing the command, so it cannot synchronously poll an "after" status like
the other two. ``restart_bot`` fires a DETACHED watcher script (must never be
waited on — see ``scripts/infra_restart_bot_watcher.ps1``) that performs the
kill+relaunch+poll itself and sends its own Telegram confirmation once the
fresh bot's heartbeat is fresh (same standalone-script-reads-.env pattern as
``scripts/boot_watch_check.py``).
"""
from __future__ import annotations

import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Callable, Dict, Optional

ALLOWED_TARGETS = ("cloudflared", "backend", "bot")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CLOUDFLARED_RESTART_TASK = "JarvisInfraRestartCloudflared"
_BACKEND_ACTION_SCRIPT = _PROJECT_ROOT / "scripts" / "infra_restart_backend.ps1"
_BOT_WATCHER_SCRIPT = _PROJECT_ROOT / "scripts" / "infra_restart_bot_watcher.ps1"
_HEARTBEAT_FILE = _PROJECT_ROOT / "state" / "bot_heartbeat.txt"
_BACKEND_HEALTH_URL = "http://127.0.0.1:8010/health"

DEFAULT_POLL_TIMEOUT_SEC = 20.0
DEFAULT_POLL_INTERVAL_SEC = 1.0
DEFAULT_HEARTBEAT_MAX_AGE_SEC = 180


def is_allowed_target(target: str) -> bool:
    return target in ALLOWED_TARGETS


# ---------------------------------------------------------------------------
# Status readers — all read-only, none of the three needs elevation to read.
# ---------------------------------------------------------------------------

def cloudflared_status(run: Callable = subprocess.run) -> str:
    """``Get-Service`` is a read, not a privileged action — safe to call from
    an unelevated caller. Empty output (service not installed) → "NotFound";
    any subprocess failure → "unknown" (never raises)."""
    try:
        res = run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Service -Name cloudflared -ErrorAction SilentlyContinue).Status"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return "unknown"
    out = (getattr(res, "stdout", "") or "").strip()
    return out or "NotFound"


def backend_status(check: Optional[Callable[[], bool]] = None) -> str:
    if check is None:
        def check() -> bool:
            with urllib.request.urlopen(_BACKEND_HEALTH_URL, timeout=5) as resp:
                return resp.status == 200
    try:
        return "Running" if check() else "Down"
    except Exception:
        return "Down"


def bot_status(now: Callable[[], float] = time.time,
               heartbeat_path: Path = _HEARTBEAT_FILE,
               max_age_sec: int = DEFAULT_HEARTBEAT_MAX_AGE_SEC) -> str:
    try:
        last = int(heartbeat_path.read_text(encoding="utf-8").strip())
    except Exception:
        return "Down"
    return "Running" if (now() - last) <= max_age_sec else "Down"


def status_all(run: Callable = subprocess.run,
               backend_check: Optional[Callable[[], bool]] = None) -> Dict[str, str]:
    return {
        "cloudflared": cloudflared_status(run=run),
        "backend": backend_status(check=backend_check),
        "bot": bot_status(),
    }


def _poll_until_running(status_fn: Callable[[], str], timeout: float, interval: float,
                         sleep: Callable[[float], None]) -> str:
    current = status_fn()
    iterations = max(1, int(timeout / interval)) if interval > 0 else 1
    for _ in range(iterations):
        if current == "Running":
            break
        sleep(interval)
        current = status_fn()
    return current


# ---------------------------------------------------------------------------
# Restart actions
# ---------------------------------------------------------------------------

def restart_cloudflared(run: Callable = subprocess.run,
                         poll_timeout: float = DEFAULT_POLL_TIMEOUT_SEC,
                         poll_interval: float = DEFAULT_POLL_INTERVAL_SEC,
                         sleep: Callable[[float], None] = time.sleep) -> Dict[str, object]:
    before = cloudflared_status(run=run)
    try:
        run(["schtasks", "/Run", "/TN", _CLOUDFLARED_RESTART_TASK],
            capture_output=True, text=True, timeout=10)
    except Exception as exc:
        return {"ok": False, "before": before, "after": before,
                "detail": "trigger failed: %s" % exc}
    after = _poll_until_running(lambda: cloudflared_status(run=run),
                                 poll_timeout, poll_interval, sleep)
    return {"ok": after == "Running", "before": before, "after": after, "detail": ""}


def restart_backend(run: Callable = subprocess.run,
                     poll_timeout: float = DEFAULT_POLL_TIMEOUT_SEC,
                     poll_interval: float = DEFAULT_POLL_INTERVAL_SEC,
                     sleep: Callable[[float], None] = time.sleep,
                     backend_check: Optional[Callable[[], bool]] = None) -> Dict[str, object]:
    before = backend_status(check=backend_check)
    try:
        run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(_BACKEND_ACTION_SCRIPT)],
            capture_output=True, text=True, timeout=30)
    except Exception as exc:
        return {"ok": False, "before": before, "after": before,
                "detail": "restart failed: %s" % exc}
    after = _poll_until_running(lambda: backend_status(check=backend_check),
                                 poll_timeout, poll_interval, sleep)
    return {"ok": after == "Running", "before": before, "after": after, "detail": ""}


def restart_bot(popen: Callable = subprocess.Popen) -> Dict[str, object]:
    """Fire-and-forget on purpose: the watcher kills this very process, so
    ``popen`` must never be waited on here (see
    test_restart_bot_never_blocks_waiting_for_the_watcher)."""
    before = bot_status()
    try:
        popen([
            "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden", "-File", str(_BOT_WATCHER_SCRIPT),
        ])
    except Exception as exc:
        return {"ok": False, "before": before, "after": before,
                "detail": "trigger failed: %s" % exc}
    return {"ok": True, "before": before, "after": None,
            "detail": "restart triggered — bot will confirm separately once back up"}


def restart(target: str, *,
            run: Callable = subprocess.run,
            popen: Callable = subprocess.Popen,
            backend_check: Optional[Callable[[], bool]] = None,
            poll_timeout: float = DEFAULT_POLL_TIMEOUT_SEC,
            poll_interval: float = DEFAULT_POLL_INTERVAL_SEC,
            sleep: Callable[[float], None] = time.sleep) -> Dict[str, object]:
    """Single chokepoint: validates the allowlist THEN dispatches — no caller
    may reach a target-specific restart function with an unvalidated string."""
    if not is_allowed_target(target):
        raise ValueError("target not in allowlist: %r" % (target,))
    if target == "cloudflared":
        return restart_cloudflared(run=run, poll_timeout=poll_timeout,
                                    poll_interval=poll_interval, sleep=sleep)
    if target == "backend":
        return restart_backend(run=run, poll_timeout=poll_timeout,
                                poll_interval=poll_interval, sleep=sleep,
                                backend_check=backend_check)
    return restart_bot(popen=popen)
