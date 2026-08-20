# -*- coding: utf-8 -*-
"""Ссылка «Ферма →» на инстансе клиента: тупик и утечка словом.

Спека `docs/superpowers/specs/2026-08-20-farm-link-off-client-instance.md`,
вариант **Б**: ссылка рисуется тогда и только тогда, когда маршрут
`/panel/jarvis` смонтирован В ЭТОМ САМОМ приложении.

Улику дала панель, а не рассуждение: в `logs/panel_yarina.stdout.log` за 20.08
две записи `GET /panel/jarvis HTTP/1.1" 404 Not Found` — клиент ткнул дважды.

Почему сторожа новые, хотя старый на связку панелей был и был зелёным.
`tests/chatter/test_panels_web.py` собирает `FastAPI` руками и включает ОБА
роутера, `tamapi` и `jarvis`. На таком приложении ссылка ведёт не в тупик, и
покраснеть тест не мог НИ ПРИ КАКОМ состоянии кода: он проверял вёрстку
страницы, а дефект живёт в СОСТАВЕ приложения, которое её отдаёт. Поэтому
здесь страница рендерится ТОЛЬКО через `app.panel_client.build_app()` —
сборка руками и есть то, что скрыло дефект.

Сторожа написаны ОТ СПЕКИ (§3), до кода. Главный — Д2: он ловит СЛЕДУЮЩУЮ
такую ссылку, а не только эту.
"""
from __future__ import annotations

import importlib
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatter.storage.db import Store

KEY = "farm-link-guard-key"
NOW = 1_800_000_000.0
DAY = 86400.0

# Ссылка и слово проверяются РАЗДЕЛЬНО: текст могут оставить без ссылки, и
# тогда 404 уйдёт, а рассказ клиенту про ферму других клиентов останется.
FARM_HREF = "href='/panel/jarvis'"
FARM_WORD = "Ферма"
NEIGHBOUR_HREF = "href='/panel/tamapi/dynamics'"


