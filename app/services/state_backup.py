# -*- coding: utf-8 -*-
"""Daily backup of critical ``state/`` files to Cloudflare R2 (DEV-16).

``state/`` lives on a single disk (users.json, money-ledgers, brain-state,
dev_tasks, verdicts, persona-centroids) — losing that disk loses everything
except media (already on R2, see ``app.services.r2_storage``). This module
uploads a fixed, explicit allowlist of critical files (never ``.env``, never
a credential/token file such as ``ig_accounts.json``/``api_keys.json``/
``google_oauth_token.json``) to R2 under ``backups/state/<date>/<rel_path>``,
writes a per-run manifest (sha256 per file, for integrity verification on
restore), and rotates (deletes) backups older than ``KEEP_DAYS``.

Uses a SEPARATE, private R2 bucket (``R2_BACKUP_BUCKET``) from the public
media bucket (``R2_BUCKET``) — backup keys are predictable
(``backups/state/2026-07-15/users.json``), and the media bucket may have its
Public Development URL enabled; that must never expose backup contents. The
backup bucket needs no ``R2_PUBLIC_BASE_URL`` — restore always goes through
the authenticated S3 API (``app.services.r2_storage.download_file``), never
a public URL. Reuses the same account-level credentials
(``R2_ACCOUNT_ID``/``R2_ACCESS_KEY_ID``/``R2_SECRET_ACCESS_KEY``/``R2_ENDPOINT``)
as the media bucket.

Entry points: ``scripts/state_backup.py`` (daily scheduled task, mirrors
``scripts/morning_digest.py``) and the ``/backup_now``/``/backup_status``
Telegram admin commands (``tools/jarvis_smart_telegram_control.py``).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from app.services import r2_storage
from app.services.r2_storage import R2Config, R2ConfigError

logger = logging.getLogger(__name__)

__all__ = [
    "CRITICAL_PATTERNS",
    "KEEP_DAYS",
    "BACKUP_PREFIX",
    "BackupResult",
    "load_backup_config",
    "discover_backup_files",
    "sha256_file",
    "build_manifest",
    "run_backup",
    "list_backup_dates",
    "rotate_old_backups",
    "restore_file",
    "verify_restored_file",
    "format_backup_result",
    "format_backup_status",
]

# Explicit allowlist (glob patterns relative to state/). Anything NOT listed
# here is never backed up — in particular .env and any credential/token file
# under state/ (ig_accounts.json, api_keys.json, google_oauth_token.json).
CRITICAL_PATTERNS: tuple[str, ...] = (
    "users.json",
    "cost_tracking.json",
    "daily_metrics.json",
    "regress_baseline.json",
    "personas/personas.json",
    "personas/expenses.jsonl",
    "personas/*/arcface_centroid.npy",
    "expenses/persona_video.jsonl",
    "jarvis_brain/*.json",
    "dev_tasks/*.json",
    "dev_tasks/log.jsonl",
    "dev_tasks/*/report.md",
    # Свидетельства подключения клиента (арка T7). Обе улики живут вне git и
    # существуют в ОДНОМ экземпляре: consent — единственная запись о том, что
    # доступ к личной переписке аккаунта разрешён, кем и когда; bundle — маркер
    # того, что бандл секретов снимался и какие сессии в нём.
    # Глобы УЗКИЕ по хвосту имени, а не `connect/*`: в том же каталоге лежат
    # session-файлы Telethon и прочие секреты, а `connect/*.md` утащил бы ещё и
    # человеческий журнал `<slug>.md`, который свидетельством не является.
    "connect/*.consent.md",
    "connect/*.bundle.txt",
)

KEEP_DAYS = 14
BACKUP_PREFIX = "backups/state"

_REQUIRED_ENV = (
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_ENDPOINT",
    "R2_BACKUP_BUCKET",
)


@dataclass
class BackupResult:
    date: str
    uploaded: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    manifest_key: str | None = None
    total_bytes: int = 0

    @property
    def ok(self) -> bool:
        return not self.failed


def load_backup_config() -> R2Config:
    """Config for the private state-backup bucket. NOT the public media
    bucket/config in ``r2_storage._load_config`` — see module docstring."""
    values = {name: os.getenv(name, "").strip() for name in _REQUIRED_ENV}
    missing = [name for name, val in values.items() if not val]
    if missing:
        raise R2ConfigError(
            "missing R2 backup config: " + ", ".join(missing) + ". Create a "
            "PRIVATE R2 bucket (do NOT enable Public Development URL) for "
            "state backups and set R2_BACKUP_BUCKET to its name."
        )
    return R2Config(
        account_id=values["R2_ACCOUNT_ID"],
        access_key_id=values["R2_ACCESS_KEY_ID"],
        secret_access_key=values["R2_SECRET_ACCESS_KEY"],
        bucket=values["R2_BACKUP_BUCKET"],
        endpoint=values["R2_ENDPOINT"],
        public_base_url="",
    )


def discover_backup_files(
    state_root: Path, patterns: tuple[str, ...] = CRITICAL_PATTERNS
) -> list[Path]:
    """Resolve the critical-file allowlist against an actual state/ dir.
    Missing files/patterns are silently skipped (a fresh install may not
    have every category yet) — never an error."""
    state_root = Path(state_root)
    found: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for p in sorted(state_root.glob(pattern)):
            if p.is_file() and p not in seen:
                seen.add(p)
                found.append(p)
    return found


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(files: list[Path], state_root: Path, generated_at: str) -> dict:
    state_root = Path(state_root)
    entries = []
    total = 0
    for p in files:
        size = p.stat().st_size
        total += size
        entries.append({
            "rel_path": p.relative_to(state_root).as_posix(),
            "size": size,
            "sha256": sha256_file(p),
        })
    return {
        "generated_at": generated_at,
        "count": len(entries),
        "total_bytes": total,
        "files": entries,
    }


def run_backup(
    state_root: Path,
    *,
    now: datetime | None = None,
    upload_file: Callable[..., str] = r2_storage.upload_file,
    client: Any | None = None,
    config: R2Config | None = None,
    patterns: tuple[str, ...] = CRITICAL_PATTERNS,
) -> BackupResult:
    """Upload every file the allowlist finds under ``state_root`` to
    ``backups/state/<date>/<rel_path>``, then a manifest.json alongside them.
    A single file's upload failure never aborts the run — every other file
    still gets backed up, and the failure is reported honestly."""
    config = config or load_backup_config()
    now = now or datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    result = BackupResult(date=date_str)
    files = discover_backup_files(state_root, patterns)

    manifest_files: list[Path] = []
    for path in files:
        rel = path.relative_to(Path(state_root)).as_posix()
        key = f"{BACKUP_PREFIX}/{date_str}/{rel}"
        try:
            upload_file(path, key=key, client=client, config=config)
            result.uploaded.append(rel)
            result.total_bytes += path.stat().st_size
            manifest_files.append(path)
        except Exception as exc:  # noqa: BLE001 — honest per-file failure, keep going
            logger.warning("state backup upload failed for %s: %s", rel, exc)
            result.failed.append({"rel_path": rel, "error": str(exc)})

    manifest = build_manifest(manifest_files, state_root, now.isoformat())
    manifest_key = f"{BACKUP_PREFIX}/{date_str}/manifest.json"
    fd, tmp_name = tempfile.mkstemp(suffix=".json")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
        upload_file(tmp_path, key=manifest_key, client=client, config=config)
        result.manifest_key = manifest_key
    except Exception as exc:  # noqa: BLE001
        logger.warning("state backup manifest upload failed: %s", exc)
        result.failed.append({"rel_path": "manifest.json", "error": str(exc)})
    finally:
        tmp_path.unlink(missing_ok=True)

    return result


def list_backup_dates(
    *,
    list_objects: Callable[..., list[dict]] = r2_storage.list_objects,
    client: Any | None = None,
    config: R2Config | None = None,
) -> list[str]:
    """Sorted unique ``YYYY-MM-DD`` dates present under ``BACKUP_PREFIX/``."""
    config = config or load_backup_config()
    objs = list_objects(BACKUP_PREFIX + "/", client=client, config=config)
    dates: set[str] = set()
    for o in objs:
        rest = o["key"][len(BACKUP_PREFIX) + 1:]
        date_part = rest.split("/", 1)[0]
        if date_part:
            dates.add(date_part)
    return sorted(dates)


def rotate_old_backups(
    *,
    keep_days: int = KEEP_DAYS,
    today: datetime | None = None,
    list_objects: Callable[..., list[dict]] = r2_storage.list_objects,
    delete_object: Callable[..., None] = r2_storage.delete_object,
    client: Any | None = None,
    config: R2Config | None = None,
) -> list[str]:
    """Delete every object under a backup date older than ``keep_days``.
    Date strings compare lexically == chronologically (YYYY-MM-DD)."""
    config = config or load_backup_config()
    today = today or datetime.now(timezone.utc)
    cutoff = (today - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    objs = list_objects(BACKUP_PREFIX + "/", client=client, config=config)
    deleted: list[str] = []
    for o in objs:
        rest = o["key"][len(BACKUP_PREFIX) + 1:]
        date_part = rest.split("/", 1)[0]
        if date_part and date_part < cutoff:
            delete_object(o["key"], client=client, config=config)
            deleted.append(o["key"])
    return deleted


def restore_file(
    rel_path: str,
    dest: str | Path,
    *,
    date: str,
    download_file: Callable[..., Path] = r2_storage.download_file,
    client: Any | None = None,
    config: R2Config | None = None,
) -> Path:
    """Download one backed-up file to ``dest`` (acceptance-test helper: live
    restore-and-verify of a single file)."""
    config = config or load_backup_config()
    key = f"{BACKUP_PREFIX}/{date}/{rel_path}"
    return download_file(key, dest, client=client, config=config)


def verify_restored_file(path: str | Path, expected_sha256: str) -> bool:
    return sha256_file(Path(path)) == expected_sha256


def format_backup_result(result: BackupResult) -> str:
    lines = [f"\U0001f4be Бэкап state/ за {result.date}:"]
    kb = result.total_bytes / 1024.0
    lines.append(f"Загружено: {len(result.uploaded)} файлов ({kb:.1f} KB)")
    if result.failed:
        lines.append(f"⚠️ Ошибки: {len(result.failed)}")
        for f in result.failed[:5]:
            lines.append(f"  - {f['rel_path']}: {str(f['error'])[:80]}")
    else:
        lines.append("Ошибок нет.")
    if result.manifest_key:
        lines.append(f"Манифест: {result.manifest_key}")
    return "\n".join(lines)


def format_backup_status(dates: list[str], keep_days: int = KEEP_DAYS) -> str:
    if not dates:
        return "\U0001f4be Бэкапов в R2 не найдено (ещё не запускался / ошибка конфигурации)."
    lines = [f"\U0001f4be Бэкапы state/ в R2: {len(dates)} шт."]
    lines.append(f"Последний: {dates[-1]}")
    lines.append(f"Самый старый: {dates[0]}")
    lines.append(f"Ротация: хранится {keep_days} дней.")
    return "\n".join(lines)
