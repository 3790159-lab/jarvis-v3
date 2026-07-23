# Мультиклиентный гардиан chatter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Один супервизор держит N клиентов chatter, каждый со своей сессией/БД/логом/heartbeat, стартует и останавливается по отдельности, а алерты называют упавшего клиента.

**Architecture:** Решение принимается в Python (чистый парсер+валидатор реестра, покрыт pytest), исполнение — в PowerShell (тонкий супервизор, получает готовый JSON-план). Желаемое состояние — `chatter/clients/registry.yaml`, наблюдаемое — `state/chatter_clients.json`; дашборд позже пишет верх и читает низ.

**Tech Stack:** Python 3.14 (stdlib + PyYAML), PowerShell 5.1, pytest, Windows Task Scheduler.

**Спека:** `docs/superpowers/specs/2026-07-24-chatter-guardian-multiclient-design.md`

---

## ⚠️ Условия работы над аркой

**Работать в отдельном worktree, НЕ в `C:\jarvis`.** Гардиан деплоит из рабочего дерева: если задача перезапустится посреди арки, она подхватит полуготовый скрипт и может убить живого клиента. Worktree создаётся навыком `superpowers:using-git-worktrees` перед Задачей 1.

**Задача 9 (миграция volska) — только после утренних дрилов** (память + Д-7) и только по команде владельца. Задачи 1–8 живого раннера не касаются.

**Все команды pytest — через `.venv`:** `C:\jarvis\.venv\Scripts\python.exe` (в worktree — свой путь до репозитория, venv общий). Bash-синтаксис: `./.venv/Scripts/python.exe`. Коммит-сообщения только через `-F <файл>` (PowerShell here-string `@'...'@` в Bash мусорит `@` в первую строку).

---

## File Structure

**Создать:**

| Файл | Ответственность |
|---|---|
| `chatter/core/client_registry.py` | Чистый парсер + валидатор реестра. Ноль ФС, ноль сети — проверки существования файлов инжектируются callable'ами. |
| `chatter/registry_cli.py` | Тонкий CLI: читает `registry.yaml`, делает ФС-проверки, печатает JSON-план для PowerShell. |
| `chatter/clients/registry.yaml` | Желаемое состояние (реестр клиентов). |
| `scripts/chatter_client.ps1` | Старт/стоп/статус отдельного клиента через правку `enabled` в реестре. |
| `tests/chatter/test_client_registry.py` | Валидация: конфликты, нормализация путей, дефолты. |
| `tests/chatter/test_registry_cli.py` | Контракт JSON-плана. |
| `tests/test_chatter_guardian_multiclient.py` | PowerShell-интеграция: изоляция процессов, конвергенция. |

**Изменить:**

| Файл | Что |
|---|---|
| `chatter/telethon_run.py:201,268,1421,1794+` | `--client <slug>`, per-slug heartbeat. |
| `scripts/chatter_guardian_detached.ps1` | Супервизор: per-slug скоуп процессов, цикл конвергенции, `chatter_clients.json`. |
| `scripts/chatter_watch_check.py:28+` | `--client`, per-slug heartbeat и маркер. |
| `chatter/clients/active.yaml` | Комментарий: гардианом не используется. |
| `docs/chatter/ONBOARDING_MANUAL.md` §4 | Переписать под реестр. |

---

## Task 1: Чистый валидатор реестра

**Files:**
- Create: `chatter/core/client_registry.py`
- Test: `tests/chatter/test_client_registry.py`

- [ ] **Step 1: Написать падающие тесты нормализации и конфликтов**

`tests/chatter/test_client_registry.py`:

