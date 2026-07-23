"""Реестр клиентов chatter: кто должен жить под гардианом.

ЧИСТЫЙ слой: парсинг + валидация без файловой системы и без сети. Проверки
существования файлов инжектируются callable'ами, поэтому конфликт сессий
тестируется без .secrets и без реальных клиентов.

Почему решение принимается здесь, а не в PowerShell: гардиан — .ps1, а
PowerShell не умеет YAML и плохо тестируется. Вся логика живёт тут под pytest,
наружу отдаётся готовый JSON-план (chatter/registry_cli.py), PowerShell
остаётся тонким исполнителем.
"""
from __future__ import annotations

import ntpath
from dataclasses import dataclass
from typing import Callable, Iterable

import yaml

SECRETS_DIRNAME = ".secrets"


class RegistryError(ValueError):
    """Реестр сломан ЦЕЛИКОМ (не читается / не той формы).

    Про отдельного клиента говорит ClientIssue, а не исключение: ошибка
    конфигурации одного не имеет права ронять весь парк."""


@dataclass(frozen=True)
class ClientEntry:
    slug: str
    enabled: bool
    personas: tuple[str, ...]
    session: str
    db: str


@dataclass(frozen=True)
class ClientIssue:
    slug: str
    error: str


def normalize_path(path: str, *, root: str) -> str:
    """Канонический вид пути ДЛЯ СРАВНЕНИЯ (не для открытия файла).

    Всегда ntpath, даже под Linux-CI: сравниваем windows-пути из реестра, и
    результат не должен зависеть от того, где запущены тесты.

    Без нормализации '.secrets/demo.session', '.secrets\\demo.session' и
    'C:\\jarvis\\.secrets\\demo.session' сравнились бы как три разных файла — и
    валидация конфликта пропустила бы ровно тот случай, ради которого написана
    (volska сегодня пиннится на сессию demo)."""
    if not ntpath.isabs(path):
        path = ntpath.join(root, path)
    return ntpath.normcase(ntpath.normpath(path))


def parse_registry(text: str) -> tuple[ClientEntry, ...]:
    """YAML -> записи реестра.

    Пути по умолчанию выводятся из slug'а — так же, как это делает сам раннер
    (resolve_runtime_paths), поэтому минимальная запись нового клиента это две
    строки."""
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise RegistryError(f"registry.yaml не парсится: {exc}") from exc
    if not isinstance(data, dict):
        raise RegistryError("registry.yaml: ожидался словарь верхнего уровня")
    clients = data.get("clients")
    if clients is None:
        raise RegistryError("registry.yaml: нет ключа 'clients'")
    if not isinstance(clients, dict):
        raise RegistryError(
            "registry.yaml: 'clients' должен быть словарём slug -> настройки")

    out: list[ClientEntry] = []
    for slug, cfg in clients.items():
        slug = str(slug)
        if cfg is None:
            cfg = {}
        if not isinstance(cfg, dict):
            raise RegistryError(
                f"registry.yaml: клиент {slug!r} должен быть словарём настроек")
        personas = cfg.get("personas")
        if personas is None:
            personas = [slug]
        elif isinstance(personas, str):
            personas = [personas]
        out.append(ClientEntry(
            slug=slug,
            enabled=bool(cfg.get("enabled", False)),
            personas=tuple(str(p) for p in personas),
            session=str(cfg.get("session") or f"{SECRETS_DIRNAME}/{slug}.session"),
            db=str(cfg.get("db") or f"{SECRETS_DIRNAME}/{slug}.db"),
        ))
    return tuple(out)


def validate(
    entries: Iterable[ClientEntry], *, root: str,
    session_available: Callable[[str], bool],
    client_dir_exists: Callable[[str], bool],
) -> tuple[tuple[ClientEntry, ...], tuple[ClientIssue, ...]]:
    """(runnable, issues). Выключенные клиенты не валидируются вовсе —
    выключенный клиент с несуществующей сессией это не ошибка."""
    enabled = [e for e in entries if e.enabled]
    issues: list[ClientIssue] = []
    bad: set[str] = set()

    # Конфликты общих рантайм-файлов среди ВКЛЮЧЁННЫХ. Два процесса на одной
    # Telethon-сессии = гонка за запись .session и, в худшем случае, разлогин
    # аккаунта. Проверять до любого запуска.
    for field in ("session", "db"):
        groups: dict[str, list[ClientEntry]] = {}
        for e in enabled:
            groups.setdefault(normalize_path(getattr(e, field), root=root), []).append(e)
        for group in groups.values():
            if len(group) < 2:
                continue
            names = sorted(x.slug for x in group)
            for e in group:
                others = ", ".join(n for n in names if n != e.slug)
                issues.append(ClientIssue(e.slug, (
                    f"registry conflict: {field} {getattr(e, field)!r} shared "
                    f"with enabled client(s) {others}")))
                bad.add(e.slug)

    for e in enabled:
        if e.slug in bad:
            continue
        if not e.personas:
            issues.append(ClientIssue(e.slug, "registry: personas is empty"))
            bad.add(e.slug)
            continue
        if not client_dir_exists(e.slug):
            issues.append(ClientIssue(
                e.slug, f"registry: client dir for {e.slug!r} not found"))
            bad.add(e.slug)
            continue
        if not session_available(e.session):
            # «Доступна» = build_session сможет её открыть: .enc ИЛИ legacy
            # plaintext. Проверять только plaintext-файл нельзя — после
            # cutover P1/P2 его на диске нет вовсе, и живой клиент был бы
            # помечен invalid (см. chatter/registry_cli.session_available).
            #
            # Явная ошибка, а не тихий цикл рестартов: без сессии Telethon
            # уходит в интерактивный запрос кода и висит вечно.
            issues.append(ClientIssue(e.slug, (
                f"registry: session {e.session!r} not available "
                f"(ни {e.session}.enc, ни plaintext)")))
            bad.add(e.slug)

    runnable = tuple(e for e in enabled if e.slug not in bad)
    return runnable, tuple(sorted(issues, key=lambda i: (i.slug, i.error)))
