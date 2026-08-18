# -*- coding: utf-8 -*-
"""Тонкое приложение клиентской панели: то, чего в нём НЕТ, — и есть предмет.

Спека: docs/superpowers/specs/2026-08-18-yarina-client-panel-instance.md (ОК
владельца 19.08). Инстанс поднимается для стороннего человека, поэтому «второй
`app.main` с другим TAMAPI_DB» отдавать нельзя, и замер назвал ровно две
причины:

* `app/main.py:308-309` на старте вешает `_watchdog_loop()`, который каждые 60 с
  зовёт `restart_bot_if_dead()`. Второй инстанс = второй хозяин у бота, тот же
  класс, что стоил прода 18.08 (DEV-38) и 13 ч 42 мин простоя Ольги 16.08;
* `app/main.py:236-244` монтирует клиентский дашборд и `/panel/jarvis` ОДНИМ
  блоком, и `require_owner` у них на ОДНОМ ключе. Значит вместе с панелью
  клиента ключ открывает PID'ы, ветки и сроки ключей фермы.

Сторожа ниже держат обе дыры закрытыми ПО СОСТАВУ приложения, а не по
настройке: то, чего в приложении нет, нельзя открыть опечаткой в env.
"""
from __future__ import annotations

import importlib
import sys

import pytest
from fastapi.testclient import TestClient

from chatter.storage.db import Store

KEY = "yarina-panel-key"
OWNER_KEY = "owner-key-do-not-hand-out"
NOW = 1_800_000_000.0
DAY = 86400.0


