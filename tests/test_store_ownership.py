# -*- coding: utf-8 -*-
"""Сторожа арки «`Store` не переживает свой отказ» (спека 2026-08-28, §5).

Написаны ОТ СПЕКИ и ДО кода: автор сторожей реализации не видел. Поэтому
меряют они ИСХОД, а не текст — «файл сносится», «соединение закрыто», — и ни
один не спрашивает, как именно это сделано.

Два разных предмета в одном файле намеренно: спека §1.3 говорит, что это ОДИН
класс (у соединения нет владельца), и разносить сторожей по файлам значило бы
второй раз объяснить то же самое.
"""
from __future__ import annotations

import ast
import asyncio
import shutil
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from chatter.storage.db import PaymentsMigrationBlocked, Store  # noqa: E402


# ── фикстуры: база, на которой конструктор ГАРАНТИРОВАННО падает ──────────

def _legacy_payments_db(dir_path: Path, *, rows: int) -> Path:
    """База со СТАРОЙ схемой `payments` (без `dedup_key`).

    `rows=1` закрывает окно перестройки — конструктор обязан отказать штатно.
    `rows=0` окно оставляет открытым, и падение придётся организовать иначе.
    """
    dir_path.mkdir(parents=True, exist_ok=True)
    db = dir_path / "client.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE payments (id INTEGER PRIMARY KEY, amount REAL)")
    for i in range(rows):
        conn.execute("INSERT INTO payments (id, amount) VALUES (?, ?)", (i + 1, 1.0))
    conn.commit()
    conn.close()
    return db


def _tree_is_removable(dir_path: Path) -> bool:
    """Снести каталог БЕЗ `gc.collect()`.

    Сборщик здесь запрещён намеренно: он лечит следствие и на большой куче
    стоит паузы, а замер 22.08 показал, что именно с ним всё «работает».
    Сторож обязан требовать, чтобы работало и без него.
    """
    try:
        shutil.rmtree(dir_path)
    except OSError:
        return False
    return not dir_path.exists()


# ── §5.1-§5.4: конструктор, который упал, не имеет права запирать файл ────

def test_a_constructor_that_fails_mid_migration_releases_the_file(tmp_path):
    """Неожиданный сбой ПОСРЕДИ миграции не оставляет запертого файла.

    Окно перестройки открыто (0 строк), падение приходит из `_backup` — то
    есть уже после `connect` и после решения мигрировать.
    """
    work = tmp_path / "case1"
    db = _legacy_payments_db(work, rows=0)

    def _boom(self, path, *, tag="3a"):
        raise OSError("диск отвалился посреди бэкапа")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Store, "_backup", _boom, raising=True)
        with pytest.raises(OSError):
            Store(db)
        # excinfo (и его трассировка) ещё жив — ровно как у вызывающего,
        # который пишет traceback в лог. Именно в этом состоянии замер 22.08
        # показывал WinError 32.
        assert _tree_is_removable(work), (
            "каталог с базой не сносится: упавший конструктор оставил "
            "открытое соединение, и файл заперт")


def test_the_statutory_refusal_also_releases_the_file(tmp_path):
    """ШТАТНЫЙ отказ — не авария, и он тоже обязан отпускать файл.

    Отдельным сторожем от предыдущего намеренно: `_assert_payments_rebuild_window`
    бросает ПО ПЛАНУ (в payments есть строки, деньги втихую не конвертируем).
    Это нормальный путь, и если он запирает базу клиента, дефект приходит не
    из поломки, а из проектного решения.
    """
    work = tmp_path / "case2"
    db = _legacy_payments_db(work, rows=3)

    with pytest.raises(PaymentsMigrationBlocked):
        Store(db)

    assert _tree_is_removable(work), (
        "штатный отказ перестройки payments запер файл базы")