```python
"""Реестр клиентов: парсинг + валидация. Ноль ФС — проверки инжектируются."""
from __future__ import annotations

import pytest

from chatter.core.client_registry import (
    ClientEntry, RegistryError, normalize_path, parse_registry, validate,
)

ROOT = r"C:\jarvis"


def _val(entries, *, session_exists=lambda p: True, client_dir_exists=lambda s: True):
    return validate(entries, root=ROOT, session_exists=session_exists,
                    client_dir_exists=client_dir_exists)


# ── нормализация путей ──────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    ".secrets/demo.session",
    ".secrets\\demo.session",
    r"C:\jarvis\.secrets\demo.session",
    r"C:\JARVIS\.secrets\DEMO.session",
    r"C:\jarvis\.secrets\..\.secrets\demo.session",
])
def test_normalize_path_collapses_equivalent_forms(raw):
    """Без этого конфликт сессий пропустит ровно тот случай, ради которого
    написан: volska пиннится на '.secrets/demo.session', а demo — на
    '.secrets\\demo.session'."""
    assert normalize_path(raw, root=ROOT) == normalize_path(
        r"C:\jarvis\.secrets\demo.session", root=ROOT)


def test_normalize_path_keeps_different_files_different():
    a = normalize_path(".secrets/demo.session", root=ROOT)
    b = normalize_path(".secrets/acme.session", root=ROOT)
    assert a != b


# ── конфликты ───────────────────────────────────────────────────────────────

def test_two_enabled_sharing_session_are_both_invalid():
    entries = (
        ClientEntry("volska", True, ("volska",), ".secrets/demo.session", ".secrets/volska.db"),
        ClientEntry("demo", True, ("demo",), ".secrets\\demo.session", ".secrets/demo.db"),
    )
    runnable, issues = _val(entries)
    assert runnable == ()
    by_slug = {i.slug: i.error for i in issues}
    assert set(by_slug) == {"volska", "demo"}
    assert "demo" in by_slug["volska"] and "session" in by_slug["volska"]
    assert "volska" in by_slug["demo"] and "session" in by_slug["demo"]


def test_two_enabled_sharing_db_are_both_invalid():
    entries = (
        ClientEntry("a", True, ("a",), ".secrets/a.session", ".secrets/shared.db"),
        ClientEntry("b", True, ("b",), ".secrets/b.session", ".secrets/shared.db"),
    )
    runnable, issues = _val(entries)
    assert runnable == ()
    assert {i.slug for i in issues} == {"a", "b"}
    assert all("db" in i.error for i in issues)


def test_conflict_with_disabled_client_is_not_a_conflict():
    """Сегодняшняя реальность: volska живёт на demo.session, а demo выключен.
    Эта пара ОБЯЗАНА быть валидной, иначе арка ломает прод в первый же цикл."""
    entries = (
        ClientEntry("volska", True, ("volska",), ".secrets/demo.session", ".secrets/demo.db"),
        ClientEntry("demo", False, ("demo", "demo2"), ".secrets/demo.session", ".secrets/demo.db"),
    )
    runnable, issues = _val(entries)
    assert [e.slug for e in runnable] == ["volska"]
    assert issues == ()


def test_third_client_runs_despite_conflict_between_first_two():
    entries = (
        ClientEntry("a", True, ("a",), ".secrets/shared.session", ".secrets/a.db"),
        ClientEntry("b", True, ("b",), ".secrets/shared.session", ".secrets/b.db"),
        ClientEntry("c", True, ("c",), ".secrets/c.session", ".secrets/c.db"),
    )
    runnable, issues = _val(entries)
    assert [e.slug for e in runnable] == ["c"]
    assert {i.slug for i in issues} == {"a", "b"}


def test_three_way_conflict_names_both_others():
    entries = tuple(
        ClientEntry(s, True, (s,), ".secrets/shared.session", f".secrets/{s}.db")
        for s in ("a", "b", "c")
    )
    runnable, issues = _val(entries)
    assert runnable == ()
    by_slug = {i.slug: i.error for i in issues}
    assert "b" in by_slug["a"] and "c" in by_slug["a"]


# ── прочая валидация ────────────────────────────────────────────────────────

def test_missing_session_file_is_explicit_error_not_silent():
    """Иначе Telethon уйдёт в интерактивный запрос кода и повиснет навсегда."""
    entries = (ClientEntry("acme", True, ("acme",), ".secrets/acme.session", ".secrets/acme.db"),)
    runnable, issues = _val(entries, session_exists=lambda p: False)
    assert runnable == ()
    assert len(issues) == 1
    assert "session" in issues[0].error and "not found" in issues[0].error


def test_unknown_client_dir_is_error():
    entries = (ClientEntry("ghost", True, ("ghost",), ".secrets/ghost.session", ".secrets/ghost.db"),)
    runnable, issues = _val(entries, client_dir_exists=lambda s: False)
    assert runnable == ()
    assert "ghost" in issues[0].error


def test_empty_personas_is_error():
    entries = (ClientEntry("acme", True, (), ".secrets/acme.session", ".secrets/acme.db"),)
    runnable, issues = _val(entries)
    assert runnable == ()
    assert "personas" in issues[0].error


def test_disabled_client_is_never_validated():
    """Выключенный клиент с несуществующей сессией — не ошибка, а просто выключенный."""
    entries = (ClientEntry("old", False, (), "", ""),)
    runnable, issues = _val(entries, session_exists=lambda p: False,
                            client_dir_exists=lambda s: False)
    assert runnable == ()
    assert issues == ()


def test_issues_are_deterministically_ordered():
    entries = tuple(
        ClientEntry(s, True, (s,), ".secrets/shared.session", f".secrets/{s}.db")
        for s in ("z", "a", "m")
    )
    _, issues = _val(entries)
    assert [i.slug for i in issues] == ["a", "m", "z"]


# ── парсинг ─────────────────────────────────────────────────────────────────

def test_parse_minimal_entry_derives_defaults():
    """Новый клиент = две строки. Пути выводятся из slug'а, как в раннере."""
    entries = parse_registry("clients:\n  acme:\n    enabled: true\n")
    assert entries == (
        ClientEntry("acme", True, ("acme",), ".secrets/acme.session", ".secrets/acme.db"),
    )


def test_parse_explicit_pins_win():
    text = (
        "clients:\n"
        "  volska:\n"
        "    enabled: true\n"
        "    personas: [volska]\n"
        "    session: .secrets/demo.session\n"
        "    db: .secrets/demo.db\n"
    )
    (e,) = parse_registry(text)
    assert e.session == ".secrets/demo.session"
    assert e.db == ".secrets/demo.db"


def test_parse_multi_persona_entry():
    text = "clients:\n  demo:\n    enabled: false\n    personas: [demo, demo2]\n"
    (e,) = parse_registry(text)
    assert e.personas == ("demo", "demo2")


def test_parse_enabled_defaults_to_false():
    """Безопасный дефолт: забытый enabled не поднимает клиента молча."""
    (e,) = parse_registry("clients:\n  acme: {}\n")
    assert e.enabled is False


@pytest.mark.parametrize("text,fragment", [
    ("clients: [a, b]\n", "словарём"),
    ("nope: 1\n", "clients"),
    ("clients:\n  acme: 5\n", "acme"),
    ("clients:\n  acme:\n   - broken\n  : :\n", "не парсится"),
])
def test_broken_registry_raises_registry_error(text, fragment):
    with pytest.raises(RegistryError) as exc:
        parse_registry(text)
    assert fragment in str(exc.value)
```

- [ ] **Step 2: Прогнать — должно упасть на импорте**

Run: `./.venv/Scripts/python.exe -m pytest tests/chatter/test_client_registry.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'chatter.core.client_registry'`

- [ ] **Step 3: Реализовать модуль**

`chatter/core/client_registry.py`:

```python
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
    """Реестр сломан ЦЕЛИКОМ (не читается/не той формы). Про отдельного
    клиента говорит ClientIssue, а не исключение: ошибка одного не имеет права
    ронять весь парк."""


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
    поведение не должно зависеть от того, где запущены тесты.

    Без нормализации '.secrets/demo.session', '.secrets\\demo.session' и
    'C:\\jarvis\\.secrets\\demo.session' сравнились бы как три разных файла — и
    валидация конфликта пропустила бы ровно тот случай, ради которого написана
    (volska пиннится на сессию demo)."""
    if not ntpath.isabs(path):
        path = ntpath.join(root, path)
    return ntpath.normcase(ntpath.normpath(path))


def parse_registry(text: str) -> tuple[ClientEntry, ...]:
    """YAML -> записи реестра. Пути по умолчанию выводятся из slug'а, как это
    делает сам раннер (resolve_runtime_paths), поэтому минимальная запись
    нового клиента — две строки."""
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
    session_exists: Callable[[str], bool],
    client_dir_exists: Callable[[str], bool],
) -> tuple[tuple[ClientEntry, ...], tuple[ClientIssue, ...]]:
    """(runnable, issues). Выключенные клиенты не валидируются вовсе."""
    enabled = [e for e in entries if e.enabled]
    issues: list[ClientIssue] = []
    bad: set[str] = set()

    # Конфликты общих рантайм-файлов среди ВКЛЮЧЁННЫХ. Два процесса на одной
    # Telethon-сессии = гонка за запись .session и, в худшем случае, разлогин
    # аккаунта.
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
        if not session_exists(e.session):
            # Явная ошибка, а не тихий цикл рестартов: без session-файла
            # Telethon уходит в интерактивный запрос кода и висит.
            issues.append(ClientIssue(
                e.slug, f"registry: session file {e.session!r} not found"))
            bad.add(e.slug)

    runnable = tuple(e for e in enabled if e.slug not in bad)
    return runnable, tuple(sorted(issues, key=lambda i: (i.slug, i.error)))
```

- [ ] **Step 4: Прогнать — всё зелёное**

Run: `./.venv/Scripts/python.exe -m pytest tests/chatter/test_client_registry.py -q`
Expected: PASS (21 тест)

- [ ] **Step 5: Коммит**

```bash
git add chatter/core/client_registry.py tests/chatter/test_client_registry.py
git commit -F <msg-file>   # feat(chatter): реестр клиентов — чистый парсер + валидация конфликтов
```

---

## Task 2: JSON-план для PowerShell

**Files:**
- Create: `chatter/registry_cli.py`, `chatter/clients/registry.yaml`
- Test: `tests/chatter/test_registry_cli.py`

- [ ] **Step 1: Написать падающий тест контракта**

`tests/chatter/test_registry_cli.py`:

```python
"""Контракт JSON-плана: его читает PowerShell-супервизор, поэтому форма
фиксируется тестом, а не договорённостью."""
from __future__ import annotations

import json

from chatter.registry_cli import build_plan

REG = (
    "clients:\n"
    "  volska:\n"
    "    enabled: true\n"
    "    personas: [volska]\n"
    "    session: .secrets/demo.session\n"
    "    db: .secrets/demo.db\n"
    "  demo:\n"
    "    enabled: false\n"
    "    personas: [demo, demo2]\n"
    "    session: .secrets/demo.session\n"
    "    db: .secrets/demo.db\n"
)


def _plan(text=REG, *, session_exists=lambda p: True, client_dir_exists=lambda s: True):
    return build_plan(text, root=r"C:\jarvis", session_exists=session_exists,
                      client_dir_exists=client_dir_exists)


def test_plan_lists_every_client_including_disabled():
    """Супервизору нужен ПОЛНЫЙ список: выключенных надо не только не
    запускать, но и остановить, если они ещё живы."""
    plan = _plan()
    assert [c["slug"] for c in plan["clients"]] == ["volska", "demo"]


def test_runnable_client_carries_launch_arguments():
    (volska,) = [c for c in _plan()["clients"] if c["slug"] == "volska"]
    assert volska["desired"] == "enabled"
    assert volska["runnable"] is True
    assert volska["error"] is None
    assert volska["personas"] == ["volska"]
    assert volska["session"] == ".secrets/demo.session"
    assert volska["db"] == ".secrets/demo.db"


def test_disabled_client_is_not_runnable_and_has_no_error():
    (demo,) = [c for c in _plan()["clients"] if c["slug"] == "demo"]
    assert demo["desired"] == "disabled"
    assert demo["runnable"] is False
    assert demo["error"] is None


def test_conflict_surfaces_as_error_on_both():
    text = REG.replace("  demo:\n    enabled: false", "  demo:\n    enabled: true")
    plan = _plan(text)
    errs = {c["slug"]: c["error"] for c in plan["clients"]}
    assert errs["volska"] and "demo" in errs["volska"]
    assert errs["demo"] and "volska" in errs["demo"]
    assert all(c["runnable"] is False for c in plan["clients"])


def test_plan_is_json_serialisable():
    """PowerShell читает это через ConvertFrom-Json — никаких кортежей."""
    json.dumps(_plan())


def test_broken_registry_reports_fatal_not_crash():
    """Сломанный реестр не должен ронять гардиан: он обязан узнать причину и
    сказать её владельцу."""
    plan = _plan("nope: 1\n")
    assert plan["clients"] == []
    assert "clients" in plan["fatal"]
```

- [ ] **Step 2: Прогнать — должно упасть**

Run: `./.venv/Scripts/python.exe -m pytest tests/chatter/test_registry_cli.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'chatter.registry_cli'`

- [ ] **Step 3: Реализовать CLI**

`chatter/registry_cli.py`:

```python
"""JSON-план реестра для PowerShell-супервизора.

    python -m chatter.registry_cli            -> план на stdout
    python -m chatter.registry_cli --registry <путь>

Тонкий слой: вся логика в chatter.core.client_registry, здесь только ФС и
сериализация. Гардиан читает вывод через ConvertFrom-Json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable

from chatter.core.client_registry import RegistryError, parse_registry, validate

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = ROOT / "chatter" / "clients" / "registry.yaml"


def build_plan(
    text: str, *, root: str,
    session_exists: Callable[[str], bool],
    client_dir_exists: Callable[[str], bool],
) -> dict:
    """{'fatal': str|None, 'clients': [ ... ]} — ПОЛНЫЙ список клиентов."""
    try:
        entries = parse_registry(text)
    except RegistryError as exc:
        # Не поднимаем исключение: гардиан обязан пережить сломанный реестр и
        # сообщить причину, а не умереть молча (DEV-18).
        return {"fatal": str(exc), "clients": []}

    runnable, issues = validate(
        entries, root=root, session_exists=session_exists,
        client_dir_exists=client_dir_exists)
    runnable_slugs = {e.slug for e in runnable}
    errors = {i.slug: i.error for i in issues}
    return {
        "fatal": None,
        "clients": [
            {
                "slug": e.slug,
                "desired": "enabled" if e.enabled else "disabled",
                "runnable": e.slug in runnable_slugs,
                "error": errors.get(e.slug),
                "personas": list(e.personas),
                "session": e.session,
                "db": e.db,
            }
            for e in entries
        ],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="JSON-план реестра клиентов chatter")
    p.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    p.add_argument("--root", default=str(ROOT))
    args = p.parse_args(argv)

    reg = Path(args.registry)
    if not reg.exists():
        print(json.dumps({"fatal": f"registry not found: {reg}", "clients": []}))
        return 0

    plan = build_plan(
        reg.read_text(encoding="utf-8"),
        root=args.root,
        session_exists=lambda s: (Path(args.root) / s).exists(),
        client_dir_exists=lambda slug: (
            Path(args.root) / "chatter" / "clients" / slug).is_dir(),
    )
    print(json.dumps(plan, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Создать реестр**

`chatter/clients/registry.yaml`:

```yaml
# Реестр клиентов chatter: КТО ДОЛЖЕН ЖИТЬ под гардианом (desired state).
#
# Одна запись = один процесс раннера со своими сессией, БД, логом и heartbeat.
# Внутри записи может быть несколько персон (personas: [demo, demo2]) — это
# сегодняшнее поведение одного раннера с несколькими персонами.
#
# Подключить клиента = добавить запись сюда. Правка PowerShell-скрипта гардиана
# НЕ требуется. Минимальная запись — две строки: пути выводятся из slug'а
# (.secrets/<slug>.session, .secrets/<slug>.db).
#
# ⚠️ ДВА ВКЛЮЧЁННЫХ КЛИЕНТА НЕ МОГУТ ДЕЛИТЬ session ИЛИ db — валидация
# отклонит обоих. Два процесса на одной Telethon-сессии = гонка за запись
# .session вплоть до разлогина аккаунта.
#
# Наблюдаемое состояние (кто живёт на самом деле) — state/chatter_clients.json.
# Старт/стоп клиента: scripts\chatter_client.ps1 -Slug <s> -Action start|stop
clients:
  volska:
    enabled: true
    personas: [volska]
    session: .secrets/demo.session   # пин: volska живёт на сессии demo-аккаунта
    db: .secrets/demo.db

  demo:
    enabled: false                   # взаимоисключим с volska: тот же аккаунт
    personas: [demo, demo2]
    session: .secrets/demo.session
    db: .secrets/demo.db
```

- [ ] **Step 5: Прогнать оба набора тестов**

Run: `./.venv/Scripts/python.exe -m pytest tests/chatter/test_registry_cli.py tests/chatter/test_client_registry.py -q`
Expected: PASS

- [ ] **Step 6: Проверить CLI живьём**

Run: `./.venv/Scripts/python.exe -m chatter.registry_cli`
Expected: одна строка JSON; `volska` — `"runnable": true`, `demo` — `"desired": "disabled"`, `"fatal": null`

- [ ] **Step 7: Коммит**

```bash
git add chatter/registry_cli.py chatter/clients/registry.yaml tests/chatter/test_registry_cli.py
git commit -F <msg-file>   # feat(chatter): JSON-план реестра для супервизора
```

---

## Task 3: `--client` и per-slug heartbeat в раннере

**Files:**
- Modify: `chatter/telethon_run.py:201,268,1421,1794+`
- Test: `tests/chatter/test_telethon_run.py`

- [ ] **Step 1: Написать падающие тесты**

Добавить в `tests/chatter/test_telethon_run.py`:

```python
def test_heartbeat_path_for_derives_per_client_file():
    """N раннеров, пишущих в один heartbeat, делают супервизор слепым: он
    считает живым любого, пока жив хоть один."""
    from chatter.telethon_run import heartbeat_path_for
    assert heartbeat_path_for("volska") == Path("state") / "chatter_heartbeat_volska.txt"
    assert heartbeat_path_for("acme") == Path("state") / "chatter_heartbeat_acme.txt"