def _seed(db_path: str, slug: str) -> None:
    s = Store(db_path)
    s.get_or_create_contact("111:%s" % slug)
    s.add_message("111:%s" % slug, "user", "скільки коштує манікюр", ts=NOW - DAY)
    del s


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Окружение инстанса. ОДНО на оба приложения — см. Д5."""
    db = tmp_path / "yarina.db"
    _seed(str(db), "yarina")
    beat = tmp_path / "state" / "chatter_heartbeat_yarina.txt"
    beat.parent.mkdir(parents=True, exist_ok=True)
    beat.write_text("beat", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JARVIS_PANELS_KEY", KEY)
    monkeypatch.setenv("TAMAPI_DB", str(db))
    monkeypatch.setenv("TAMAPI_SLUG", "yarina")
    monkeypatch.delenv("TAMAPI_HEARTBEAT", raising=False)

    import app.routers.jarvis_panel as jp
    import app.routers.panels_auth as pa
    import app.routers.tamapi_dashboard as td
    for m in (pa, td, jp):
        importlib.reload(m)
    return pa, td, jp


@pytest.fixture()
def client_app(env):
    """Приложение КЛИЕНТА — настоящая сборка, а не подобие."""
    import app.panel_client as pc
    importlib.reload(pc)
    return pc.build_app()


@pytest.fixture()
def owner_app(env):
    """Приложение ВЛАДЕЛЬЦА: то же самое, но ферма в нём смонтирована."""
    pa, td, jp = env
    api = FastAPI()
    api.include_router(pa.router)
    api.include_router(td.router)
    api.include_router(jp.router)
    pa.install_panel_auth_redirect(api)
    return api


def _open(api) -> TestClient:
    c = TestClient(api)
    c.cookies.set("panels_key", KEY)
    return c


def _html_get_paths(api) -> list[str]:
    """Все GET-страницы панели без параметров пути.

    Список берётся у САМОГО приложения, а не выписан руками: выписанный
    промолчит ровно на той странице, которую завтра добавят.
    """
    out: list[str] = []
    for r in api.routes:
        path = getattr(r, "path", "")
        methods = getattr(r, "methods", None) or set()
        if "GET" not in methods or "{" in path:
            continue
        if path == "/panel" or path.startswith("/panel/"):
            out.append(path)
    return sorted(set(out))


def _hrefs(html: str) -> list[str]:
    return (re.findall(r"href='([^']*)'", html)
            + re.findall(r'href="([^"]*)"', html))


# ── Д1: известный случай ────────────────────────────────────────────────────

def test_d1_client_screen_has_no_link_to_the_farm(client_app):
    c = _open(client_app)
    r = c.get("/panel/tamapi")
    assert r.status_code == 200
    assert FARM_HREF not in r.text, (
        "экран клиента ведёт на /panel/jarvis, которого в этом приложении нет "
        "ПО ЗАМЫСЛУ: клиент упирается в 404")


def test_d1_client_screen_does_not_say_the_word_farm(client_app):
    """Отдельным ассертом: 404 читается как поломка, а слово читается ВЕРНО —
    клиент узнаёт, что за его ботом стоит ферма других клиентов."""
    c = _open(client_app)
    r = c.get("/panel/tamapi")
    assert r.status_code == 200
    assert FARM_WORD not in r.text, (
        "экран клиента рассказывает про ферму словом, даже если ссылку сняли")


# ── Д2: класс, а не случай ──────────────────────────────────────────────────

def test_d2_no_internal_link_of_the_client_app_leads_to_a_dead_end(client_app):
    """Каждая внутренняя ссылка КАЖДОЙ страницы клиентского приложения обязана
    вести в маршрут ЭТОГО приложения.

    Это главный сторож: он ловит следующую такую ссылку, а не только ту, из-за
    которой заведён. `scripts/mutate_panels_no_dead_end.py` этого не ловил —
    «тупик» зависит от того, КАКОЕ приложение отдаёт страницу.
    """
    c = _open(client_app)
    checked: list[tuple[str, str, int]] = []
    for page in _html_get_paths(client_app):
        r = c.get(page)
        if (r.status_code != 200
                or "html" not in r.headers.get("content-type", "")):
            continue
        for href in _hrefs(r.text):
            if not href or href.startswith(("#", "http://", "https://",
                                            "mailto:", "javascript:")):
                continue
            target = urlsplit(urljoin("http://t" + page, href)).path
            checked.append((page, target, c.get(target).status_code))
    assert checked, "на страницах клиента не нашлось ни одной ссылки — сторож слеп"
    dead = [x for x in checked if x[2] == 404]
    assert not dead, "тупиковые ссылки на панели клиента: %s" % (dead,)


# ── Д3: парный, в обратную сторону ──────────────────────────────────────────

def test_d3_the_farm_link_stays_where_the_farm_is_mounted(owner_app):
    """Иначе правка «убрать везде» прошла бы как зелёная."""
    c = _open(owner_app)
    r = c.get("/panel/tamapi")
    assert r.status_code == 200
    assert FARM_HREF in r.text, "у владельца ссылка на ферму пропала"
    assert FARM_WORD in r.text
    assert c.get("/panel/jarvis").status_code == 200, (
        "ссылка у владельца ведёт в 404 — тупик просто переехал")


# ── Д4: сосед не тронут ─────────────────────────────────────────────────────

@pytest.mark.parametrize("which", ["client", "owner"])
def test_d4_the_dynamics_link_is_alive_on_both_apps(which, client_app, owner_app):
    """`/panel/tamapi/dynamics` есть в ОБОИХ приложениях и отвечает 200 —
    правка не имеет права задеть соседнюю ссылку."""
    api = client_app if which == "client" else owner_app
    c = _open(api)
    r = c.get("/panel/tamapi")
    assert r.status_code == 200
    assert NEIGHBOUR_HREF in r.text, "«Динаміка →» пропала у %s" % which
    assert c.get("/panel/tamapi/dynamics").status_code == 200


# ── Д5: признак берётся из состава приложения, а не из env ──────────────────

def test_d5_the_same_environment_gives_different_screens(client_app, owner_app):
    """Оба приложения рендерятся в ОДНОМ И ТОМ ЖЕ окружении процесса (фикстура
    `env` у них общая, между рендерами не меняется НИЧЕГО), а экраны обязаны
    отличаться. Решение, взятое из переменной окружения, здесь дало бы
    ОДИНАКОВЫЙ ответ — вариант А спеки этот сторож не переживает.
    """
    client_html = _open(client_app).get("/panel/tamapi").text
    owner_html = _open(owner_app).get("/panel/tamapi").text
    assert FARM_HREF not in client_html
    assert FARM_HREF in owner_html


def test_d5_the_decision_asks_the_application_that_serves_the_page():
    """Статическая половина: признак обязан читаться из состава приложения.

    Поведение выше проходит и у реализации, которая угадала состав по слагу или
    по порту; такая угадайка разъедется с составом в первый же день, когда
    маршрут добавят или уберут.
    """
    src = (Path(__file__).resolve().parents[1]
           / "app" / "routers" / "tamapi_dashboard.py")
    assert "app.routes" in src.read_text(encoding="utf-8"), (
        "решение о ссылке не спрашивает у приложения его собственный состав")