def test_the_original_exception_reaches_the_caller_unchanged(tmp_path):
    """Лечение НЕ глотает и НЕ подменяет исключение.

    Самый дешёвый способ сломать эту арку — закрыть соединение в `except` и
    забыть про голый `raise`. Снаружи это выглядит как «починили», а на деле
    отказ перестройки денег превращается в другую ошибку или в тишину.
    """
    work = tmp_path / "case3"
    db = _legacy_payments_db(work, rows=2)

    with pytest.raises(PaymentsMigrationBlocked) as excinfo:
        Store(db)

    assert "окно перестройки" in str(excinfo.value), (
        "сообщение штатного отказа изменилось: %s" % excinfo.value)
    assert excinfo.value.__class__ is PaymentsMigrationBlocked, (
        "тип исключения подменён на %s" % excinfo.value.__class__.__name__)


def test_keyboard_interrupt_mid_migration_releases_the_file(tmp_path):
    """`Ctrl+C` посреди миграции запирает файл ровно так же.

    Прерывают тут руками регулярно, а `KeyboardInterrupt` — не `Exception`.
    Сторож пинит именно это: ловить надо `BaseException`.
    """
    work = tmp_path / "case4"
    db = _legacy_payments_db(work, rows=0)

    def _interrupt(self, path, *, tag="3a"):
        raise KeyboardInterrupt

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Store, "_backup", _interrupt, raising=True)
        with pytest.raises(KeyboardInterrupt):
            Store(db)
        assert _tree_is_removable(work), (
            "прерывание с клавиатуры оставило файл базы запертым")


def test_a_successful_store_keeps_its_connection_open(tmp_path):
    """ВСТРЕЧНАЯ ПОЛОВИНА, без неё «закрывать всегда» прошло бы.

    Конструктор, закрывающий соединение и на успехе, сделал бы `Store`
    бесполезным — и все сторожа выше остались бы зелёными.
    """
    db = tmp_path / "fresh.db"
    store = Store(db)
    try:
        assert store._conn.execute("SELECT 1").fetchone()[0] == 1, (
            "успешно построенный Store отдал закрытое соединение")
    finally:
        store.close()


# ── §5.6: обе ручки панели закрывают соединение — по ИСХОДУ ───────────────

class _RecordingStore(Store):
    """Настоящий `Store`, который помнит, кого создали.

    Проверять будем не текст вызова, а состояние соединения ПОСЛЕ выхода из
    ручки: `with` там или `try/finally` — не наше дело.
    """

    created: list["_RecordingStore"] = []

    def __init__(self, path):
        super().__init__(path)
        _RecordingStore.created.append(self)


def _is_closed(store: Store) -> bool:
    try:
        store._conn.execute("SELECT 1")
    except sqlite3.ProgrammingError:
        return True
    return False


@pytest.fixture
def panel_with_recording_store(tmp_path, monkeypatch):
    """Панель, чей `Store` мы видим, а база — временная."""
    from app.routers import tamapi_dashboard

    _RecordingStore.created.clear()
    db = tmp_path / "panel.db"
    Store(db).close()          # создать схему заранее, чтобы ручка не мигрировала

    monkeypatch.setattr(tamapi_dashboard, "_db_path", lambda: db, raising=True)
    monkeypatch.setattr("chatter.storage.db.Store", _RecordingStore, raising=True)
    return tamapi_dashboard


def test_the_status_lamp_closes_its_connection(panel_with_recording_store, monkeypatch):
    """`_status()` — читающая ручка, зовётся на КАЖДЫЙ показ лампы."""
    tamapi_dashboard = panel_with_recording_store
    monkeypatch.setattr(tamapi_dashboard, "_heartbeat", lambda: (0.0, "тест"),
                        raising=True)

    tamapi_dashboard._status()

    assert _RecordingStore.created, "ручка не открыла ни одного Store — сторож слеп"
    leaked = [s for s in _RecordingStore.created if not _is_closed(s)]
    assert not leaked, (
        "_status() оставил %d открытых соединений; процесс панели живёт "
        "неделями и зовёт её на каждый показ" % len(leaked))


