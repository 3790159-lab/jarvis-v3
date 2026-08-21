# -*- coding: utf-8 -*-
"""Daily backup of critical ``state/`` files to Cloudflare R2 (DEV-16).

``state/`` lives on a single disk (users.json, money-ledgers, brain-state,
dev_tasks, verdicts, persona-centroids) — losing that disk loses everything
except media (already on R2, see ``app.services.r2_storage``). This module
uploads a fixed, explicit allowlist of critical files (never ``.env``, never
a credential/token file such as ``ig_accounts.json``/``api_keys.json``/
``google_oauth_token.json``) to R2 under ``backups/state/<date>/<rel_path>``,
writes a per-run manifest (sha256 per file, for integrity verification on
restore), and rotates (deletes) each declared prefix by ITS OWN retention
(``RETENTION``: ``backups/state`` 14 days, ``backups/client`` 365 days) —
one threshold over both would silently eat the year-long client set.

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
    "verify_uploaded",
    "list_backup_dates",
    "CLIENT_PREFIX",
    "CLIENT_KEEP_DAYS",
    "RETENTION",
    "RetentionError",
    "validate_retention",
    "rotate_old_backups",
    "rotate_all_backups",
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

# Клиентский набор (DEV-46) едет под СВОИМ префиксом и хранится ГОД.
CLIENT_PREFIX: str = "backups/client"
CLIENT_KEEP_DAYS: int = 365

# Литеральный список пар (префикс, срок хранения в сутках). Правит ЧЕЛОВЕК:
# он НЕ выводится из других констант и не собирается циклом — выведенный
# список согласен с реализацией по определению и молчит ровно там, где она
# забыла ([[jarvis-literal-lists-not-introspection]]).
#
# Срок хранения — свойство ПРЕФИКСА, а не флаг внутри общего префикса: класса
# объекта в ключе нет, и один порог на оба класса означал бы потерю годового
# набора на пятнадцатые сутки, причём МОЛЧА — для ротации удаление не авария,
# а работа (спека §9.3).
RETENTION: tuple[tuple[str, int], ...] = (
    (BACKUP_PREFIX, KEEP_DAYS),          # ("backups/state", 14)
    (CLIENT_PREFIX, CLIENT_KEEP_DAYS),   # ("backups/client", 365)
)


class RetentionError(Exception):
    """Список ретенции невалиден — ротация не начинается ВООБЩЕ.

    Отдельный класс, а не ValueError: вызывающий обязан отличить «раскладка
    сроков сломана, не удалено ничего» от сбоя самого хранилища.
    """


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
    # Второй конец: что реально ВИДНО в бакете листингом после заливки.
    # ``uploaded`` — это лишь «put_object вернул управление».
    verified: list[str] = field(default_factory=list)

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


def verify_uploaded(
    result: BackupResult,
    *,
    list_objects: Callable[..., list[dict]] = r2_storage.list_objects,
    client: Any | None = None,
    config: R2Config | None = None,
) -> list[dict]:
    """Доказать ДРУГИМ вызовом API, что залитое реально лежит в бакете.

    ``upload_file`` вернувший управление — это ещё не бэкап: объект может не
    появиться (не тот бакет, политика токена на запись без чтения, ретенция).
    Здесь мы перечитываем префикс дня листингом и требуем, чтобы каждый
    ожидаемый ключ присутствовал и имел ненулевой размер.

    Возвращает список проблем (пустой = всё доехало) и наполняет
    ``result.verified``. Сбой самого листинга — тоже проблема: непроверяемый
    бэкап считается несостоявшимся (DEV-18, не глотать), иначе мы возвращаемся
    к молчаливому зелёному, ради которого всё это и делается.
    """
    config = config or load_backup_config()
    prefix = f"{BACKUP_PREFIX}/{result.date}/"

    expected = list(result.uploaded)
    if result.manifest_key:
        expected.append(result.manifest_key[len(prefix):]
                        if result.manifest_key.startswith(prefix) else "manifest.json")

    try:
        objs = list_objects(prefix, client=client, config=config)
    except Exception as exc:  # noqa: BLE001 — честный красный, а не тишина
        logger.error("state backup verification listing failed: %s", exc)
        return [{"rel_path": "*", "error": f"проверка листингом не удалась: {exc}"}]

    sizes = {o["key"]: o.get("size", 0) for o in objs}
    problems: list[dict] = []
    for rel in expected:
        key = f"{prefix}{rel}"
        size = sizes.get(key)
        if size is None:
            problems.append({"rel_path": rel,
                             "error": f"нет в листинге бакета: {key}"})
        elif size <= 0:
            problems.append({"rel_path": rel,
                             "error": f"нулевой размер объекта (0 байт): {key}"})
        else:
            result.verified.append(rel)
    return problems


def run_backup(
    state_root: Path,
    *,
    now: datetime | None = None,
    upload_file: Callable[..., str] = r2_storage.upload_file,
    list_objects: Callable[..., list[dict]] = r2_storage.list_objects,
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

    # Оба конца: заливка посчитана — теперь докажи листингом, что доехало.
    result.failed.extend(
        verify_uploaded(result, list_objects=list_objects, client=client, config=config))

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


def validate_retention(retention: tuple[tuple[str, int], ...] = RETENTION) -> None:
    """Красное (``RetentionError``) на любой раскладке, при которой ротация
    может съесть чужой срок. Проверяется ДО первого листинга и ДО первого
    удаления: это единственное место арки, где ошибка тихо УДАЛЯЕТ, а не
    краснеет.

    Красное на:

    * повторе префикса (в том числе на двух РАЗНЫХ сроках у одного префикса —
      у каждого префикса ровно один срок, у каждого класса ровно один префикс);
    * ``keep_days <= 0`` и на нецелом сроке: «удалить всё» — не срок хранения;
    * ВЛОЖЕННОСТИ: один объявленный префикс является путевым префиксом
      другого. Их листинги перекрылись бы, и короткий срок съел бы длинный;
    * пустом префиксе и префиксе с хвостовым ``/``: листинг строится как
      ``prefix + "/"``, пустой дал бы листинг корня.
    """
    seen: dict[str, int] = {}
    prefixes: list[str] = []
    for pair in retention:
        try:
            prefix, keep_days = pair
        except (TypeError, ValueError):
            raise RetentionError(
                f"пара ретенции должна быть (префикс, срок), получено {pair!r}") from None
        if not isinstance(prefix, str) or not prefix or prefix.endswith("/"):
            raise RetentionError(
                f"префикс должен быть непустой строкой без хвостового '/', "
                f"получено {prefix!r}")
        if isinstance(keep_days, bool) or not isinstance(keep_days, int) or keep_days <= 0:
            raise RetentionError(
                f"срок хранения префикса {prefix!r} должен быть целым > 0, "
                f"получено {keep_days!r}")
        if prefix in seen:
            raise RetentionError(
                f"префикс {prefix!r} объявлен дважды (сроки {seen[prefix]} и "
                f"{keep_days}): у каждого префикса ровно один срок")
        seen[prefix] = keep_days
        prefixes.append(prefix)

    for i, first in enumerate(prefixes):
        for second in prefixes[i + 1:]:
            if second.startswith(first + "/") or first.startswith(second + "/"):
                raise RetentionError(
                    f"префиксы {first!r} и {second!r} вложены друг в друга: их "
                    f"листинги перекрылись бы, и короткий срок съел бы длинный")


def rotate_old_backups(
    *,
    prefix: str = BACKUP_PREFIX,
    keep_days: int = KEEP_DAYS,
    today: datetime | None = None,
    list_objects: Callable[..., list[dict]] = r2_storage.list_objects,
    delete_object: Callable[..., None] = r2_storage.delete_object,
    client: Any | None = None,
    config: R2Config | None = None,
) -> list[str]:
    """Delete every object under a backup date older than ``keep_days`` in the
    GIVEN prefix. Date strings compare lexically == chronologically (YYYY-MM-DD).

    Листинг строго ``prefix + "/"``: ни листинга корня, ни листинга, который
    захватил бы соседний объявленный префикс с ДРУГИМ сроком. Ключ, не лежащий
    под этим префиксом, не удаляется ни при каком возрасте — это следует уже из
    области листинга, но проверяется явно: чужой срок стоит удалённых данных, а
    слой хранения тут не единственная гарантия.
    """
    config = config or load_backup_config()
    today = today or datetime.now(timezone.utc)
    cutoff = (today - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    scope = prefix + "/"
    objs = list_objects(scope, client=client, config=config)
    deleted: list[str] = []
    for o in objs:
        key = o["key"]
        if not key.startswith(scope):
            logger.warning(
                "state backup rotation: ключ вне префикса %s не удаляем: %s", scope, key)
            continue
        date_part = key[len(scope):].split("/", 1)[0]
        if date_part and date_part < cutoff:
            delete_object(key, client=client, config=config)
            deleted.append(key)
    return deleted


def rotate_all_backups(
    *,
    retention: tuple[tuple[str, int], ...] = RETENTION,
    today: datetime | None = None,
    list_objects: Callable[..., list[dict]] = r2_storage.list_objects,
    delete_object: Callable[..., None] = r2_storage.delete_object,
    client: Any | None = None,
    config: R2Config | None = None,
) -> dict[str, list[str]]:
    """Ротация КАЖДОГО объявленного префикса СВОИМ сроком.

    Возвращает ``{префикс: [удалённые ключи]}``. Каждый пройденный префикс
    присутствует в словаре, даже если удалять было нечего: иначе «ротация не
    гонялась» и «нечего удалять» неотличимы.

    FAIL-CLOSED: ``validate_retention`` зовётся ПЕРВЫМ делом. Список невалиден
    → ``RetentionError`` наружу и НИ ОДНОГО вызова ``delete_object``; плохая
    пара не «пропускается, чтобы продолжить остальные».

    Не глотаем (DEV-18): сбой листинга или удаления на одном префиксе летит
    наверх, а не превращает остальные префиксы в тихий пропуск, выглядящий
    успехом. Ловит и рапортует вызывающий (``scripts/state_backup.py``).
    """
    validate_retention(retention)
    config = config or load_backup_config()
    today = today or datetime.now(timezone.utc)
    deleted: dict[str, list[str]] = {}
    for prefix, keep_days in retention:
        deleted[prefix] = rotate_old_backups(
            prefix=prefix,
            keep_days=keep_days,
            today=today,
            list_objects=list_objects,
            delete_object=delete_object,
            client=client,
            config=config,
        )
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
    lines.append(f"Подтверждено листингом: {len(result.verified)}")
    if not result.verified:
        lines.append("🚨 НИ ОДИН объект не подтверждён листингом бакета — бэкапа НЕТ.")
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
