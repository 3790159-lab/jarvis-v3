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

DEV-46 (шаг 2): рядом с обходом ``state/`` живёт ОБНАРУЖЕНИЕ
клиентского набора (``client_sets``, ``orphan_client_dbs`` — второй корень,
корень репозитория) и ЯВНЫЙ запрет на вывоз секретов
(``FORBIDDEN_PATTERNS``, ``assert_not_forbidden``).

DEV-46 (шаг 5): ЗАЛИВКА клиентского набора — ``run_client_backup``. Это
единственное необратимое действие арки наружу, и оно устроено так, чтобы
открытый текст не мог уехать НИ ОДНОЙ веткой: функция начинается с
фейл-клоуза на ключе (нет ``JARVIS_BACKUP_PUBLIC_KEY`` или
``JARVIS_BACKUP_KEY_SALT`` → ``ClientBackupRefused`` ДО первого
``upload_file``), каждый объект шифруется ``backup_crypto.encrypt_for`` с
AAD = ПОЛНЫЙ ключ объекта, база едет СНИМКОМ (``sqlite_snapshot``), а имя
клиента в ключе заменено псевдонимом (§9.1). Манифест НЕ шифруется (§3.4) и
считает sha256 по ШИФРОТЕКСТУ — тому, что реально лежит в бакете: так хост,
не имеющий приватного ключа, доказывает «байты доехали», не умея прочитать
содержимое. Что из этих байтов поднимется РАБОТАЮЩАЯ база, доказывают
``counts`` в манифесте, и только на машине владельца (§9.2 п. 3).

Entry points: ``scripts/state_backup.py`` (daily scheduled task, mirrors
``scripts/morning_digest.py``) and the ``/backup_now``/``/backup_status``
Telegram admin commands (``tools/jarvis_smart_telegram_control.py``).
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from app.services import backup_crypto, backup_sandbox, r2_storage
from app.services.r2_storage import R2Config, R2ConfigError
from app.services.sqlite_snapshot import snapshot_counts, snapshot_sqlite

logger = logging.getLogger(__name__)