@pytest.fixture()
def instance(tmp_path, monkeypatch):
    """Инстанс Ярины: своя БД, свой слаг, свой пульс, СВОЙ ключ."""
    db = tmp_path / "yarina.db"
    s = Store(str(db))
    s.get_or_create_contact("111:yarina")
    s.add_message("111:yarina", "user", "скільки коштує манікюр", ts=NOW - DAY)
    del s

    beat = tmp_path / "state" / "chatter_heartbeat_yarina.txt"
    beat.parent.mkdir(parents=True, exist_ok=True)
    beat.write_text("beat", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JARVIS_PANELS_KEY", KEY)
    monkeypatch.setenv("TAMAPI_DB", str(db))
    monkeypatch.setenv("TAMAPI_SLUG", "yarina")
    monkeypatch.delenv("TAMAPI_HEARTBEAT", raising=False)

    import app.routers.panels_auth as pa
    import app.routers.tamapi_dashboard as td
    for m in (pa, td):
        importlib.reload(m)
    import app.panel_client as pc
    importlib.reload(pc)
    return pc.build_app(), str(db)


def _paths(api) -> set[str]:
    return {r.path for r in api.routes}


def test_the_jarvis_panel_is_absent_from_the_instance(instance):
    """Панель фермы не имеет права быть в приложении, ключ от которого уходит
    наружу. Проверяется по СОСТАВУ маршрутов, а не глазами ревьюера."""
    api, _ = instance
    leaked = sorted(p for p in _paths(api) if p.startswith("/panel/jarvis"))
    assert not leaked, "инстанс клиента отдаёт панель Джарвиса: %s" % (leaked,)


def test_the_instance_starts_nothing_in_the_background(instance):
    """Ни одной фоновой задачи: второй `_watchdog_loop` — это второй рестартер
    живого бота."""
    api, _ = instance
    assert not api.router.on_startup, (
        "инстанс вешает startup-обработчики: %s" % (api.router.on_startup,))
    assert not api.router.on_shutdown, api.router.on_shutdown


def test_the_instance_does_not_drag_in_the_whole_backend(tmp_path, monkeypatch):
    """`app.main` не имеет права быть импортирован: он и есть носитель сорока
    роутеров и фоновых задач.

    Сборка ЗОВЁТСЯ настоящая: импорты живут ВНУТРИ `build_app`, и сторож,
    который только импортирует модуль, не исполнил бы ни одного из них — и был бы
    зелёным даже тогда, когда сборка тянет весь бэкенд."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JARVIS_PANELS_KEY", KEY)
    monkeypatch.setenv("TAMAPI_DB", str(tmp_path / "x.db"))
    monkeypatch.setenv("TAMAPI_SLUG", "yarina")
    sys.modules.pop("app.main", None)

    import app.panel_client as pc
    importlib.reload(pc)
    pc.build_app()

    assert "app.main" not in sys.modules, (
        "тонкое приложение притащило app.main со всеми его фоновыми задачами")


def test_the_dashboard_answers_with_its_own_key(instance):
    api, _ = instance
    c = TestClient(api)
    c.cookies.set("panels_key", KEY)
    r = c.get("/panel/tamapi")
    assert r.status_code == 200, r.text


def test_the_dashboard_is_closed_without_the_key(instance):
    api, _ = instance
    assert TestClient(api).get("/panel/tamapi").status_code == 401


def test_the_instance_shows_only_its_own_client(instance, tmp_path):
    """БД чужого клиента не имеет права протечь: инстанс существует ровно
    затем, чтобы сторонний человек видел ОДНОГО клиента."""
    api, _ = instance
    foreign = tmp_path / "volska.db"
    s = Store(str(foreign))
    s.get_or_create_contact("999:volska")
    s.add_message("999:volska", "user", "СЕКРЕТНА РЕПЛІКА ОЛЬГИ", ts=NOW - DAY)
    del s

    c = TestClient(api)
    c.cookies.set("panels_key", KEY)
    body = c.get("/panel/tamapi").text
    assert "СЕКРЕТНА РЕПЛІКА ОЛЬГИ" not in body


def test_the_owner_panel_is_not_fixed_by_deleting_it():
    """ГРАНИЦА. Дыру Ф2 можно «закрыть», сняв `/panel/jarvis` у владельца —
    и все сторожа выше позеленеют, а владелец останется без панели фермы.
    Поэтому: роутер панели Джарвиса обязан существовать и иметь маршруты."""
    from app.routers.jarvis_panel import router as jarvis_panel
    assert [r for r in jarvis_panel.routes], "панель Джарвиса исчезла целиком"


# ──────────────── окружение инстанса: дефолт = отказ ────────────────

def _problems(**env):
    from app.panel_client import instance_env_problems
    return instance_env_problems(env, owner_key=OWNER_KEY)


def test_missing_db_is_refused_because_the_default_is_someone_elses_client():
    """Самая дорогая опечатка: без TAMAPI_DB дашборд берёт
    `.secrets/demo.db` и слаг `volska` — то есть показывает ОЛЬГУ. Молча."""
    p = _problems(JARVIS_PANELS_KEY=KEY, TAMAPI_SLUG="yarina")
    assert any("TAMAPI_DB" in x for x in p), p


def test_missing_slug_is_refused():
    p = _problems(JARVIS_PANELS_KEY=KEY, TAMAPI_DB="x.db")
    assert any("TAMAPI_SLUG" in x for x in p), p


def test_the_owner_key_is_refused():
    """Ключ владельца на инстансе клиента — это выдача доступа к ферме.

    Ловушка не теоретическая: `.env` грузится с override=False, значит ЗАБЫТАЯ
    переменная процесса молча заменяется ключом владельца из файла."""
    p = _problems(JARVIS_PANELS_KEY=OWNER_KEY, TAMAPI_DB="x.db", TAMAPI_SLUG="yarina")
    assert any("владельца" in x for x in p), p


def test_no_key_at_all_is_refused():
    p = _problems(TAMAPI_DB="x.db", TAMAPI_SLUG="yarina")
    assert any("JARVIS_PANELS_KEY" in x for x in p), p


def test_a_correct_instance_env_passes():
    """ГРАНИЦА: проверка не имеет права запрещать всё подряд — иначе её снимут."""
    assert _problems(JARVIS_PANELS_KEY=KEY, TAMAPI_DB="x.db",
                     TAMAPI_SLUG="yarina") == []


# ──────────────── запускающий: пути из слага, ключ — свой ────────────────

def _launcher(tmp_path):
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "scripts"))
    import run_panel_client
    return run_panel_client


def test_paths_are_derived_from_the_slug_not_from_dashboard_defaults(tmp_path):
    """Дефолты дашборда — `volska` и `.secrets/demo.db`. Запускающий обязан
    задать всё явно, иначе инстанс Ярины покажет Ольгу."""
    rpc = _launcher(tmp_path)
    env = rpc.build_instance_env("yarina", {"JARVIS_PANELS_KEY_YARINA": KEY},
                                 root=tmp_path)
    assert env["TAMAPI_SLUG"] == "yarina"
    assert env["TAMAPI_DB"].endswith("yarina.db"), env["TAMAPI_DB"]
    assert "demo.db" not in env["TAMAPI_DB"]
    assert env["TAMAPI_HEARTBEAT"].endswith("chatter_heartbeat_yarina.txt")
    assert env["JARVIS_PANELS_KEY"] == KEY


def test_the_per_slug_key_variable_is_what_is_read(tmp_path):
    """Ключ инстанса берётся ИЗ СВОЕЙ переменной, а не из общей.

    Иначе он подхватит ключ владельца из `.env` и отдаст с панелью ферму."""
    rpc = _launcher(tmp_path)
    env = rpc.build_instance_env(
        "yarina", {"JARVIS_PANELS_KEY": OWNER_KEY}, root=tmp_path)
    assert env["JARVIS_PANELS_KEY"] == "", (
        "запускающий взял ключ владельца: %r" % (env["JARVIS_PANELS_KEY"],))


def test_owner_key_is_read_from_the_file_not_from_environ(tmp_path, monkeypatch):
    """Сверка обязана читать `.env`, а не `os.environ`.

    После `app.env_bootstrap` два значения сливаются в одно, и сверка стала
    бы сравнением значения с самим собой — всегда зелёной."""
    rpc = _launcher(tmp_path)
    (tmp_path / ".env").write_text(
        "SOMETHING=1" + chr(10) + "JARVIS_PANELS_KEY=" + OWNER_KEY + chr(10)
        + "OTHER=2" + chr(10), encoding="utf-8")
    monkeypatch.delenv("JARVIS_PANELS_KEY", raising=False)
    assert rpc.owner_key_from_env_file(root=tmp_path) == OWNER_KEY


def test_missing_env_file_does_not_crash_the_launcher(tmp_path):
    """Нет `.env` — не повод падать: сверка просто нечем занята."""
    rpc = _launcher(tmp_path)
    assert rpc.owner_key_from_env_file(root=tmp_path / "nope") == ""


# ──────────────── адрес бинда: НЕ на все интерфейсы ────────────────

def test_the_instance_never_binds_all_interfaces_by_default(tmp_path):
    """Замер: у основного бэкенда JARVIS_BACKEND_HOST=0.0.0.0, и взять его
    резолвер значило бы открыть панель клиента ВСЕЙ локальной сети — при том,
    что ключ от неё уходит стороннему человеку."""
    rpc = _launcher(tmp_path)
    host, problem = rpc.resolve_client_host(environ={}, ip="")
    assert problem is None, problem
    assert host == "127.0.0.1", host


def test_the_tailnet_address_wins_when_available(tmp_path):
    """Решение владельца — вариант A (Tailscale): по умолчанию слушаем ровно
    там, а не везде."""
    rpc = _launcher(tmp_path)
    host, problem = rpc.resolve_client_host(environ={}, ip="100.102.179.47")
    assert problem is None, problem
    assert host == "100.102.179.47"


def test_all_interfaces_is_refused_even_when_asked_explicitly(tmp_path):
    rpc = _launcher(tmp_path)
    host, problem = rpc.resolve_client_host("0.0.0.0", environ={}, ip="")
    assert host is None and problem, (host, problem)
    assert "0.0.0.0" in problem


def test_all_interfaces_stays_possible_when_named_out_loud(tmp_path):
    """ГРАНИЦА: запрет без выхода учит обходить сам инструмент. Осознанный
    флаг обязан работать."""
    rpc = _launcher(tmp_path)
    host, problem = rpc.resolve_client_host("0.0.0.0", environ={}, ip="",
                                            allow_any=True)
    assert problem is None and host == "0.0.0.0"


def test_the_backend_bind_setting_does_not_leak_in(tmp_path):
    """JARVIS_BACKEND_HOST — настройка ДРУГОГО процесса. Если она когда-нибудь
    начнёт влиять на панель клиента, это и будет тихое открытие наружу."""
    rpc = _launcher(tmp_path)
    host, problem = rpc.resolve_client_host(
        environ={"JARVIS_BACKEND_HOST": "0.0.0.0"}, ip="100.102.179.47")
    assert problem is None
    assert host == "100.102.179.47", host


def test_missing_tailscale_does_not_break_the_launch(tmp_path):
    """Нет tailscale — уходим на петлю, а не падаем и не открываемся наружу."""
    rpc = _launcher(tmp_path)
    assert rpc.tailnet_ip(exe=str(tmp_path / "no-such-tailscale.exe")) == ""


def test_the_tailscale_path_survived_being_written_to_a_file(tmp_path):
    r"""`TAILSCALE_EXE` обязан быть ПУТЁМ, а не строкой с проглоченным экраном.

    Замер (19.08): в файле по этому смещению лежит байт 0x09 — то есть `\t`
    из `...\Tailscale\tailscale.exe` был раскрыт в ТАБУЛЯЦИЮ ещё при записи
    файла, и raw-строка сохранила уже табуляцию. Путь стал
    `C:\Program Files\Tailscale<TAB>ailscale.exe`, `subprocess` на нём кидает,
    `tailnet_ip()` возвращает "" — и инстанс МОЛЧА уходит на петлю, то есть
    становится недоступен тому самому человеку, ради которого поднимается.

    Соседний сторож (`test_missing_tailscale_does_not_break_the_launch`) это
    пропустил ПО ПОСТРОЕНИЮ: он проверяет поведение при ОТСУТСТВУЮЩЕМ exe, а
    сломанная константа — ровно отсутствующий exe. Сторож на поведение обязан
    быть дополнен сторожем на САМУ КОНСТАНТУ.
    """
    from pathlib import Path as _Path
    rpc = _launcher(tmp_path)
    assert chr(9) not in rpc.TAILSCALE_EXE, (
        "в пути табуляция — экран `\t` был раскрыт при записи файла: %r"
        % (rpc.TAILSCALE_EXE,))
    assert _Path(rpc.TAILSCALE_EXE).name == "tailscale.exe", rpc.TAILSCALE_EXE


# ──────────────── ключ клиента: где он на самом деле лежит ────────────────

def test_the_client_key_is_found_when_it_lives_in_the_env_file(tmp_path):
    """Ключ клиента владелец держит в `.env` — и больше нигде.

    Запускающий читает окружение ДО `app.env_bootstrap` (иначе сверка с ключом
    владельца слепнет, см. соседний сторож), поэтому `.env` он обязан
    прочитать САМ. Без этого fail-closed отказывает при ВЕРНО настроенном
    ключе — отказ звучит как «ключ не задан», хотя он задан.
    """
    rpc = _launcher(tmp_path)
    (tmp_path / ".env").write_text(
        "JARVIS_PANELS_KEY=" + OWNER_KEY + chr(10)
        + "JARVIS_PANELS_KEY_YARINA=" + KEY + chr(10), encoding="utf-8")
    env = rpc.build_instance_env("yarina", {}, root=tmp_path)
    assert env["JARVIS_PANELS_KEY"] == KEY, (
        "ключ настроен в .env, но запускающий его не нашёл: %r"
        % (env["JARVIS_PANELS_KEY"],))


def test_the_process_variable_wins_over_the_env_file(tmp_path):
    """Переменная процесса — способ поднять инстанс НЕ трогая `.env`.
    Файл остаётся запасным источником, а не главным."""
    rpc = _launcher(tmp_path)
    (tmp_path / ".env").write_text(
        "JARVIS_PANELS_KEY_YARINA=key-from-file" + chr(10), encoding="utf-8")
    env = rpc.build_instance_env(
        "yarina", {"JARVIS_PANELS_KEY_YARINA": KEY}, root=tmp_path)
    assert env["JARVIS_PANELS_KEY"] == KEY, env["JARVIS_PANELS_KEY"]


def test_reading_the_env_file_does_not_open_a_door_to_the_owner_key(tmp_path):
    """Чтение файла обязано искать ТОЧНОЕ имя `JARVIS_PANELS_KEY_<SLUG>`.

    Ошибка на префиксе здесь означает ровно ту катастрофу, ради которой всё
    это писалось: ключ владельца уезжает стороннему человеку."""
    rpc = _launcher(tmp_path)
    (tmp_path / ".env").write_text(
        "JARVIS_PANELS_KEY=" + OWNER_KEY + chr(10), encoding="utf-8")
    env = rpc.build_instance_env("yarina", {}, root=tmp_path)
    assert env["JARVIS_PANELS_KEY"] == "", (
        "запускающий подобрал ключ владельца из файла: %r"
        % (env["JARVIS_PANELS_KEY"],))


def test_the_owner_key_is_still_refused_when_it_comes_from_the_file(tmp_path):
    """Fail-closed не должен ослабнуть от нового источника: если владелец
    вписал в `JARVIS_PANELS_KEY_<SLUG>` СВОЙ ключ, инстанс не поднимается."""
    import app.panel_client as pc
    rpc = _launcher(tmp_path)
    (tmp_path / ".env").write_text(
        "JARVIS_PANELS_KEY=" + OWNER_KEY + chr(10)
        + "JARVIS_PANELS_KEY_YARINA=" + OWNER_KEY + chr(10), encoding="utf-8")
    env = rpc.build_instance_env("yarina", {}, root=tmp_path)
    problems = pc.instance_env_problems(env, owner_key=OWNER_KEY)
    assert problems, "ключ владельца из файла проехал молча"
    assert any("владельца" in p for p in problems), problems