def test_heartbeat_path_for_without_client_keeps_legacy_path():
    """Ручной запуск без --client ведёт себя как раньше."""
    from chatter.telethon_run import HEARTBEAT_PATH, heartbeat_path_for
    assert heartbeat_path_for(None) == HEARTBEAT_PATH
    assert heartbeat_path_for("") == HEARTBEAT_PATH


def test_client_arg_is_parsed_and_defaults_to_none():
    from chatter.telethon_run import build_arg_parser
    assert build_arg_parser().parse_args([]).client is None
    assert build_arg_parser().parse_args(["--client", "volska"]).client == "volska"
```

- [ ] **Step 2: Прогнать — упадёт**

Run: `./.venv/Scripts/python.exe -m pytest tests/chatter/test_telethon_run.py -q -k "heartbeat_path_for or client_arg"`
Expected: FAIL, `ImportError: cannot import name 'heartbeat_path_for'`

- [ ] **Step 3: Реализовать**

В `chatter/telethon_run.py` после `HEARTBEAT_PATH` (:201):

```python
def heartbeat_path_for(client: str | None) -> Path:
    """Per-client heartbeat. Без --client — легаси-путь (ручной запуск).

    Общий файл на N раннеров сделал бы супервизор слепым: свежий heartbeat
    одного клиента читался бы как признак жизни всех."""
    if not client:
        return HEARTBEAT_PATH
    return Path("state") / f"chatter_heartbeat_{client}.txt"
```

В парсере аргументов (:1794+) добавить **последним** аргументом:

```python
    # ПОСЛЕДНИМ и с этим именем намеренно: значение попадает в командную строку
    # процесса, и по нему супервизор точечно находит/убивает раннер ИМЕННО
    # этого клиента (раньше матч шёл по модулю и бил всех сразу).
    p.add_argument("--client", default=None,
                   help="slug клиента: идентичность процесса + путь heartbeat")
```

Вынести создание парсера в `build_arg_parser()` (сам `main` зовёт его), затем прокинуть `heartbeat_path_for(args.client)` в задачу heartbeat (:1421).

- [ ] **Step 4: Прогнать — зелёное, регрессов нет**

Run: `./.venv/Scripts/python.exe -m pytest tests/chatter/test_telethon_run.py -q`
Expected: PASS

- [ ] **Step 5: Полный прогон chatter**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -m pytest tests/chatter -q -p no:cacheprovider`
Expected: PASS, счётчик = базовый (1063) + новые

- [ ] **Step 6: Коммит**

```bash
git add chatter/telethon_run.py tests/chatter/test_telethon_run.py
git commit -F <msg-file>   # feat(chatter): --client + per-slug heartbeat в раннере
```

---

## Task 4: Алерты называют клиента

**Files:**
- Modify: `scripts/chatter_watch_check.py:28+`
- Test: `tests/test_chatter_watch_check.py`

- [ ] **Step 1: Написать падающие тесты**

```python
def test_paths_for_client_are_per_slug():
    from scripts.chatter_watch_check import marker_path_for, heartbeat_path_for
    assert heartbeat_path_for("volska").name == "chatter_heartbeat_volska.txt"
    assert marker_path_for("volska").name == "chatter_watch_alert_volska.json"


def test_paths_without_client_stay_legacy():
    from scripts.chatter_watch_check import (
        HEARTBEAT_PATH, MARKER_PATH, heartbeat_path_for, marker_path_for)
    assert heartbeat_path_for(None) == HEARTBEAT_PATH
    assert marker_path_for(None) == MARKER_PATH


def test_alert_text_names_the_client():
    """Без имени клиента владелец не знает, кого чинить."""
    from scripts.chatter_watch_check import format_alert
    assert "volska" in format_alert(client="volska", state="down")
    assert "volska" in format_alert(client="volska", state="up")


def test_alert_text_without_client_is_unchanged():
    from scripts.chatter_watch_check import format_alert
    assert format_alert(client=None, state="down")
```

- [ ] **Step 2: Прогнать — упадёт**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_chatter_watch_check.py -q`
Expected: FAIL, `ImportError: cannot import name 'marker_path_for'`

- [ ] **Step 3: Реализовать**

В `scripts/chatter_watch_check.py`: добавить `--client`, функции `heartbeat_path_for` / `marker_path_for` / `format_alert`, прокинуть в `main`. **Сохранить stdlib-only** — ни одного нового импорта вне stdlib (это причина, по которой алертер переживает поломку пакета `chatter`).

- [ ] **Step 4: Прогнать**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_chatter_watch_check.py -q`
Expected: PASS

- [ ] **Step 5: Проверить stdlib-only фактом**

Run: `./.venv/Scripts/python.exe -c "import ast,sys; m=ast.parse(open('scripts/chatter_watch_check.py',encoding='utf-8').read()); print(sorted({(n.module or '').split('.')[0] for n in ast.walk(m) if isinstance(n,ast.ImportFrom)} | {a.name.split('.')[0] for n in ast.walk(m) if isinstance(n,ast.Import) for a in n.names}))"`
Expected: только stdlib (`argparse`, `json`, `os`, `re`, `sys`, `time`, `urllib`, `pathlib`)

- [ ] **Step 6: Коммит**

```bash
git add scripts/chatter_watch_check.py tests/test_chatter_watch_check.py
git commit -F <msg-file>   # feat(chatter): алерты per-client — маркер, heartbeat и текст с именем
```

---

## Task 5: 🔴 Изоляция процессов (главный регресс арки)

**Files:**
- Modify: `scripts/chatter_guardian_detached.ps1` (`Get-RunnerProcesses`, `Stop-OldRunner`)
- Test: `tests/test_chatter_guardian_multiclient.py`

Дефект, который здесь снимается: `Get-RunnerProcesses` матчит `*$Root*chatter.telethon_run*` без различения клиента, `Stop-OldRunner` бьёт всех найденных. Пока клиент один — незаметно; со вторым клиентом запуск одного убивает другого.

- [ ] **Step 1: Написать падающий тест на двух живых фейковых раннерах**

`tests/test_chatter_guardian_multiclient.py` (паттерн — `tests/test_bot_guardian_stop_old_bot.py`: dot-source настоящего `.ps1` с `-Root <tmp> -NoLoop`, без Pester, `C:\jarvis` не трогаем):

