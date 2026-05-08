"""Phase 35: System Watchdog вЂ” monitors Jarvis health and auto-restarts services.

Runs as a background service (JarvisWatchdog) and checks every 60 seconds:
- Backend /health endpoint
- Disk space > 1GB
- Memory usage < 90%

On failure: tries to restart via Windows Service Manager + sends Telegram alert.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

BACKEND_URL = os.getenv("BACKEND_BASE_URL", "http://127.0.0.1:8010")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
CHECK_INTERVAL_SEC = int(os.getenv("WATCHDOG_INTERVAL", "60"))
MIN_DISK_GB = float(os.getenv("WATCHDOG_MIN_DISK_GB", "1.0"))
MAX_MEMORY_PCT = float(os.getenv("WATCHDOG_MAX_MEMORY_PCT", "90.0"))
GRACE_PERIOD_SEC = int(os.getenv("WATCHDOG_GRACE_PERIOD_SEC", "300"))

# Module-load time — used by restart_bot_if_dead to skip false-positives at startup
_module_started_at = time.time()


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------

def check_backend() -> Dict[str, Any]:
    """Check if backend is responding. Returns {"ok": bool, "detail": str}."""
    try:
        import urllib.request
        req = urllib.request.urlopen(f"{BACKEND_URL}/health", timeout=10)
        data = req.read().decode()
        return {"ok": True, "detail": data[:200]}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def check_disk_space() -> Dict[str, Any]:
    """Check available disk space on the project drive."""
    try:
        import shutil
        project_path = Path(__file__).parent.parent.parent
        total, used, free = shutil.disk_usage(project_path)
        free_gb = free / (1024 ** 3)
        return {
            "ok": free_gb >= MIN_DISK_GB,
            "free_gb": round(free_gb, 2),
            "detail": f"{free_gb:.1f}GB free",
        }
    except Exception as exc:
        return {"ok": True, "detail": str(exc)}  # Don't restart over disk check failure


def check_memory() -> Dict[str, Any]:
    """Check system memory usage."""
    try:
        import psutil
        mem = psutil.virtual_memory()
        pct = mem.percent
        return {
            "ok": pct < MAX_MEMORY_PCT,
            "percent": pct,
            "detail": f"{pct:.1f}% used",
        }
    except ImportError:
        return {"ok": True, "detail": "psutil not available"}
    except Exception as exc:
        return {"ok": True, "detail": str(exc)}


# ---------------------------------------------------------------------------
# Remediation
# ---------------------------------------------------------------------------

def restart_service(service_name: str) -> bool:
    """Restart a Windows service. Returns True on success."""
    if sys.platform != "win32":
        logger.warning("restart_service: not on Windows, skipping")
        return False
    try:
        subprocess.run(
            ["powershell", "-Command", f"Restart-Service {service_name} -Force"],
            capture_output=True,
            timeout=30,
            check=True,
        )
        logger.info("Restarted service: %s", service_name)
        return True
    except Exception as exc:
        logger.error("Failed to restart %s: %s", service_name, exc)
        return False


def send_telegram_alert(text: str) -> bool:
    """Send alert to admin via Telegram."""
    if not BOT_TOKEN or not ADMIN_CHAT_ID:
        logger.warning("Telegram alert skipped: no BOT_TOKEN or ADMIN_CHAT_ID")
        return False
    try:
        import urllib.request
        import json
        payload = json.dumps({
            "chat_id": ADMIN_CHAT_ID,
            "text": f"рџљЁ Jarvis Watchdog:\n{text}",
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as exc:
        logger.error("Telegram alert failed: %s", exc)
        return False


def cleanup_old_logs(state_dir: Optional[Path] = None, max_log_mb: int = 50) -> int:
    """Truncate log files larger than max_log_mb. Returns number of files cleaned."""
    if state_dir is None:
        state_dir = Path(__file__).parent.parent.parent / "state"
    cleaned = 0
    for log_file in state_dir.glob("*.log"):
        size_mb = log_file.stat().st_size / (1024 * 1024)
        if size_mb > max_log_mb:
            try:
                lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                keep = lines[-1000:]  # keep last 1000 lines
                log_file.write_text("\n".join(keep) + "\n", encoding="utf-8")
                logger.info("Truncated %s (was %.1f MB)", log_file.name, size_mb)
                cleaned += 1
            except Exception as exc:
                logger.warning("Failed to truncate %s: %s", log_file.name, exc)
    return cleaned


# ---------------------------------------------------------------------------
# Main watchdog loop
# ---------------------------------------------------------------------------

def check_bot_alive() -> bool:
    """Check if bot heartbeat file is fresh (< 90s)."""
    heartbeat_file = Path(__file__).parent.parent.parent / "state" / "bot_heartbeat.txt"
    if not heartbeat_file.exists():
        return False
    try:
        last_beat = int(heartbeat_file.read_text(encoding="utf-8").strip())
        return (time.time() - last_beat) < 90
    except Exception:
        return False


def restart_bot_if_dead() -> bool:
    """If bot heartbeat is stale, restart bot via start_jarvis.ps1. Returns True if restart attempted."""
    # Grace period: don't panic in the first N seconds after watchdog start
    elapsed = time.time() - _module_started_at
    if elapsed < GRACE_PERIOD_SEC:
        logger.info("Within grace period (%.0fs / %ds), skipping bot restart check", elapsed, GRACE_PERIOD_SEC)
        return False
    if check_bot_alive():
        return False
    logger.warning("Bot heartbeat stale вЂ” attempting restart via start_jarvis.ps1")
    try:
        project_root = Path(__file__).parent.parent.parent
        script = project_root / "start_jarvis.ps1"
        if script.exists():
            subprocess.Popen(
                ["powershell", "-File", str(script)],
                cwd=str(project_root),
            )
            send_telegram_alert("Bot heartbeat stale вЂ” РїРµСЂРµР·Р°РїСѓС‰РµРЅ С‡РµСЂРµР· start_jarvis.ps1")
            return True
        else:
            logger.warning("start_jarvis.ps1 not found at %s", script)
            send_telegram_alert("Bot heartbeat stale вЂ” start_jarvis.ps1 РЅРµ РЅР°Р№РґРµРЅ, СЂСѓС‡РЅРѕР№ РїРµСЂРµР·Р°РїСѓСЃРє!")
            return False
    except Exception as exc:
        logger.error("restart_bot_if_dead failed: %s", exc)
        return False


def run_watchdog_cycle() -> Dict[str, Any]:
    """Run one health check cycle. Returns results dict."""
    results: Dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "backend": None,
        "disk": None,
        "memory": None,
        "actions_taken": [],
    }

    # Backend check
    backend = check_backend()
    results["backend"] = backend
    if not backend["ok"]:
        logger.warning("Backend unhealthy: %s", backend["detail"])
        restarted = restart_service("JarvisBackend")
        if restarted:
            time.sleep(8)
            # Give bot time to reconnect
            restart_service("JarvisBot")
            results["actions_taken"].append("restarted_backend")
            send_telegram_alert(
                f"Backend РЅРµ РѕС‚РІРµС‡Р°Р». РџРµСЂРµР·Р°РїСѓС‰РµРЅ Р°РІС‚РѕРјР°С‚РёС‡РµСЃРєРё.\n"
                f"РџСЂРёС‡РёРЅР°: {backend['detail']}"
            )
        else:
            send_telegram_alert(
                f"Backend РЅРµ РѕС‚РІРµС‡Р°РµС‚ Рё РЅРµ СѓРґР°Р»РѕСЃСЊ РїРµСЂРµР·Р°РїСѓСЃС‚РёС‚СЊ!\n"
                f"Р”РµС‚Р°Р»Рё: {backend['detail']}"
            )

    # Disk check
    disk = check_disk_space()
    results["disk"] = disk
    if not disk["ok"]:
        logger.warning("Low disk space: %s", disk["detail"])
        cleaned = cleanup_old_logs()
        results["actions_taken"].append(f"cleaned_{cleaned}_logs")
        send_telegram_alert(
            f"РњР°Р»Рѕ РјРµСЃС‚Р° РЅР° РґРёСЃРєРµ: {disk['detail']}\n"
            f"РџРѕС‡РёС‰РµРЅРѕ Р»РѕРі-С„Р°Р№Р»РѕРІ: {cleaned}"
        )

    # Memory check
    mem = check_memory()
    results["memory"] = mem
    if not mem["ok"]:
        logger.warning("High memory usage: %s", mem["detail"])
        send_telegram_alert(
            f"Р’С‹СЃРѕРєРѕРµ РїРѕС‚СЂРµР±Р»РµРЅРёРµ РїР°РјСЏС‚Рё: {mem['detail']}\n"
            "Р РµРєРѕРјРµРЅРґСѓРµС‚СЃСЏ РїРµСЂРµР·Р°РїСѓСЃС‚РёС‚СЊ СЃРµСЂРІРёСЃС‹."
        )

    return results


def main():
    """Entry point for JarvisWatchdog service."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [WATCHDOG] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger.info("Jarvis Watchdog started. Check interval: %ds", CHECK_INTERVAL_SEC)
    logger.info("Backend: %s | Min disk: %.1f GB | Max memory: %.0f%%",
                BACKEND_URL, MIN_DISK_GB, MAX_MEMORY_PCT)

    # Wait for backend to start before first check
    time.sleep(30)

    consecutive_failures = 0
    while True:
        try:
            results = run_watchdog_cycle()
            if results["actions_taken"]:
                logger.info("Watchdog actions: %s", results["actions_taken"])
            else:
                logger.info("All checks passed. Disk: %s | Memory: %s",
                            (results["disk"] or {}).get("detail", "?"),
                            (results["memory"] or {}).get("detail", "?"))
            consecutive_failures = 0
        except Exception as exc:
            consecutive_failures += 1
            logger.error("Watchdog cycle failed (#%d): %s", consecutive_failures, exc)
            if consecutive_failures >= 5:
                send_telegram_alert(f"Watchdog itself is failing!\n{exc}")
                consecutive_failures = 0

        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