def test_the_action_handler_closes_its_connection(panel_with_recording_store,
                                                  monkeypatch):
    """`action()` — ЕДИНСТВЕННАЯ мутирующая ручка дашборда.

    `data="stop_all"` возвращает запрос подтверждения — но `Store` к этому
    моменту уже открыт. Путь раннего возврата и есть самый частый.
    """
    tamapi_dashboard = panel_with_recording_store
    monkeypatch.setattr(tamapi_dashboard, "_cfg", lambda: None, raising=True)

    asyncio.run(tamapi_dashboard.action(None, data="stop_all", event_token=None))

    assert _RecordingStore.created, "ручка не открыла ни одного Store — сторож слеп"
    leaked = [s for s in _RecordingStore.created if not _is_closed(s)]
    assert not leaked, (
        "action() оставил %d открытых соединений на пути раннего возврата"
        % len(leaked))


# ── §5.7-§5.8: сторож по AST и его собственная проверка ───────────────────

def bare_store_sites(root: Path) -> list[str]:
    """`Store(...)`, у которого нет владельца: не в `with` и не в `try/finally`.

    По AST, а не по подстроке, НАМЕРЕННО: в `app/panel_client.py` слово
    `Store(` стоит внутри комментария, и подстрочный сторож споткнулся бы о
    него, а разбор кода комментариев не видит вовсе.
    """
    found: list[str] = []
    for py in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(py.read_bytes())
        except SyntaxError:
            continue
        owned = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    owned.add(id(item.context_expr))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Name) and fn.id == "Store"):
                continue
            if id(node) in owned:
                continue
            found.append("%s:%d" % (py.relative_to(root.parent).as_posix(),
                                    node.lineno))
    return found


def _write(tmp_path: Path, name: str, src: str) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir(exist_ok=True)
    (pkg / name).write_bytes(src.encode("utf-8"))
    return pkg


def test_the_ast_check_reddens_on_a_bare_store(tmp_path):
    """Сторож §2.3 обязан ВИДЕТЬ голый вызов — иначе он украшение."""
    pkg = _write(tmp_path, "leaky.py", "def f():\n    store = Store(path())\n")
    assert bare_store_sites(pkg), "голый `Store(...)` не найден — проверка слепа"


def test_the_ast_check_is_blind_to_comments_and_strings(tmp_path):
    """Слепота к комментариям — часть требования, а не побочный эффект."""
    pkg = _write(tmp_path, "commented.py",
                 "# было так: store = Store(path())\n"
                 "TEXT = 'store = Store(path())'\n"
                 "def f():\n    with Store(path()) as s:\n        return s\n")
    assert bare_store_sites(pkg) == [], (
        "проверка сработала на комментарии или строке: %s"
        % bare_store_sites(pkg))


def test_the_ast_check_accepts_the_with_form(tmp_path):
    """Встречная половина: правильная форма НЕ краснеет."""
    pkg = _write(tmp_path, "owned.py",
                 "def f():\n    with Store(path()) as s:\n        return s.q()\n")
    assert bare_store_sites(pkg) == []


def test_the_scope_leaves_the_process_lifetime_form_alone():
    """ПИН ГРАНИЦЫ. `chatter/run.py` и `telethon_run.py` держат соединение
    срок процесса ЗАКОННО (спека §2.3). Сторож, потребовавший `with` и там,
    заставил бы переписать верную форму — поэтому скоуп только `app/`."""
    sites = bare_store_sites(_REPO_ROOT / "chatter")
    assert sites, (
        "в chatter/ не нашлось ни одного голого Store — значит скоуп больше "
        "не про что ограничивать, и пин границы устарел")


def test_no_bare_store_in_app():
    """НАСТОЯЩАЯ проверка: в `app/` владельца имеет каждое соединение."""
    sites = bare_store_sites(_REPO_ROOT / "app")
    assert sites == [], (
        "в app/ найден `Store(...)` без владельца: %s\n"
        "Процессы под app/ живут неделями; соединение без `with` не "
        "закрывается никогда." % ", ".join(sites))