```python
"""Мультиклиентный гардиан: изоляция процессов и конвергенция.

Гоняем НАСТОЯЩИЙ .ps1 через dot-source с -Root <tmp> -NoLoop. Фейковые раннеры —
реальные процессы python.exe с той же формой командной строки, что у боевого
раннера (включая --client), а не моки: проверяемое поведение — матч по
командной строке, и мок здесь доказывал бы только сам себя.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="гардиан — Windows-only (PowerShell + taskkill + Win32_Process)")

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "chatter_guardian_detached.ps1"


def _run_ps(body: str, root: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    command = f". '{SCRIPT}' -Root '{root}' -NoLoop\n{body}\n"
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True, timeout=timeout,
        encoding="utf-8", errors="replace")


def _fake_runner(root: Path, slug: str) -> subprocess.Popen:
    """Живой процесс с командной строкой боевой формы:
    <root>\\.venv\\Scripts\\python.exe -u -m chatter.telethon_run ... --client <slug>
    Матч идёт по CommandLine, поэтому важен именно её вид, а не что внутри."""
    venv = root / ".venv" / "Scripts"
    venv.mkdir(parents=True, exist_ok=True)
    py = venv / "python.exe"
    if not py.exists():
        py.write_bytes(Path(sys.executable).read_bytes())
    script = root / "chatter.telethon_run"
    script.write_text("import time\nwhile True: time.sleep(0.2)\n", encoding="utf-8")
    return subprocess.Popen(
        [str(py), "-u", str(script), "--llm", "real", "--client", slug],
        cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_stop_old_runner_kills_only_the_named_client(tmp_path):
    """🔴 ГЛАВНЫЙ РЕГРЕСС АРКИ. До фикса Stop-OldRunner убивал обоих:
    матч шёл по '*chatter.telethon_run*' без различения клиента, поэтому
    подъём второго клиента гасил живого первого."""
    a = _fake_runner(tmp_path, "aaa")
    b = _fake_runner(tmp_path, "bbb")
    try:
        time.sleep(1.5)
        assert a.poll() is None and b.poll() is None, "фейковые раннеры не поднялись"

        res = _run_ps("Stop-OldRunner -Slug aaa | Out-Null", tmp_path)
        assert res.returncode == 0, res.stderr

        deadline = time.time() + 15
        while a.poll() is None and time.time() < deadline:
            time.sleep(0.3)
        assert a.poll() is not None, "клиент aaa должен быть убит"
        assert b.poll() is None, (
            "клиент bbb ОБЯЗАН остаться живым — это и есть дефект, "
            "ради которого делается арка")
    finally:
        for p in (a, b):
            if p.poll() is None:
                p.kill()


def test_get_runner_processes_is_scoped_to_slug(tmp_path):
    a = _fake_runner(tmp_path, "aaa")
    b = _fake_runner(tmp_path, "bbb")
    try:
        time.sleep(1.5)
        res = _run_ps("(Get-RunnerProcesses -Slug aaa | Measure-Object).Count", tmp_path)
        assert res.stdout.strip().splitlines()[-1] == "1", res.stdout
    finally:
        for p in (a, b):
            if p.poll() is None:
                p.kill()


def test_slug_match_does_not_catch_prefix_siblings(tmp_path):
    """'--client volska' не должен матчить '--client volska2' — иначе
    подъём volska2 убьёт volska."""
    v2 = _fake_runner(tmp_path, "volska2")
    try:
        time.sleep(1.5)
        res = _run_ps("(Get-RunnerProcesses -Slug volska | Measure-Object).Count", tmp_path)
        assert res.stdout.strip().splitlines()[-1] == "0", res.stdout
    finally:
        if v2.poll() is None:
            v2.kill()


def test_get_runner_processes_ignores_other_roots(tmp_path):
    """Раннер чужого инстанса (другой $Root) не наш — не трогаем."""
    other = tmp_path.parent / (tmp_path.name + "_other")
    other.mkdir(exist_ok=True)
    p = _fake_runner(other, "aaa")
    try:
        time.sleep(1.5)
        res = _run_ps("(Get-RunnerProcesses -Slug aaa | Measure-Object).Count", tmp_path)
        assert res.stdout.strip().splitlines()[-1] == "0", res.stdout
    finally:
        if p.poll() is None:
            p.kill()


def _legacy_runner(root: Path) -> subprocess.Popen:
    """Раннер СТАРОЙ формы: без --client (так его запускал прежний скрипт)."""
    venv = root / ".venv" / "Scripts"
    venv.mkdir(parents=True, exist_ok=True)
    py = venv / "python.exe"
    if not py.exists():
        py.write_bytes(Path(sys.executable).read_bytes())
    script = root / "chatter.telethon_run"
    script.write_text("import time\nwhile True: time.sleep(0.2)\n", encoding="utf-8")
    return subprocess.Popen(
        [str(py), "-u", str(script), "--llm", "real"],
        cwd=str(root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_legacy_runner_without_client_is_swept(tmp_path):
    """🔴 МИНА ДЕПЛОЯ (спека §9.1). Живой раннер запущен СТАРЫМ скриптом, у него
    нет --client. Новый супервизор ищет по токену --client, живого не находит,
    считает клиента упавшим и поднимает ВТОРОЙ раннер — два процесса на одной
    demo.session, ровно та катастрофа, ради которой написана валидация §4,
    только протащенная через дверь, которую валидация не сторожит."""
    legacy = _legacy_runner(tmp_path)
    try:
        time.sleep(1.5)
        assert legacy.poll() is None
        # Легаси НЕ виден точечному поиску — это и есть причина мины.
        res = _run_ps("(Get-RunnerProcesses -Slug volska | Measure-Object).Count", tmp_path)
        assert res.stdout.strip().splitlines()[-1] == "0"
        # ...поэтому супервизор обязан зачистить его отдельно, до конвергенции.
        res = _run_ps("Stop-LegacyRunners | Out-Null", tmp_path)
        assert res.returncode == 0, res.stderr
        deadline = time.time() + 15
        while legacy.poll() is None and time.time() < deadline:
            time.sleep(0.3)
        assert legacy.poll() is not None, "легаси-раннер должен быть зачищен"
    finally:
        if legacy.poll() is None:
            legacy.kill()


def test_legacy_sweep_does_not_touch_managed_runners(tmp_path):
    """Зачистка бьёт ТОЛЬКО процессы без --client: иначе она убила бы клиентов,
    которых сама же и подняла, и супервизор зациклился бы на рестартах."""
    managed = _fake_runner(tmp_path, "aaa")
    try:
        time.sleep(1.5)
        res = _run_ps("Stop-LegacyRunners | Out-Null", tmp_path)
        assert res.returncode == 0, res.stderr
        time.sleep(1.0)
        assert managed.poll() is None, "клиент с --client зачисткой не трогается"
    finally:
        if managed.poll() is None:
            managed.kill()
```

- [ ] **Step 2: Прогнать — упадёт**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_chatter_guardian_multiclient.py -q`
Expected: FAIL — `Get-RunnerProcesses` пока не принимает `-Slug`

- [ ] **Step 3: Реализовать скоуп по клиенту**

В `scripts/chatter_guardian_detached.ps1`:

```powershell
function Get-RunnerProcesses {
    # INSTANCE-SCOPED по $Root И CLIENT-SCOPED по -Slug.
    #
    # Два оператора намеренно:
    #   -like  для $Root/модуля — в пути windows-бэкслеши, в regex они были бы
    #          escape-последовательностями;
    #   -match для --client <slug> — нужна ГРАНИЦА ТОКЕНА. '*--client volska*'
    #          через -like поймал бы и '--client volska2', то есть подъём
    #          volska2 убил бы volska.
    param([Parameter(Mandatory)][string]$Slug)
    $token = '--client\s+' + [regex]::Escape($Slug) + '(\s|$)'
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -like "*$Root*chatter.telethon_run*" -and
            $_.CommandLine -match $token
        }
}