__all__ = [
    "CRITICAL_PATTERNS",
    "KEEP_DAYS",
    "BACKUP_PREFIX",
    "BackupResult",
    "load_backup_config",
    "discover_backup_files",
    "ClientSet",
    "client_sets",
    "orphan_client_dbs",
    "ForbiddenTravel",
    "FORBIDDEN_PATTERNS",
    "is_forbidden",
    "assert_not_forbidden",
    "sha256_file",
    "build_manifest",
    "run_backup",
    "ClientBackupRefused",
    "client_object_keys",
    "run_client_backup",
    "format_client_backup_result",
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


# ── Клиентский набор (DEV-46): ВТОРОЙ корень обхода ─────────────────────────
# `discover_backup_files` устроен вокруг ОДНОГО корня `state/` и матчит глобы
# относительно него. Обе пропажи лежат ВНЕ его: база клиента в `.secrets/`,
# реквизиты в `chatter/clients/<slug>/`. Их не «исключали» — их НЕ МОГЛО БЫТЬ
# ВИДНО по построению обхода (спека §1.3).
#
# Второй корень — корень РЕПОЗИТОРИЯ, и главный риск такой правки назван в
# спеке прямо (§2.3): новый корень втянет соседей по каталогу МОЛЧА. Рядом с
# `.secrets/<slug>.db` лежат `<slug>.session`, `*.session.enc`, `entropy.bin`
# — то есть полный доступ к аккаунту клиента. Поэтому здесь пояс и подтяжки:
# набор строится УЗКИМИ путями по слагам из реестра И проходит через финальное
# сито `assert_not_forbidden`. Одного из двух мало.


class ForbiddenTravel(Exception):
    """Запрещённый к вывозу файл попал в отбор бэкапа.

    Отдельный класс, а не ValueError: вызывающий обязан отличить «отбор
    содержит секрет» от любой другой поломки — реакция на это одна и
    немедленная, остановиться.
    """


# ЛИТЕРАЛЬНЫЙ список. Глобы — относительно КОРНЯ РЕПОЗИТОРИЯ, `*` НЕ
# пересекает `/` (см. `is_forbidden`). Правит ЧЕЛОВЕК: список не выводится ни
# из чего и не собирается циклом — выведенный список согласен с реализацией по
# определению и молчит ровно там, где она забыла
# ([[jarvis-literal-lists-not-introspection]]).
FORBIDDEN_PATTERNS: tuple[str, ...] = (
    ".secrets/*.session",
    ".secrets/*.session.enc",
    ".secrets/*.enc",
    ".secrets/entropy.bin",
    ".secrets/*.bak",
    ".secrets/*.db-journal",
    ".secrets/*.db-wal",
    ".secrets/*.db-shm",
    ".env",
    ".env.enc",
    ".env.runpod",
    "state/connect/*.session",
    "state/connect/secrets_bundle.zip",
    "state/api_keys.json",
    "state/ig_accounts.json",
    "state/google_oauth_token.json",
)


def _forbidden_pattern_for(rel_posix: str) -> str | None:
    """Шаблон, под который подпал путь, либо ``None``.

    Отдельно от `is_forbidden`, чтобы исключение называло ПРИЧИНУ, а не только
    факт: «файл запрещён» без шаблона не подсказывает, что чинить.

    Сравнение РЕГИСТРОНЕЗАВИСИМО (и путь, и шаблон приводятся к нижнему
    регистру): NTFS регистр не различает, поэтому `.SECRETS/demo.SESSION`
    откроет ровно тот же session-файл. Сито, которое обходится сменой
    регистра, — не сито. Это строго РАСШИРЯЕТ запрет и ничего не разрешает:
    что совпадало раньше, совпадает и теперь. Заодно отношение к регистру
    здесь совпадает с тем, что у дедупликации в `client_sets`
    (`normalize_path` → `ntpath.normcase`), и в одном модуле не живут два
    разных правила про одно и то же.
    """
    segments = [s.lower() for s in str(rel_posix).replace("\\", "/").split("/")
                if s not in ("", ".")]
    if not segments:
        return None
    for pattern in FORBIDDEN_PATTERNS:
        pat_segments = [p.lower() for p in pattern.split("/")]
        if len(pat_segments) != len(segments):
            continue
        if all(fnmatch.fnmatchcase(seg, pat)
               for seg, pat in zip(segments, pat_segments)):
            return pattern
    return None


def is_forbidden(rel_posix: str) -> bool:
    """Путь относительно корня репозитория подпадает под `FORBIDDEN_PATTERNS`?

    Сопоставление ПОСЕГМЕНТНОЕ: путь и шаблон бьются по `/`, число сегментов
    обязано совпасть, каждый сегмент — `fnmatch.fnmatchcase` по приведённому к
    нижнему регистру виду (см. `_forbidden_pattern_for`: на NTFS регистр не
    различается, и сито, обходимое сменой регистра, ситом не является).

    Через `fnmatch` целиком делать НЕЛЬЗЯ: там `*` съедает `/`, и
    `.secrets/*.session` совпал бы с `.secrets/a/b.session` — то есть запрет,
    написанный про ОДИН каталог, начал бы молча означать «и всё вложенное».
    """
    return _forbidden_pattern_for(rel_posix) is not None


def _rel_to_repo(path: Path, repo_root: Path) -> str:
    """Путь относительно `repo_root` posix-слэшами.

    Относительный и posix — чтобы `missing` и тексты исключений не зависели от
    машины. Файл ВНЕ дерева репозитория называется вслух в логе: под шаблоны,
    которые все репо-относительные, он не подпадёт ни при каком имени, и молча
    считать его безопасным нельзя.
    """
    path = Path(path)
    repo_root = Path(repo_root)
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except (ValueError, OSError):
        pass
    try:
        rel = Path(os.path.relpath(str(path), str(repo_root))).as_posix()
    except ValueError:  # разные диски под Windows — относительного пути нет
        rel = path.as_posix()
    logger.warning(
        "state backup: путь %s лежит ВНЕ дерева репозитория %s (взят как %s) — "
        "репо-относительные запреты к нему неприменимы",
        path, repo_root, rel)
    return rel


def assert_not_forbidden(paths: Iterable[Path], repo_root: Path) -> None:
    """ФЕЙЛ-КЛОУЗ и ГРОМКО: `ForbiddenTravel` с именем файла и причиной.

    Не «отфильтровать молча»: попадание запрещённого в отбор означает, что
    сломан шаблон или чей-то `db:` в реестре указывает на сессию. Тихая
    фильтрация спрятала бы поломку, а цена ошибки здесь — полный доступ к
    аккаунту клиента.
    """
    repo_root = Path(repo_root)
    for path in paths:
        rel = _rel_to_repo(path, repo_root)
        pattern = _forbidden_pattern_for(rel)
        if pattern is not None:
            raise ForbiddenTravel(
                f"в отбор бэкапа попал запрещённый к вывозу файл {rel!r} "
                f"(шаблон {pattern!r}). Это НЕ фильтруется молча: либо сломан "
                f"шаблон, либо путь в реестре клиентов указывает на секрет. "
                f"Цена ошибки здесь — полный доступ к аккаунту клиента."
            )


@dataclass(frozen=True)
class ClientSet:
    """Клиентские данные ОДНОГО слага: история воронки + платёжные реквизиты.

    `db`/`requisites` — ``None``, если файла на диске нет (тогда путь назван в
    `missing`) либо если этот же файл уже отдан ДРУГОМУ слагу дедупликацией
    (см. `client_sets`).
    """

    slug: str
    db: Path | None
    requisites: Path | None
    missing: tuple[str, ...]


def client_sets(
    repo_root: Path, *, registry_text: str | None = None
) -> tuple[ClientSet, ...]:
    """Клиентский набор строится ПО СЛАГАМ ИЗ РЕЕСТРА (§7 п. 1), а не глобом
    по каталогу: без слага нельзя вычислить псевдоним объекта (§9.1), а глоб
    не знает, чей файл нашёл.

    Только `enabled: true`. Дедупликация по РЕАЛЬНОМУ пути файла
    (`chatter.core.client_registry.normalize_path`): volska и demo делят
    `.secrets/demo.db`, и файл обязан ехать ОДИН раз, а не дважды. Файл
    остаётся у ПЕРВОГО слага в порядке реестра, у следующих поле — ``None``,
    и в `missing` он НЕ попадает: он не пропал, он уже отобран.

    Отсутствующий на диске файл — не молчаливый пропуск: путь попадает в
    `missing`. «Файла нет» и «файл не искали» обязаны быть различимы.

    `registry_text` по умолчанию читается из
    ``<repo_root>/chatter/clients/registry.yaml``; нечитаемый или сломанный
    реестр летит наверх (DEV-18), а не превращается в пустой набор — «клиентов
    нет» и «реестр не прочитали» это разные вещи.

    ПЕРЕД возвратом весь отбор проходит `assert_not_forbidden`: узкие пути из
    реестра И финальное сито, одного из двух мало.
    """
    from chatter.core.client_registry import normalize_path, parse_registry

    repo_root = Path(repo_root)
    if registry_text is None:
        registry_text = (repo_root / "chatter" / "clients"
                         / "registry.yaml").read_text(encoding="utf-8")
    entries = parse_registry(registry_text)

    root = str(repo_root)
    seen: set[str] = set()
    selected: list[Path] = []
    out: list[ClientSet] = []
    for entry in entries:
        if not entry.enabled:
            continue
        found: dict[str, Path | None] = {"db": None, "requisites": None}
        missing: list[str] = []
        candidates = (
            ("db", str(entry.db)),
            ("requisites", f"chatter/clients/{entry.slug}/requisites.yaml"),
        )
        for kind, raw in candidates:
            raw = raw.replace("\\", "/")
            path = Path(raw)
            if not path.is_absolute():
                path = repo_root / raw
            rel = _rel_to_repo(path, repo_root)
            if not path.is_file():
                missing.append(rel)
                continue
            key = normalize_path(str(path), root=root)
            if key in seen:
                continue
            seen.add(key)
            found[kind] = path
            selected.append(path)
        out.append(ClientSet(
            slug=entry.slug,
            db=found["db"],
            requisites=found["requisites"],
            missing=tuple(missing),
        ))

    assert_not_forbidden(selected, repo_root)
    return tuple(out)


def orphan_client_dbs(
    repo_root: Path, sets: tuple[ClientSet, ...]
) -> tuple[Path, ...]:
    """`.secrets/*.db`, не принадлежащие НИ ОДНОМУ слагу реестра.

    Обратная сторона реестрового подхода: файл есть, хозяина нет. Молча
    выбросить нельзя — это «шаблон не нашёл ничего» наизнанку (§7 п. 5).

    Глоб `.secrets/*.db` здесь НЕ источник набора (источник — реестр), а
    единственный способ увидеть базу, которую НИКТО не назвал: переименованный
    слаг, забытый остаток, база удалённого из реестра клиента.

    Хозяин — ЛЮБОЙ слаг реестра, включённый или нет. База выключенного
    клиента сиротой не является: у неё есть известный хозяин, а то, что она не
    едет, — следствие осознанного `enabled: false`, записанного человеком в
    самом реестре. Назвать её ничьей значит сказать неправду и приучить
    смотреть мимо настоящих сирот.

    Поэтому реестр читается здесь заново (`enabled` тут не при чём, а `sets`
    содержит только включённых). Если файла реестра на диске нет, хозяева
    берутся из переданных `sets`, и это НАЗЫВАЕТСЯ в логе: ответ в таком
    прогоне беднее, чем обещает докстрока, и знать об этом обязан читатель, а
    не только автор.
    """
    from chatter.core.client_registry import (
        SECRETS_DIRNAME, normalize_path, parse_registry)

    repo_root = Path(repo_root)
    root = str(repo_root)
    owned = {normalize_path(str(s.db), root=root)
             for s in sets if s.db is not None}
    registry_path = repo_root / "chatter" / "clients" / "registry.yaml"
    if registry_path.is_file():
        for entry in parse_registry(registry_path.read_text(encoding="utf-8")):
            raw = str(entry.db).replace("\\", "/")
            path = Path(raw)
            if not path.is_absolute():
                path = repo_root / raw
            owned.add(normalize_path(str(path), root=root))
    else:
        logger.warning(
            "state backup: реестра клиентов нет (%s) — хозяева считаются только "
            "по переданным наборам, база ВЫКЛЮЧЕННОГО клиента будет названа "
            "сиротой", registry_path)
    orphans: list[Path] = []
    for path in sorted((repo_root / SECRETS_DIRNAME).glob("*.db")):
        if not path.is_file():
            continue
        if normalize_path(str(path), root=root) in owned:
            continue
        orphans.append(path)
    return tuple(orphans)


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
    prefix: str = BACKUP_PREFIX,
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

    `prefix` — ПРЕФИКС НАБОРА (``BACKUP_PREFIX`` по умолчанию, то есть
    поведение прежнее до буквы). Параметр появился ради клиентского набора
    (``CLIENT_PREFIX``): у наборов разные префиксы, и сверка листингом обязана
    смотреть на СВОЙ — на чужом каждый ожидаемый ключ выглядел бы пропавшим.
    """
    config = config or load_backup_config()
    prefix = f"{prefix}/{result.date}/"

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


# ── Заливка клиентского набора (DEV-46, шаг 5) ──────────────────────────────
# Единственное необратимое действие арки НАРУЖУ. Ночью его намеренно не
# писали; всё, что ниже, устроено вокруг одного требования: открытый текст не
# уезжает НИ ОДНОЙ веткой.


class ClientBackupRefused(Exception):
    """Клиентский набор НЕ отправлен, и наружу не ушло ни байта.

    Отдельный класс, а не общий провал бэкапа: «ключа нет» — это осознанное
    состояние НАСТРОЙКИ (владелец ещё не завёл пару, §3.3 B), а не авария
    хранилища, и реакция на него другая. Ронять ночную задачу каждый раз,
    пока ключа нет, значит приучить не смотреть на её алерты; МОЛЧАТЬ при
    этом нельзя — вызывающий обязан назвать отказ вслух
    (см. ``scripts/state_backup.py``).
    """


def client_object_keys(slug_pseudonym: str, date_str: str) -> dict:
    """Полные ключи объектов ОДНОГО клиента за дату:
    ``{'db': 'backups/client/<дата>/<псевдоним>/db', 'requisites': ...}``.

    Слага здесь нет и быть не может (§9.1): в ключ едет псевдоним
    (``backup_crypto.pseudonym``). Имена внутри — ``db`` и
    ``requisites.yaml``; они ОДИНАКОВЫ у всех клиентов и потому не выдают
    ничего. Прятать имя в манифесте и оставить его в имени объекта — значит
    запечатать конверт и надписать адрес снаружи.

    Одно место, где ключ собирается, — потому что этот же ключ едет в AAD
    конверта: собранный во второй раз «почти так же» он расшифровку сломает.
    """
    if not isinstance(slug_pseudonym, str) or not slug_pseudonym.strip():
        raise ValueError(
            f"псевдоним слага должен быть непустой строкой, "
            f"получено {slug_pseudonym!r}")
    if not isinstance(date_str, str) or not date_str.strip():
        raise ValueError(
            f"дата должна быть непустой строкой YYYY-MM-DD, "
            f"получено {date_str!r}")
    if "/" in slug_pseudonym or "/" in date_str:
        raise ValueError(
            f"ни псевдоним, ни дата не содержат '/': ключ объекта собирается "
            f"из сегментов, а не из готового пути "
            f"({slug_pseudonym!r}, {date_str!r})")
    base = f"{CLIENT_PREFIX}/{date_str}/{slug_pseudonym}"
    return {"db": f"{base}/db", "requisites": f"{base}/requisites.yaml"}


def _client_manifest_key(date_str: str) -> str:
    return f"{CLIENT_PREFIX}/{date_str}/manifest.json"


def run_client_backup(
    repo_root: Path,
    *,
    now: datetime | None = None,
    env: Mapping[str, str] | None = None,
    upload_file: Callable[..., str] = r2_storage.upload_file,
    list_objects: Callable[..., list[dict]] = r2_storage.list_objects,
    client: Any | None = None,
    config: R2Config | None = None,
) -> BackupResult:
    """Снимок -> счётчики -> шифрование -> заливка -> манифест -> сверка листингом.

    Порядок именно такой и переставлять его нельзя:

    * **ФЕЙЛ-КЛОУЗ НА КЛЮЧЕ идёт ПЕРВЫМ.** Нет ``JARVIS_BACKUP_PUBLIC_KEY``
      или ``JARVIS_BACKUP_KEY_SALT`` — ``ClientBackupRefused`` ДО единого
      вызова ``upload_file`` и до чтения хоть одного клиентского файла.
      Ветки «ну тогда без шифрования» здесь нет и не появится: это главное
      свойство функции, всё остальное — детали.
    * **База едет СНИМКОМ** (``snapshot_sqlite``), а не файловой копией:
      sha256 рваного снимка совпадает с рваным снимком, и проверка
      целостности на нём ЗЕЛЁНАЯ (§4.1–4.2). Раннера при этом НЕ
      останавливаем (§6 п. 4). Временный каталог со снимком — расшифрованные
      клиентские данные на диске — сносится в ``finally``.
    * **Счётчики берутся СО СНИМКА**, а не с живой базы: тогда «на момент
      снимка» (§4.3 шаг 5) выполняется само собой, а не по договорённости.
    * **Каждый объект шифруется своим ключом объекта в AAD** — конверт нельзя
      молча переставить на другую дату или другому клиенту.
    * **Манифест НЕ шифруется** (§3.4) и считает sha256 по ШИФРОТЕКСТУ: хост
      без приватного ключа доказывает «байты доехали», не умея прочитать
      содержимое. Что из этих байтов поднимется РАБОТАЮЩАЯ база, доказывают
      ``counts`` — и только на машине владельца (§9.2 п. 3). Половина
      проверки на хосте возможна и обязана называться своим именем.
    * **Ни одного «уже загружено» без сверки**: в конце — ``verify_uploaded``
      по КЛИЕНТСКОМУ префиксу.

    Провал одного объекта не отменяет остальных, но НАЗЫВАЕТСЯ в
    ``result.failed`` (DEV-18). Туда же попадают реквизиты, которых нет на
    диске (``ClientSet.missing``): «файла нет» и «файл не искали» обязаны
    быть различимы. Набор БЕЗ базы (дедупликация: volska и demo делят один
    файл) ошибкой не является — файл уже отобран у первого слага, — но
    реквизиты такого набора всё равно едут.

    ``result.uploaded``/``verified`` содержат пути ОТНОСИТЕЛЬНО
    ``backups/client/<дата>/`` (``<псевдоним>/db``), ``total_bytes`` —
    размер ШИФРОТЕКСТА, то есть того, что реально занято в бакете.
    """
    # ── ФЕЙЛ-КЛОУЗ НА КЛЮЧЕ. Первое, что делает функция. ─────────────────
    # Ни одной строки клиентских данных не прочитано, ни одного объекта не
    # залито, конфиг R2 ещё даже не собран.
    try:
        public_key = backup_crypto.load_public_key(env)
    except backup_crypto.BackupCryptoError as exc:
        raise ClientBackupRefused(
            f"нет JARVIS_BACKUP_PUBLIC_KEY: {exc}") from exc
    try:
        salt = backup_crypto.load_pseudonym_salt(env)
    except backup_crypto.BackupCryptoError as exc:
        raise ClientBackupRefused(
            f"нет JARVIS_BACKUP_KEY_SALT: {exc}") from exc
    fingerprint = backup_crypto.public_key_fingerprint(public_key)

    repo_root = Path(repo_root)
    now = now or datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    result = BackupResult(date=date_str)
    sets = client_sets(repo_root)
    config = config or load_backup_config()

    entries: list[dict] = []
    day_prefix = f"{CLIENT_PREFIX}/{date_str}/"
    # Временный каталог проходит ТО ЖЕ сито, что песочница дрила: корень из
    # TMPDIR/TEMP/TMP в момент вызова, отказ на дерево репозитория и на
    # `.secrets` — ДО создания каталога. Пока сита здесь не было, заливка
    # писала снимок ЖИВОЙ базы клиента в дерево без единого вопроса, а дрил в
    # то же дерево отказывался распаковывать: два правила на одну вещь, и
    # слабое молчало (амендмент Д).
    try:
        # `extra_roots=(repo_root,)` — дерево, КОТОРОЕ МЫ БЭКАПИМ, запретно
        # наравне с деревом, где лежит код. Сегодня это одно и то же (задача
        # передаёт своё дерево), но защищаем мы именно бэкапимое: разойдись
        # аргумент с расположением кода — снимок клиентской базы открытым
        # текстом лёг бы внутрь него, а проверка смотрела бы не туда.
        tmp_root = backup_sandbox.make_sandbox(
            prefix="jarvis-client-backup-", extra_roots=(repo_root,))
    except backup_sandbox.SandboxRefused as exc:
        raise ClientBackupRefused(
            f"некуда положить снимок: {exc}") from exc
    try:
        for cset in sets:
            pseudo = backup_crypto.pseudonym(cset.slug, salt)
            keys = client_object_keys(pseudo, date_str)

            # Пропажа НАЗЫВАЕТСЯ, а не пропускается молча. `rel_path` —
            # псевдонимный (это тот объект, который НЕ доехал), слаг живёт
            # только в тексте ошибки: сводку читает владелец на своей
            # машине, где слаги и так лежат открытым текстом в дереве
            # (§9.1, «граница, названная вслух»).
            for miss in cset.missing:
                kind = "requisites" if miss.endswith("requisites.yaml") else "db"
                rel_missing = keys[kind][len(day_prefix):]
                result.failed.append({
                    "rel_path": rel_missing,
                    "error": f"файла нет на диске: {miss} (клиент {cset.slug})",
                })
                logger.warning(
                    "client backup: у клиента %s нет файла %s — объект %s не поедет",
                    cset.slug, miss, keys[kind])

            plan: list[tuple[str, Path | None]] = [
                ("db", cset.db), ("requisites", cset.requisites)]
            for kind, source in plan:
                if source is None:
                    # Либо файла нет (уже назван выше), либо он ОТДАН первому
                    # слагу дедупликацией — второе не ошибка и не пропажа.
                    continue
                key = keys[kind]
                rel = key[len(day_prefix):]
                try:
                    counts: dict | None = None
                    if kind == "db":
                        snap = tmp_root / f"{pseudo}.db"
                        snapshot_sqlite(source, snap)
                        counts = snapshot_counts(snap)
                        payload = snap.read_bytes()
                    else:
                        payload = Path(source).read_bytes()

                    # AAD = ПОЛНЫЙ ключ объекта. Не слаг, не псевдоним, не имя
                    # файла: конверт привязан к своему месту целиком.
                    blob = backup_crypto.encrypt_for(
                        public_key, payload, aad=key.encode("utf-8"))
                    del payload

                    enc_path = tmp_root / f"{pseudo}.{kind}.enc"
                    enc_path.write_bytes(blob)
                    upload_file(enc_path, key=key, client=client, config=config)

                    entry = {
                        "rel_path": rel,
                        "size": len(blob),
                        # sha256 ШИФРОТЕКСТА — того, что лежит в бакете.
                        "sha256": hashlib.sha256(blob).hexdigest(),
                    }
                    if counts is not None:
                        entry["counts"] = counts
                    entries.append(entry)
                    result.uploaded.append(rel)
                    result.total_bytes += len(blob)
                except Exception as exc:  # noqa: BLE001 — честный провал объекта
                    logger.warning(
                        "client backup: объект %s (клиент %s) не поехал: %s",
                        key, cset.slug, exc)
                    result.failed.append({"rel_path": rel, "error": str(exc)})

        manifest = {
            "generated_at": now.isoformat(),
            "key_fingerprint": fingerprint,
            "count": len(entries),
            "total_bytes": sum(e["size"] for e in entries),
            "files": entries,
        }
        manifest_key = _client_manifest_key(date_str)
        manifest_path = tmp_root / "manifest.json"
        try:
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8")
            # Манифест едет ОТКРЫТЫМ (§3.4): он нужен, чтобы проверить
            # целостность, не открывая содержимое. Слага в нём нет — только
            # псевдонимные `rel_path`.
            upload_file(manifest_path, key=manifest_key, client=client, config=config)
            result.manifest_key = manifest_key
        except Exception as exc:  # noqa: BLE001
            logger.warning("client backup: манифест не поехал: %s", exc)
            result.failed.append({"rel_path": "manifest.json", "error": str(exc)})
    finally:
        # Во временном каталоге лежат РАСШИФРОВАННЫЕ клиентские данные
        # (снимок базы). Он сносится на ЛЮБОМ пути выхода — на успехе, на
        # провале объекта, на непредвиденном исключении.
        #
        # `ignore_errors=True` здесь стоял и был неправ (DEV-18): незакрытый
        # хэндл SQLite (DEV-48) оставил бы снимок клиентской базы на диске, а
        # ошибку проглотил бы — и никто бы не узнал. Гарантию даёт РЕЗУЛЬТАТ:
        # `remove_sandbox` пересчитывает остаток и называет его поимённо.
        # Говорит он в журнал, а не в stderr: задачу в 04:00 никто не смотрит
        # живьём, и всё сказанное обязано пережить прогон.
        leftovers = backup_sandbox.remove_sandbox(
            tmp_root, lambda msg: logger.error("client backup: %s", msg))
        if leftovers:
            result.failed.append({
                "rel_path": str(tmp_root),
                "error": (f"временный каталог НЕ снесён, в нём остался "
                          f"РАСШИФРОВАННЫЙ снимок клиентской базы "
                          f"({len(leftovers)} файл(ов)) — уберите руками"),
            })

    # Оба конца: заливка посчитана — теперь докажи листингом, что доехало.
    result.failed.extend(verify_uploaded(
        result, prefix=CLIENT_PREFIX, list_objects=list_objects,
        client=client, config=config))
    return result


def format_client_backup_result(result: BackupResult) -> str:
    """Строки сводки про клиентский набор.

    Зелёное хоста называется СВОИМ именем: доехали БАЙТЫ. Что из них
    поднимется работающая база, хост проверить не может — у него нет
    приватного ключа, — и это доказывает недельный дрил у владельца
    (§9.2 п. 3). Иначе «подтверждено листингом» прочтут как «восстановление
    доказано».
    """
    kb = result.total_bytes / 1024.0
    lines = [f"\U0001f512 Клиентский набор за {result.date}: "
             f"{len(result.uploaded)} объектов ({kb:.1f} KB, шифротекст)"]
    lines.append(
        f"Подтверждено листингом: {len(result.verified)} "
        f"— доехали БАЙТЫ; что база откроется, доказывает дрил у владельца")
    if result.failed:
        lines.append(f"⚠️ Клиентский набор, ошибки: {len(result.failed)}")
        for f in result.failed[:5]:
            lines.append(f"  - {f['rel_path']}: {str(f['error'])[:80]}")
    return "\n".join(lines)


def list_backup_dates(
    *,
    prefix: str = BACKUP_PREFIX,
    list_objects: Callable[..., list[dict]] = r2_storage.list_objects,
    client: Any | None = None,
    config: R2Config | None = None,
) -> list[str]:
    """Sorted unique ``YYYY-MM-DD`` dates present under ``prefix/``.

    ``prefix`` по умолчанию ``BACKUP_PREFIX`` — поведение прежнее. Параметр
    нужен дрилу восстановления (§4.3 шаг 1: «скачать ВЧЕРАШНИЙ объект»), а
    вчерашний объект клиентского набора лежит под ``CLIENT_PREFIX``.
    """
    config = config or load_backup_config()
    objs = list_objects(prefix + "/", client=client, config=config)
    dates: set[str] = set()
    for o in objs:
        rest = o["key"][len(prefix) + 1:]
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
    prefix: str = BACKUP_PREFIX,
    download_file: Callable[..., Path] = r2_storage.download_file,
    client: Any | None = None,
    config: R2Config | None = None,
) -> Path:
    """Download one backed-up file to ``dest`` (acceptance-test helper: live
    restore-and-verify of a single file).

    ``prefix`` по умолчанию ``BACKUP_PREFIX`` — поведение прежнее. Дрил
    восстановления качает объект клиентского набора и передаёт
    ``CLIENT_PREFIX``; ``rel_path`` там — ``<псевдоним>/db``.
    """
    config = config or load_backup_config()
    key = f"{prefix}/{date}/{rel_path}"
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
