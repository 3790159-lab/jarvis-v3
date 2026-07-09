"""Phase 36: Self-Healing — disk cleanup, memory monitoring, backups.

Extends the old skeleton with full implementation:
- check_backend(): HTTP health check
- SelfHealing.check_and_heal(): disk/memory/archive/backup
- cleanup_old_logs(), archive_old_decisions(), create_backup()
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

STATE_DIR = Path("state")
BACKUPS_DIR = Path("state_backups")


# ---------------------------------------------------------------------------
# Legacy snapshot (kept for backwards compatibility)
# ---------------------------------------------------------------------------

@dataclass
class BackendHealthSnapshot:
    ok: bool
    status_code: Optional[int]
    url: str
    error: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    checked_at: str = ""

    def __post_init__(self):
        if not self.checked_at:
            self.checked_at = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def check_backend(base_url: str = "http://127.0.0.1:8010", timeout: int = 5) -> BackendHealthSnapshot:
    """HTTP health check against /health endpoint."""
    url = base_url.rstrip("/") + "/health"
    try:
        import urllib.request
        resp = urllib.request.urlopen(url, timeout=timeout)
        body = resp.read().decode()
        try:
            payload = json.loads(body)
        except Exception:
            payload = {"text": body[:500]}
        return BackendHealthSnapshot(ok=True, status_code=200, url=url, payload=payload)
    except Exception as exc:
        return BackendHealthSnapshot(ok=False, status_code=None, url=url, error=str(exc))


def recommend_recovery(snapshot: BackendHealthSnapshot) -> Dict[str, Any]:
    if snapshot.ok:
        return {"action": "none", "reason": "backend is healthy", "risk": "low"}
    return {
        "action": "restart_backend_then_recheck",
        "reason": snapshot.error or f"bad status: {snapshot.status_code}",
        "risk": "medium",
        "notes": [
            "Restart using hardened restart script if available.",
            "After restart, re-run /health and compile checks.",
        ],
    }


# ---------------------------------------------------------------------------
# Disk helpers
# ---------------------------------------------------------------------------

def disk_free_gb(path: Optional[Path] = None) -> float:
    """Return free disk space in GB."""
    if path is None:
        path = Path(__file__).parent.parent.parent
    try:
        total, used, free = shutil.disk_usage(str(path))
        return free / (1024 ** 3)
    except Exception:
        return 999.0


def process_memory_mb() -> float:
    """Return current process memory in MB."""
    try:
        import psutil
        proc = psutil.Process(os.getpid())
        return proc.memory_info().rss / (1024 * 1024)
    except ImportError:
        return 0.0
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Log cleanup
# ---------------------------------------------------------------------------

def cleanup_old_logs(state_dir: Optional[Path] = None, max_size_mb: float = 50.0) -> int:
    """Truncate log files exceeding max_size_mb. Returns count cleaned."""
    if state_dir is None:
        state_dir = STATE_DIR
    cleaned = 0
    if not state_dir.exists():
        return 0
    for f in state_dir.glob("*.log"):
        try:
            size_mb = f.stat().st_size / (1024 * 1024)
            if size_mb > max_size_mb:
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
                f.write_text("\n".join(lines[-1000:]) + "\n", encoding="utf-8")
                logger.info("Truncated %s (was %.1fMB)", f.name, size_mb)
                cleaned += 1
        except Exception as exc:
            logger.warning("cleanup_old_logs: %s: %s", f.name, exc)
    return cleaned


# ---------------------------------------------------------------------------
# Decision archive
# ---------------------------------------------------------------------------

def archive_old_decisions(
    decisions_path: Optional[Path] = None,
    older_than_days: int = 30,
) -> int:
    """Move decisions older than N days to archive file. Returns archived count."""
    if decisions_path is None:
        decisions_path = STATE_DIR / "decisions.jsonl"
    if not decisions_path.exists():
        return 0

    archive_path = decisions_path.parent / "decisions_archive.jsonl"
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)

    keep_lines: List[str] = []
    archive_lines: List[str] = []

    for line in decisions_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            ts_str = rec.get("timestamp", "")
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")) if ts_str else None
            if ts and ts < cutoff:
                archive_lines.append(line)
            else:
                keep_lines.append(line)
        except Exception:
            keep_lines.append(line)

    if archive_lines:
        try:
            with archive_path.open("a", encoding="utf-8") as f:
                f.write("\n".join(archive_lines) + "\n")
            decisions_path.write_text("\n".join(keep_lines) + "\n", encoding="utf-8")
            logger.info("Archived %d old decisions", len(archive_lines))
        except Exception as exc:
            logger.warning("archive_old_decisions failed: %s", exc)

    return len(archive_lines)


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

def backup_today(backups_dir: Optional[Path] = None, state_dir: Optional[Path] = None) -> bool:
    """Return True if today's backup already exists."""
    if backups_dir is None:
        backups_dir = BACKUPS_DIR
    today = datetime.now().strftime("%Y-%m-%d")
    return (backups_dir / today).exists()