function Stop-LegacyRunners {
    # МИНА ДЕПЛОЯ (спека §9.1). Раннер, запущенный ПРЕЖНИМ скриптом, не имеет
    # --client в командной строке, поэтому Get-RunnerProcesses -Slug его не
    # видит. Без этой зачистки новый супервизор решит, что клиент упал, и
    # поднимет второй процесс на ту же Telethon-сессию.
    #
    # Зовётся ОДИН раз перед первой конвергенцией. После миграции легаси-формы
    # не возникает никогда (супервизор всегда передаёт --client), поэтому
    # зачистка самоустраняется и повторного вреда не несёт.
    $legacy = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -like "*$Root*chatter.telethon_run*" -and
            $_.CommandLine -notmatch '--client(\s|$)'
        }
    foreach ($p in $legacy) {
        & taskkill.exe /PID $p.ProcessId /T /F *> $null
        Write-G "legacy-раннер без --client зачищен (PID $($p.ProcessId)) - миграция §9.1"
    }
    if ($legacy) { Start-Sleep -Milliseconds 700 }
}

function Stop-OldRunner {
    param([Parameter(Mandatory)][string]$Slug, [int]$MaxWaitSec = 10)

    Get-RunnerProcesses -Slug $Slug | ForEach-Object {
        & taskkill.exe /PID $_.ProcessId /T /F *> $null
        Write-G "[$Slug] taskkill sent to runner proc $($_.ProcessId) (+tree)"
    }
    $deadline = (Get-Date).AddSeconds($MaxWaitSec)
    while ((Get-RunnerProcesses -Slug $Slug) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 300
    }
    if (Get-RunnerProcesses -Slug $Slug) {
        Write-G "[$Slug] Stop-OldRunner: раннер жив после ${MaxWaitSec}s - НЕ стартую новый"
        return $false
    }
    Start-Sleep -Milliseconds 700
    return $true
}
```

- [ ] **Step 4: Прогнать — зелёное**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_chatter_guardian_multiclient.py -q`
Expected: PASS (6 тестов: 4 скоуп + 2 зачистка легаси)

- [ ] **Step 5: Коммит**

```bash
git add scripts/chatter_guardian_detached.ps1 tests/test_chatter_guardian_multiclient.py
git commit -F <msg-file>   # fix(chatter): гардиан больше не убивает чужого клиента (скоуп по --client)
```

---

## Task 6: Цикл конвергенции + наблюдаемое состояние

**Files:**
- Modify: `scripts/chatter_guardian_detached.ps1` (удалить `SEMIDEMO OVERRIDE`, переписать `Start-Runner`, `Test-Runner`, главный цикл)
- Test: `tests/test_chatter_guardian_multiclient.py`

- [ ] **Step 1: Написать падающие тесты конвергенции**

Добавить в `tests/test_chatter_guardian_multiclient.py`:

```python
import json


def _registry(root: Path, text: str) -> None:
    d = root / "chatter" / "clients"
    d.mkdir(parents=True, exist_ok=True)
    (d / "registry.yaml").write_text(text, encoding="utf-8")


def test_disabled_client_is_stopped_not_killed_silently(tmp_path):
    """enabled: false у живого клиента => остановлен, state=stopped."""
    _registry(tmp_path, "clients:\n  aaa:\n    enabled: false\n")
    p = _fake_runner(tmp_path, "aaa")
    try:
        time.sleep(1.5)
        res = _run_ps("Invoke-Converge | Out-Null; Write-ClientState", tmp_path)
        assert res.returncode == 0, res.stderr
        state = json.loads((tmp_path / "state" / "chatter_clients.json").read_text("utf-8"))
        assert state["clients"]["aaa"]["state"] == "stopped"
    finally:
        if p.poll() is None:
            p.kill()


def test_running_client_is_not_killed_by_a_validation_error(tmp_path):
    """Опечатка в реестре не имеет права ронять ЖИВОГО клиента: валидация,
    написанная ради защиты прода, не должна становиться способом его уронить
    (спека §4 правило 7)."""
    _registry(tmp_path, textwrap.dedent("""\
        clients:
          aaa:
            enabled: true
            session: .secrets/shared.session
          bbb:
            enabled: true
            session: .secrets/shared.session
    """))
    a = _fake_runner(tmp_path, "aaa")
    try:
        time.sleep(1.5)
        res = _run_ps("Invoke-Converge | Out-Null; Write-ClientState", tmp_path)
        assert res.returncode == 0, res.stderr
        assert a.poll() is None, "живой клиент убит из-за конфликта реестра"
        state = json.loads((tmp_path / "state" / "chatter_clients.json").read_text("utf-8"))
        assert state["clients"]["aaa"]["state"] == "invalid"
        assert "bbb" in state["clients"]["aaa"]["last_error"]
    finally:
        if a.poll() is None:
            a.kill()


def test_observed_state_distinguishes_stopped_from_down(tmp_path):
    """'выключен' и 'упал' требуют противоположной реакции; слипшись, они дают
    либо ложные алерты, либо пропущенные аварии."""
    _registry(tmp_path, textwrap.dedent("""\
        clients:
          off_one:
            enabled: false
          down_one:
            enabled: true
    """))
    (tmp_path / ".secrets").mkdir(exist_ok=True)
    (tmp_path / ".secrets" / "down_one.session").write_text("", encoding="utf-8")
    (tmp_path / "chatter" / "clients" / "down_one").mkdir(parents=True, exist_ok=True)
    res = _run_ps("Write-ClientState", tmp_path)
    assert res.returncode == 0, res.stderr
    state = json.loads((tmp_path / "state" / "chatter_clients.json").read_text("utf-8"))
    assert state["clients"]["off_one"]["state"] == "stopped"
    assert state["clients"]["down_one"]["state"] == "down"


def test_client_state_json_is_valid_and_has_full_shape(tmp_path):
    _registry(tmp_path, "clients:\n  aaa:\n    enabled: false\n")
    _run_ps("Write-ClientState", tmp_path)
    state = json.loads((tmp_path / "state" / "chatter_clients.json").read_text("utf-8"))
    assert isinstance(state["updated_ts"], int)
    entry = state["clients"]["aaa"]
    for key in ("desired", "state", "pid", "heartbeat_ts",
                "last_transition_ts", "consecutive_fail", "last_error"):
        assert key in entry, f"нет поля {key} — дашборд позже читает именно это"


def test_broken_registry_does_not_crash_the_supervisor(tmp_path):
    _registry(tmp_path, "nope: 1\n")
    res = _run_ps("Write-ClientState", tmp_path)
    assert res.returncode == 0, res.stderr
    state = json.loads((tmp_path / "state" / "chatter_clients.json").read_text("utf-8"))
    assert state["fatal"]
```

