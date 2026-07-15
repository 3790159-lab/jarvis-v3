# -*- coding: utf-8 -*-
"""DEV-12 infra escape hatch: allowlisted service control for ``/infra_restart``.

Exactly three targets — ``cloudflared``, ``backend``, ``bot`` — no arbitrary
shell or free-form input ever reaches a subprocess call from here; every entry
point validates against :data:`ALLOWED_TARGETS` before doing anything.

Privilege model, DEV-12a cascade (DEV-12's original design assumed the
``JarvisInfraRestartCloudflared`` Scheduled Task would always be registered
by a human running ``scripts/register_infra_restart_tasks.ps1`` once, post-
merge — but that is precisely unavailable in the "SSH is down, machine is
unreachable" scenario this escape hatch exists for). ``restart_cloudflared``
now probes elevation of the CALLING process at runtime (:func:`is_elevated`,
a read-only ``WindowsPrincipal.IsInRole(Administrator)`` check — guardian
tasks run with RunLevel Highest, so a bot spawned by one is often already
elevated) and picks one of two paths, never silently failing:

* **Path A** (elevated): Stop-Process(if StopPending)+Start-Service run
  directly, no Scheduled Task involved at all.
* **Path B** (not elevated): trigger the pre-registered Scheduled Task via
  ``schtasks /Run`` as before; if it isn't registered yet, attempt to
  register it on the fly (``scripts/register_infra_restart_tasks.ps1``) and
  retry once. If registration itself fails (no admin rights to register a
  task either), return an honest failure — never a silent one — that the
  Telegram layer surfaces as "physical access needed".

Reading service status needs no elevation either way, so polling after
either path happens directly here, unprivileged.

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
_REGISTER_TASKS_SCRIPT = _PROJECT_ROOT / "scripts" / "register_infra_restart_tasks.ps1"
_BACKEND_ACTION_SCRIPT = _PROJECT_ROOT / "scripts" / "infra_restart_backend.ps1"
_BOT_WATCHER_SCRIPT = _PROJECT_ROOT / "scripts" / "infra_restart_bot_watcher.ps1"
_HEARTBEAT_FILE = _PROJECT_ROOT / "state" / "bot_heartbeat.txt"
_BACKEND_HEALTH_URL = "http://127.0.0.1:8010/health"

DEFAULT_POLL_TIMEOUT_SEC = 20.0
DEFAULT_POLL_INTERVAL_SEC = 1.0
DEFAULT_HEARTBEAT_MAX_AGE_SEC = 180


def is_allowed_target(target: str) -> bool:
    return target in ALLOWED_TARGETS


def is_elevated(run: Callable = subprocess.run) -> bool:
    """Read-only ``WindowsPrincipal.IsInRole(Administrator)`` probe for the
    CALLING process. Never assumes elevation: any subprocess failure or
    unexpected output returns False, which drives the cloudflared cascade to
    the safer Scheduled-Task path B rather than an unprivileged Path A that
    would just fail on Start-Service."""
    try:
        res = run(
            ["powershell", "-NoProfile", "-Command",
             "([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent())"
             ".IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:
        return False
    return (getattr(res, "stdout", "") or "").strip().lower() == "true"


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

def _restart_cloudflared_path_a(run: Callable, before: str) -> Dict[str, object]:
    """Elevated path: no Scheduled Task needed — kill a wedged StopPending
    process directly, then Start-Service."""
    if before == "StopPending":
        try:
            run(["powershell", "-NoProfile", "-Command",
                 "Get-Process -Name cloudflared -ErrorAction SilentlyContinue | "
                 "Stop-Process -Force -ErrorAction SilentlyContinue"],
                capture_output=True, text=True, timeout=10)
        except Exception:
            pass
    try:
        run(["powershell", "-NoProfile", "-Command",
             "Start-Service -Name cloudflared -ErrorAction Stop"],
            capture_output=True, text=True, timeout=15)
    except Exception as exc:
        return {"ok": False, "path": "A",
                "detail": "путь A (elevated): Start-Service не удался: %s" % exc}
    return {"ok": True, "path": "A", "detail": "путь A: elevated, прямой Start-Service"}


def _cloudflared_task_registered(run: Callable) -> bool:
    try:
        res = run(["schtasks", "/Query", "/TN", _CLOUDFLARED_RESTART_TASK],
                   capture_output=True, text=True, timeout=10)
    except Exception:
        return False
    return getattr(res, "returncode", 1) == 0


def _register_cloudflared_task(run: Callable) -> bool:
    try:
        res = run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                   "-File", str(_REGISTER_TASKS_SCRIPT)],
                  capture_output=True, text=True, timeout=30)
    except Exception:
        return False
    return getattr(res, "returncode", 1) == 0


def _restart_cloudflared_path_b(run: Callable) -> Dict[str, object]:
    """Unelevated path: trigger the pre-registered Scheduled Task; if it's
    missing, attempt to register it on the fly first (best-effort — this
    itself needs admin rights, so it can fail too)."""
    just_registered = False
    if not _cloudflared_task_registered(run=run):
        just_registered = _register_cloudflared_task(run=run)
        if not just_registered:
            return {"ok": False, "path": "B",
                     "detail": "нужен физический доступ: не elevated, задача '%s' "
                               "не зарегистрирована и авторегистрация на лету не удалась"
                               % _CLOUDFLARED_RESTART_TASK}
    try:
        run(["schtasks", "/Run", "/TN", _CLOUDFLARED_RESTART_TASK],
            capture_output=True, text=True, timeout=10)
    except Exception as exc:
        return {"ok": False, "path": "B", "detail": "путь B: trigger failed: %s" % exc}
    how = "задача зарегистрирована на лету" if just_registered else "задача уже была зарегистрирована"
    return {"ok": True, "path": "B", "detail": "путь B: %s" % how}


def restart_cloudflared(run: Callable = subprocess.run,
                         poll_timeout: float = DEFAULT_POLL_TIMEOUT_SEC,
                         poll_interval: float = DEFAULT_POLL_INTERVAL_SEC,
                         sleep: Callable[[float], None] = time.sleep,
                         elevated_check: Optional[Callable[[], bool]] = None) -> Dict[str, object]:
    before = cloudflared_status(run=run)
    elevated = elevated_check() if elevated_check is not None else is_elevated(run=run)
    outcome = (_restart_cloudflared_path_a(run=run, before=before) if elevated
               else _restart_cloudflared_path_b(run=run))
    if not outcome["ok"]:
        return {"ok": False, "before": before, "after": before,
                "path": outcome["path"], "detail": outcome["detail"]}
    after = _poll_until_running(lambda: cloudflared_status(run=run),
                                 poll_timeout, poll_interval, sleep)
    return {"ok": after == "Running", "before": before, "after": after,
            "path": outcome["path"], "detail": outcome["detail"]}


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