def create_backup(
    state_dir: Optional[Path] = None,
    backups_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Copy state/ to state_backups/YYYY-MM-DD/. Returns backup path."""
    if state_dir is None:
        state_dir = STATE_DIR
    if backups_dir is None:
        backups_dir = BACKUPS_DIR
    if not state_dir.exists():
        return None

    today = datetime.now().strftime("%Y-%m-%d")
    dest = backups_dir / today
    try:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(str(state_dir), str(dest))
        logger.info("Backup created: %s", dest)
        return dest
    except Exception as exc:
        logger.error("create_backup failed: %s", exc)
        return None


def cleanup_old_backups(backups_dir: Optional[Path] = None, keep_days: int = 7) -> int:
    """Delete backups older than keep_days. Returns count deleted."""
    if backups_dir is None:
        backups_dir = BACKUPS_DIR
    if not backups_dir.exists():
        return 0

    cutoff = datetime.now() - timedelta(days=keep_days)
    deleted = 0
    for d in backups_dir.iterdir():
        if not d.is_dir():
            continue
        try:
            backup_date = datetime.strptime(d.name, "%Y-%m-%d")
            if backup_date < cutoff:
                shutil.rmtree(d)
                logger.info("Deleted old backup: %s", d.name)
                deleted += 1
        except (ValueError, Exception):
            pass
    return deleted


# ---------------------------------------------------------------------------
# Notify helper
# ---------------------------------------------------------------------------

def notify_admin(message: str) -> None:
    """Send notification to admin via Telegram."""
    from app.core.notify_isolation import telegram_send_blocked

    if telegram_send_blocked():
        logger.info("[SelfHealing notify suppressed] %s", message)
        return
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_ALLOWED_CHAT_ID", "").strip()
    if not bot_token or not chat_id:
        logger.info("[SelfHealing notify] %s", message)
        return
    try:
        import urllib.request
        payload = json.dumps({"chat_id": chat_id, "text": f"🔧 Self-Healing:\n{message}"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as exc:
        logger.warning("notify_admin failed: %s", exc)


# ---------------------------------------------------------------------------
# SelfHealing orchestrator
# ---------------------------------------------------------------------------

class SelfHealing:
    """Orchestrates all self-healing checks and remediation."""

    def __init__(
        self,
        state_dir: Optional[Path] = None,
        backups_dir: Optional[Path] = None,
        min_disk_gb: float = 1.0,
        max_memory_mb: float = 1000.0,
    ):
        self.state_dir = state_dir or STATE_DIR
        self.backups_dir = backups_dir or BACKUPS_DIR
        self.min_disk_gb = min_disk_gb
        self.max_memory_mb = max_memory_mb

    def check_and_heal(self) -> Dict[str, Any]:
        """Run all health checks and perform remediation. Returns report dict."""
        report: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actions": [],
            "disk_free_gb": None,
            "memory_mb": None,
        }

        # 1. Disk space
        free_gb = disk_free_gb()
        report["disk_free_gb"] = round(free_gb, 2)
        if free_gb < self.min_disk_gb:
            cleaned = cleanup_old_logs(self.state_dir)
            notify_admin(f"Disk low ({free_gb:.1f}GB free) — cleaned {cleaned} log files")
            report["actions"].append(f"cleaned_{cleaned}_logs")

        # 2. Memory leak detection
        mem_mb = process_memory_mb()
        report["memory_mb"] = round(mem_mb, 1)
        if mem_mb > self.max_memory_mb:
            notify_admin(f"High memory: {mem_mb:.0f}MB — restart recommended")
            report["actions"].append("high_memory_alert")

        # 3. Archive decisions older than 30 days
        archived = archive_old_decisions(self.state_dir / "decisions.jsonl")
        if archived > 0:
            report["actions"].append(f"archived_{archived}_decisions")

        # 4. Daily backup
        if not backup_today(self.backups_dir, self.state_dir):
            backup_path = create_backup(self.state_dir, self.backups_dir)
            if backup_path:
                report["actions"].append(f"backup_created:{backup_path.name}")
                cleanup_old_backups(self.backups_dir)

        return report