- [ ] **Step 2: Прогнать — упадёт**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_chatter_guardian_multiclient.py -q -k "converge or state or registry"`
Expected: FAIL — нет `Invoke-Converge` / `Write-ClientState`

- [ ] **Step 3: Реализовать супервизор**

В `scripts/chatter_guardian_detached.ps1`:

1. **Удалить блок `SEMIDEMO OVERRIDE` целиком** (хардкод volska).
2. Пути сделать per-client функциями:

```powershell
function Get-ClientPaths {
    param([Parameter(Mandatory)][string]$Slug)
    [pscustomobject]@{
        Err  = Join-Path $logDir   "chatter_$Slug.log"
        Out  = Join-Path $logDir   "chatter_$Slug.stdout.log"
        Hb   = Join-Path $stateDir "chatter_heartbeat_$Slug.txt"
        Lock = Join-Path $lockDir  "chatter_runner_$Slug.pid"
    }
}
```

3. План из реестра:

```powershell
function Get-RegistryPlan {
    # Решение принимает Python (там оно под pytest), мы только исполняем.
    try {
        $raw = & $py -m chatter.registry_cli --root $Root 2>$null
        if (-not $raw) { throw "пустой ответ registry_cli" }
        return ($raw | ConvertFrom-Json)
    } catch {
        # DEV-18: сломанный реестр не имеет права уронить гардиан молча.
        Write-G "Get-RegistryPlan FAILED: $($_.Exception.Message)"
        return [pscustomobject]@{ fatal = $_.Exception.Message; clients = @() }
    }
}
```

4. `Test-Runner -Slug` — процесс есть **И** свежий per-slug heartbeat.
5. `Start-Runner -Slug -Personas -Session -Db` — `Stop-OldRunner -Slug`, затем `Start-Process` с `--personas <...> --client <slug>` и env `TELETHON_SESSION`/`CHATTER_DB` для этого процесса.
6. `Invoke-Converge` — по плану: `runnable` → поднять/держать; `desired=disabled` → `Stop-OldRunner`; `error` → **не стартовать, живого не трогать** (спека §4 правило 7), записать `last_error`.
7. `Write-ClientState` — атомарная запись (`tmp` + `Move-Item -Force`), `state` ∈ `alive|starting|down|stopped|invalid`.
8. Главный цикл: `Write-GuardianBeat` → `Invoke-Converge` → `Write-ClientState` → `Start-Sleep`.

- [ ] **Step 4: Прогнать весь файл тестов**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_chatter_guardian_multiclient.py -q`
Expected: PASS (9 тестов)

- [ ] **Step 5: Коммит**

```bash
git add scripts/chatter_guardian_detached.ps1 tests/test_chatter_guardian_multiclient.py
git commit -F <msg-file>   # feat(chatter): супервизор N клиентов — конвергенция + chatter_clients.json
```

---

## Task 7: Старт/стоп отдельного клиента

**Files:**
- Create: `scripts/chatter_client.ps1`
- Test: `tests/test_chatter_guardian_multiclient.py`

- [ ] **Step 1: Написать падающие тесты**

```python
def test_stop_action_flips_enabled_in_registry_only(tmp_path):
    """Скрипт НЕ убивает процессы напрямую: иначе появилась бы вторая ручка
    управления, конкурирующая с супервизором, и наблюдаемое состояние
    разошлось бы с желаемым."""
    _registry(tmp_path, "clients:\n  aaa:\n    enabled: true\n")
    p = _fake_runner(tmp_path, "aaa")
    try:
        time.sleep(1.0)
        script = REPO_ROOT / "scripts" / "chatter_client.ps1"
        res = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
             "Bypass", "-File", str(script), "-Root", str(tmp_path),
             "-Slug", "aaa", "-Action", "stop"],
            capture_output=True, text=True, timeout=40, encoding="utf-8", errors="replace")
        assert res.returncode == 0, res.stderr
        text = (tmp_path / "chatter" / "clients" / "registry.yaml").read_text("utf-8")
        assert "enabled: false" in text
        assert p.poll() is None, "chatter_client.ps1 не должен убивать процесс сам"
    finally:
        if p.poll() is None:
            p.kill()


def test_start_action_flips_enabled_true(tmp_path):
    _registry(tmp_path, "clients:\n  aaa:\n    enabled: false\n")
    script = REPO_ROOT / "scripts" / "chatter_client.ps1"
    res = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
         "Bypass", "-File", str(script), "-Root", str(tmp_path),
         "-Slug", "aaa", "-Action", "start"],
        capture_output=True, text=True, timeout=40, encoding="utf-8", errors="replace")
    assert res.returncode == 0, res.stderr
    assert "enabled: true" in (tmp_path / "chatter" / "clients" / "registry.yaml").read_text("utf-8")


def test_unknown_slug_fails_loudly(tmp_path):
    _registry(tmp_path, "clients:\n  aaa:\n    enabled: true\n")
    script = REPO_ROOT / "scripts" / "chatter_client.ps1"
    res = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
         "Bypass", "-File", str(script), "-Root", str(tmp_path),
         "-Slug", "ghost", "-Action", "stop"],
        capture_output=True, text=True, timeout=40, encoding="utf-8", errors="replace")
    assert res.returncode != 0
    assert "ghost" in (res.stdout + res.stderr)
```

- [ ] **Step 2: Прогнать — упадёт** (`chatter_client.ps1` не существует)

Run: `./.venv/Scripts/python.exe -m pytest tests/test_chatter_guardian_multiclient.py -q -k "action or unknown_slug"`
Expected: FAIL

- [ ] **Step 3: Реализовать `scripts/chatter_client.ps1`**

Параметры `-Slug`, `-Action start|stop|status|list`, `-Root 'C:\jarvis'`.
`start`/`stop` — точечная правка строки `enabled:` внутри блока клиента
(текстом, как `/funnel_gate` правит `settings.yaml`, чтобы комментарии-инструкции
в реестре выжили). `status`/`list` — читают `state/chatter_clients.json`.
Неизвестный slug → ненулевой код возврата и внятное сообщение.

- [ ] **Step 4: Прогнать**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_chatter_guardian_multiclient.py -q`
Expected: PASS (12 тестов)

- [ ] **Step 5: Коммит**

```bash
git add scripts/chatter_client.ps1 tests/test_chatter_guardian_multiclient.py
git commit -F <msg-file>   # feat(chatter): chatter_client.ps1 — старт/стоп клиента через реестр
```

---

## Task 8: Документация

**Files:**
- Modify: `docs/chatter/ONBOARDING_MANUAL.md` §4, `chatter/clients/active.yaml`

- [ ] **Step 1: Переписать §4 ONBOARDING_MANUAL**

Убрать «Текущее ограничение / Путь A / Путь B» — ограничения больше нет.
Новый §4: запись в `registry.yaml` (две строки для минимального клиента),
`chatter_client.ps1 -Action start`, проверка живости через
`state/chatter_clients.json`, предупреждение про конфликт session/db.

- [ ] **Step 2: Пометить `active.yaml`**

Добавить в шапку: `# ⚠️ Гардианом НЕ используется — состав клиентов в registry.yaml.`
`# Этот файл остаётся для ручного запуска: python -m chatter.telethon_run`

- [ ] **Step 3: Полный прогон**

