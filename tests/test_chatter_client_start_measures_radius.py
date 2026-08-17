# -*- coding: utf-8 -*-
"""Подъём клиента обязан СНАЧАЛА замерить, кого переответит catch-up.

Правило родилось 17.08 из живого случая: рестарт Ярины заставил catch-up
ответить на сообщение 13 ч 49 мин давности в эскалированном диалоге — поверх
человека, который его вёл. Владелец потребовал, чтобы правило жило в СКРИПТЕ,
а не в знании: «пока оно живёт как знание, его забудут».

Сторожа гоняют настоящий `chatter_client.ps1` с временным -Root: реестр и БД
собираются в tmp, живой C:\\jarvis не участвует.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="PowerShell-скрипт")

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "chatter_client.ps1"
PYTHON = sys.executable

REGISTRY = """\
clients:
  demoklient:
    enabled: false
    session: .secrets/demoklient.session
    db: .secrets/demoklient.db
"""


def _root(tmp_path: Path, rows) -> Path:
    (tmp_path / "chatter" / "clients").mkdir(parents=True)
    (tmp_path / "chatter" / "clients" / "registry.yaml").write_text(REGISTRY, encoding="utf-8")
    (tmp_path / ".secrets").mkdir()
    if rows is not None:
        db = tmp_path / ".secrets" / "demoklient.db"
        con = sqlite3.connect(db)
        con.execute("create table messages (id integer primary key, contact_id text, "
                    "role text, text text, ts real)")
        # Схема повторяет живую: `pause_until` здесь не украшение — снуз
        # («⏸ Ще 1год») живёт именно в нём, и замер обязан спрашивать про него
        # ту же функцию, что раннер.
        con.execute("create table contacts (contact_id text primary key, state text, "
                    "paused integer, human_took_over integer, pause_until real)")
        now = time.time()
        for contact_id, role, back, text, state in rows:
            con.execute("insert into messages (contact_id, role, text, ts) values (?,?,?,?)",
                        (contact_id, role, text, now - back))
            con.execute("insert or replace into contacts values (?,?,0,0,null)",
                        (contact_id, state))
        con.commit()
        con.close()
    return tmp_path


def _run(root: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(SCRIPT), "-Root", str(root), "-PythonExe", PYTHON, *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)


def _enabled(root: Path) -> str:
    return (root / "chatter" / "clients" / "registry.yaml").read_text(encoding="utf-8")


def test_start_refuses_when_catchup_would_answer_over_a_human(tmp_path):
    """Ловит: подъём вслепую поверх диалога, который ведёт человек."""
    root = _root(tmp_path, [("42:demoklient", "user", 3600, "Покажіть договір", "escalated")])

    run = _run(root, "-Slug", "demoklient", "-Action", "start")

    assert run.returncode == 1, f"подъём разрешён при живом радиусе: {run.stdout}"
    assert "enabled: false" in _enabled(root), (
        "желаемое состояние уже переписано — супервизор поднимет клиента "
        "несмотря на отказ")
    assert "-Force" in run.stdout, (
        "отказ не назвал выход: запрет без выхода учит обходить сам инструмент")


def test_start_goes_through_when_there_is_nothing_to_re_answer(tmp_path):
    """Парная: замер, который запрещает ВСЁ, будет обойден в первый же вечер."""
    root = _root(tmp_path, [("42:demoklient", "assistant", 3600, "5 000 грн", "active")])

    run = _run(root, "-Slug", "demoklient", "-Action", "start")

    assert run.returncode == 0, run.stdout
    assert "enabled: true" in _enabled(root), run.stdout


def test_force_lifts_the_refusal_and_says_so(tmp_path):
    """Ловит: тихий обход. -Force обязан оставлять след в выводе."""
    root = _root(tmp_path, [("42:demoklient", "user", 60, "алло?", "escalated")])

    run = _run(root, "-Slug", "demoklient", "-Action", "start", "-Force")

    assert run.returncode == 0, run.stdout
    assert "enabled: true" in _enabled(root)
    assert "-Force" in run.stdout or "Force" in run.stdout, run.stdout


def test_a_measurement_that_cannot_run_stops_the_start(tmp_path):
    """Ловит: «не смогли посмотреть», прочитанное как «чисто».

    Битая БД — не разрешение. Это ровно тот случай, когда молчание
    инструмента дороже всего: человек читает пустоту как зелёный свет.
    """
    root = _root(tmp_path, None)
    (root / ".secrets" / "demoklient.db").write_bytes(b"\x00\x01 not a database")

    run = _run(root, "-Slug", "demoklient", "-Action", "start")

    assert run.returncode == 1, run.stdout
    assert "enabled: false" in _enabled(root), "подъём состоялся на несостоявшемся замере"


def test_a_client_that_never_ran_is_not_blocked(tmp_path):
    """Парная к предыдущей: у нового клиента БД ещё нет, и это ЗАКОННОЕ чисто.

    Иначе первый же онбординг упрётся в замер, который охраняет историю,
    которой не существует.
    """
    root = _root(tmp_path, None)

    run = _run(root, "-Slug", "demoklient", "-Action", "start")

    assert run.returncode == 0, run.stdout
    assert "enabled: true" in _enabled(root), run.stdout


def test_stop_does_not_measure_anything(tmp_path):
    """Ловит: замер, повешенный не на ту дверь.

    Опасен ПОДЪЁМ — catch-up работает на старте. Остановка не переотвечает
    никого, и запрет на неё означал бы, что клиента нельзя выключить, пока
    кто-то ждёт ответа. Это ровно наоборот тому, что нужно.
    """
    root = _root(tmp_path, [("42:demoklient", "user", 600, "алло?", "escalated")])

    run = _run(root, "-Slug", "demoklient", "-Action", "stop")

    assert run.returncode == 0, run.stdout
    assert "enabled: false" in _enabled(root)