Run: `PYTHONUTF8=1 ./.venv/Scripts/python.exe -m pytest tests/chatter tests/test_chatter_guardian_multiclient.py tests/test_chatter_watch_check.py -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 4: Коммит**

```bash
git add docs/chatter/ONBOARDING_MANUAL.md chatter/clients/active.yaml
git commit -F <msg-file>   # docs(chatter): онбординг через реестр, ограничение «один аккаунт» снято
```

---

## Task 9: 🔴 Миграция volska (ЖИВАЯ — только после утренних дрилов)

**Предусловия:** утренние дрилы (память + Д-7) пройдены; владелец дал команду; Задачи 1–8 сделаны и зелёные в ветке арки.

⚠️ Мерж в `phase-4.0` НЕ единой операцией: шаг 2 вносит только `registry.yaml`,
остальное — шагом 5. Порядок обоснован в спеке §9.2 (окно, где новый супервизор
живой, а источника правды ещё нет).

- [ ] **Step 1: Снять baseline фактом и ЗАПИСАТЬ PRE_MERGE_SHA**

```powershell
Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
  Where-Object { $_.CommandLine -match 'telethon_run' } |
  Select-Object ProcessId, CreationDate, CommandLine
Get-Content C:\jarvis\state\chatter_heartbeat.txt
git -C C:\jarvis rev-parse HEAD    # <PRE_MERGE_SHA> — выписать в отчёт СЕЙЧАС
```

`<PRE_MERGE_SHA>` записывается ДО любых изменений: искать нужный коммит в
истории под давлением отката нельзя.

- [ ] **Step 2: Положить registry.yaml в прод ОТДЕЛЬНЫМ коммитом (без правок скрипта)**

Смержить в `phase-4.0` только `chatter/clients/registry.yaml`. Старый скрипт
гардиана реестра не читает вовсе — поведение прода не меняется ни на йоту.

- [ ] **Step 3: Проверить реестр CLI на РЕАЛЬНЫХ путях**

Run: `python -m chatter.registry_cli --root C:\jarvis`
Expected: `volska` → `"runnable": true`, `"error": null`; `demo` → `"desired": "disabled"`; `"fatal": null`

🔴 Если `runnable=false` — СТОП. Откатывать нечего, миграция не начиналась.

- [ ] **Step 4: Репетиция конфликта (живой тест правила §4)**

```powershell
.\scripts\chatter_client.ps1 -Slug demo -Action start
python -m chatter.registry_cli --root C:\jarvis
.\scripts\chatter_client.ps1 -Slug demo -Action stop
```

Expected: оба клиента `runnable=false`, `error` каждого называет второго; volska
жива и не тронута (тот же PID) — CLI ничего не запускает.

- [ ] **Step 5: Смержить остальную арку (скрипт + раннер + алертер)**

После этого на диске новый скрипт, но в памяти задачи — ещё старый.
⚠️ С этого момента срабатывание задачи гардиана поднимет НОВЫЙ супервизор
(спека §9.3, второй шов) — это безопасно только потому, что реализован
`Stop-LegacyRunners` (§9.1, Task 5).

- [ ] **Step 6: Рестарт задачи гардиана**

```powershell
schtasks /End /TN JarvisChatterGuardian
schtasks /Run /TN JarvisChatterGuardian
```

Ожидаемо в `logs/chatter_guardian.stdout.log`: строка про зачистку
legacy-раннера без `--client`, затем старт volska.

- [ ] **Step 7: Проверка фактом — пять критериев приёмки (спека §9.5)**

```powershell
Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
  Where-Object { $_.CommandLine -match 'telethon_run' } |
  Select-Object ProcessId, CreationDate, CommandLine
Get-Content C:\jarvis\state\chatter_clients.json
Get-Content C:\jarvis\logs\chatter_volska.log -Tail 30 -Encoding UTF8
Get-Content C:\jarvis\logs\chatter_volska.log -Encoding UTF8 |
  Select-String -Pattern 'ERROR|CRITICAL|Traceback'
python -c "import sqlite3;c=sqlite3.connect(r'C:\jarvis\.secrets\demo.db');print(c.execute(\"SELECT version FROM contact_profile WHERE contact_id='237616472:volska' ORDER BY version DESC LIMIT 1\").fetchone())"
```

Все пять критериев §9.5: один процесс volska с `--client` · `state=alive`,
`last_error=null` · `catch-up` есть и ноль `ERROR|CRITICAL|Traceback` ·
`honesty_mode: honest` + `funnel_gate: false` · профиль версии ≥ 5.
⚠️ Размер живого лога через `dir`/GCI не смотреть — NTFS врёт при открытом
write-хэндле.

Любой красный критерий → откат Step 9.

- [ ] **Step 8: Окно стабильности ≥ суток, ПОТОМ убрать семидемо**

Только после суток без инцидентов удалить `state/chatter_semidemo_volska.flag`
и `scripts/run_volska_semidemo.ps1`. Отдельным коммитом.

Раньше — нельзя: пока они на диске, откат стоит одну команду.

- [ ] **Step 9: Откат (если Step 7 красный) — проверенный, а не предполагаемый**

⚠️ `run_volska_semidemo.ps1 -Revert` для ЭТОГО отката НЕ подходит: его ветка
`-Revert` поднимает **Аню** (`active.yaml` = demo,demo2), а не volska. Нужен
запуск БЕЗ `-Revert` — он пересоздаёт флаг и перезапускает гардиан.

```powershell
git -C C:\jarvis reset --hard <PRE_MERGE_SHA>   # дерево = деплой, ~90с
C:\jarvis\scripts
un_volska_semidemo.ps1      # БЕЗ -Revert
Get-Content C:\jarvis\logs\chatter_volska.log -Tail 10 -Encoding UTF8
Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
  Where-Object { $_.CommandLine -match 'telethon_run' } | Select-Object ProcessId, CommandLine
```

Откат считается выполненным только после третьей команды: процесс с ожидаемой
командной строкой и свежая запись в логе. «Скрипт отработал без ошибки» —
не проверка.

Пины для ручного восстановления, если скрипт недоступен: `CHATTER_PERSONAS=volska`,
`TELETHON_SESSION=.secrets\demo.session`, `CHATTER_DB=.secrets\demo.db`,
`state\chatter_semidemo_volska.flag` (пустой файл, само наличие = сигнал).

---

## Self-Review (выполнен)

**Покрытие спеки:** §1.1 хардкод → Task 6 Step 3.1; §1.2 взаимное убийство → Task 5; §2 топология → Task 2+6; §3 реестр → Task 2; §3.1 сохранённое свойство → Task 8; §4 валидация (правила 1–6) → Task 1; §4 правило 7 → Task 6 (`test_running_client_is_not_killed_by_a_validation_error`); §9.1 мина легаси-раннера → Task 5 (`test_legacy_runner_without_client_is_swept`); §5 изоляция файлов → Task 3 + Task 6 `Get-ClientPaths`; §6 observed state → Task 6; §7 алерты → Task 4; §8 старт/стоп → Task 7; §9 миграция → Task 9; §11 тесты → распределены по задачам.

**Пробелов не найдено.** Плейсхолдеров нет. Имена согласованы across задач: `heartbeat_path_for`, `build_arg_parser`, `--client`, `Get-RunnerProcesses -Slug`, `Stop-OldRunner -Slug`, `Invoke-Converge`, `Write-ClientState`, `Get-ClientPaths`, `Get-RegistryPlan`, `build_plan`, `parse_registry`, `validate`, `normalize_path`, `ClientEntry`, `ClientIssue`, `RegistryError`.
