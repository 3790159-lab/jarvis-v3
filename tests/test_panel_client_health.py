# -*- coding: utf-8 -*-
"""`/health` на клиентской панели и её СОСТАВ — С3 и С4 спеки.

Спека: docs/superpowers/specs/2026-08-20-client-panel-supervisor.md, §3
вариант B (одобрен владельцем): отдельная НЕаутентифицированная ручка вместо
пробы по 401. Причина отказа от 401 названа там же: проба садилась бы на
политику авторизации и покраснела бы на здоровой панели ровно в тот день,
когда редирект поменяют.

Сторожа написаны ОТ СПЕКИ, планового кода автор не видел.

Ключ от этого приложения уходит СТОРОННЕМУ человеку, поэтому у новой ручки
две обязанности сразу, и вторая важнее первой:

1. отвечать 200 без ключа — иначе watchdog её не померит;
2. не сказать при этом НИ СЛОВА о клиенте. Ответ читает кто угодно, кто
   дотянулся до порта в тайнете.

Стенд поднят со слагом `yarina` и настоящим конфигом Ярины НАМЕРЕННО: сторож
на утечку, поднятый на пустом стенде, зелен по построению — течь просто
нечему.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chatter.storage.db import Store

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENTS_DIR = REPO_ROOT / "chatter" / "clients"

KEY = "yarina-panel-key"
SLUG = "yarina"
# Имя из настоящего конфига клиента (`chatter/clients/yarina/settings.yaml`):
# ровно та строка, которая живёт в шапке дашборда и потекла бы первой.
PERSONA = "Ярина"
NOW = 1_800_000_000.0
DAY = 86400.0


@pytest.fixture()
def instance(tmp_path, monkeypatch):
    """Инстанс Ярины: своя БД, свой слаг, свой ключ, НАСТОЯЩИЙ конфиг."""
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
    monkeypatch.setenv("TAMAPI_SLUG", SLUG)
    monkeypatch.setenv("CHATTER_CLIENTS_DIR", str(CLIENTS_DIR))
    monkeypatch.delenv("TAMAPI_HEARTBEAT", raising=False)

    import app.routers.panels_auth as pa
    import app.routers.tamapi_dashboard as td
    for m in (pa, td):
        importlib.reload(m)
    import app.panel_client as pc
    importlib.reload(pc)
    return pc.build_app(), str(db)


# ---------------- предпосылка стенда: течь ЕСТЬ чему ----------------

def test_the_stand_really_carries_the_clients_identity(instance):
    """ПРЕДПОСЫЛКА, без которой С3 зелен по построению.

    Если на этом стенде имя клиента и его слаг нигде не появляются, то
    сторож на утечку ниже не доказывает ничего. Поэтому сначала показываем,
    что дашборд их ПОКАЗЫВАЕТ — а уже потом требуем, чтобы `/health` молчал.
    """
    api, db = instance
    c = TestClient(api)
    c.cookies.set("panels_key", KEY)
    body = c.get("/panel/tamapi").text
    assert PERSONA in body, (
        "стенд не показывает имя клиента — сторож на утечку был бы зелёным "
        "просто потому, что течь нечему")
    assert SLUG in body, "стенд не показывает слаг клиента"
    assert db.endswith("yarina.db"), db


# ---------------- С3: 200 без ключа и ни слова о клиенте ----------------

def test_health_answers_without_any_key(instance):
    """Вариант B: watchdog ходит без ключа и без знания о политике входа.

    Ключей у watchdog'а нет и не будет — он stdlib-only и обязан работать
    ровно тогда, когда всё остальное умерло."""
    api, _ = instance
    r = TestClient(api).get("/health")
    assert r.status_code == 200, (r.status_code, r.text)


def test_health_is_not_a_redirect_to_the_login_form(instance):
    """`install_panel_auth_redirect` отдаёт браузеру форму входа. Если
    `/health` попадёт под тот же обработчик, проба увидит 200 от СТРАНИЦЫ
    ЛОГИНА — то есть позеленеет на панели, которая сама по себе мертва."""
    api, _ = instance
    r = TestClient(api).get("/health", follow_redirects=False)
    assert r.status_code == 200, (r.status_code, r.headers.get("location"))
    assert "location" not in {k.lower() for k in r.headers}, dict(r.headers)
    assert "<form" not in r.text.lower(), r.text[:400]


def test_health_says_exactly_one_thing(instance):
    """Спека §3B: `200 {"ok": true}` — и больше ничего.

    Сверка ТОЧНЫМ равенством, а не «есть ключ ok»: любое поле, добавленное
    «для удобства диагностики» (слаг, путь к БД, версия, PID), уедет
    стороннему человеку, и заметить это будет некому."""
    api, _ = instance
    assert TestClient(api).get("/health").json() == {"ok": True}


@pytest.mark.parametrize("secret", [SLUG, PERSONA, "yarina.db", ".secrets"])
def test_health_leaks_nothing_about_the_client(instance, secret):
    """Ни слага, ни имени клиента, ни пути к базе — дословно по спеке.

    Разбито параметрами намеренно: одно упавшее имя обязано называть себя, а
    не тонуть в общем «что-то в теле не то»."""
    api, _ = instance
    body = TestClient(api).get("/health").text
    assert secret.lower() not in body.lower(), (
        "`/health` отдал наружу «%s»: %r" % (secret, body[:400]))


def _strings(node):
    """Все строки разобранного JSON — и ключи, и значения, на любой глубине.

    Разобранный, а не сырой текст: JSON УДВАИВАЕТ обратную косую, и путь
    `C:\\Users\\...\\yarina.db` лежит в теле как `C:\\\\Users\\\\...`. Сравнение
    сырого пути с сырым телом промахивается по построению."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _strings(v)


def test_health_does_not_leak_the_database_path(instance):
    r"""Путь к базе — целиком и во ВСЕХ написаниях, в каких он может утечь.

    \U0001F534 ЭТОТ СТОРОЖ БЫЛ ЗЕЛЁН ПО ПОСТРОЕНИЮ И НЕ ОХРАНЯЛ НИЧЕГО.
    Он сравнивал сырой путь с сырым текстом ответа и путь со слэшами вперёд —
    с ним же. Тело ответа это JSON, а JSON удваивает обратную косую:

        db   = C:\Users\...\yarina.db
        body = {"ok": true, "db": "C:\\Users\\...\\yarina.db"}

    Ни одно из двух утверждений не могло совпасть НИКОГДА: первое — потому
    что написания разные, второе — потому что слэшей вперёд в `TAMAPI_DB` на
    Windows не бывает вовсе. Замерено фактом: мутация «`/health` отдаёт
    `os.environ["TAMAPI_DB"]`» оставила сторож зелёным, утечку поймали только
    соседи.

    Починка: сравнивать по РАЗОБРАННОМУ JSON (там путь лежит в исходном
    написании) И по сырому тексту (там — в экранированном), и перебирать
    написания явным списком, включая имя файла базы: `.../yarina.db` можно
    отдать и одним хвостом.
    """
    api, db = instance

    assert chr(92) in db, (
        "стенд поднят с путём без обратных косых — сторож охранял бы форму, "
        "которой в TAMAPI_DB на Windows не бывает: %r" % (db,))

    r = TestClient(api).get("/health")
    raw = r.text
    try:
        parsed = r.json()
    except ValueError:
        parsed = None

    spellings = {
        "сырой путь": db,
        "с экранированными косыми (как в JSON)": db.replace(chr(92), chr(92) * 2),
        "со слэшами вперёд": db.replace(chr(92), "/"),
        "имя файла базы": Path(db).name,
    }

    values = list(_strings(parsed)) if parsed is not None else []
    for what, needle in spellings.items():
        for value in values:
            assert needle.lower() not in value.lower(), (
                "`/health` отдал путь к базе (%s) в поле ответа: %r"
                % (what, value[:200]))
        assert needle.lower() not in raw.lower(), (
            "`/health` отдал путь к базе (%s) в теле: %r" % (what, raw[:400]))


def test_the_dashboard_is_still_closed_without_the_key(instance):
    """ГРАНИЦА. `/health` можно «сделать», сняв авторизацию со всего
    приложения — и все сторожа выше позеленеют, отдав переписку клиента
    любому, кто дотянулся до порта."""
    api, _ = instance
    assert TestClient(api).get("/panel/tamapi").status_code == 401


def test_the_dashboard_still_answers_with_the_key(instance):
    """Парная граница: авторизация не имеет права сломаться от новой ручки."""
    api, _ = instance
    c = TestClient(api)
    c.cookies.set("panels_key", KEY)
    assert c.get("/panel/tamapi").status_code == 200


# ---------------- С4: состав приложения — ЛИТЕРАЛЬНЫМ списком ----------------

# Написан РУКАМИ: восемь маршрутов, которые приложение отдавало ДО этой арки
# (замер снят с транка), плюс ровно один новый. Выведенный из приложения
# список согласен с ним по определению и промолчал бы о любой ручке,
# приехавшей вместе с `/health`.
#
# Состав здесь — не стиль, а граница безопасности: модуль `app/panel_client.py`
# существует затем, чтобы того, чего в приложении НЕТ, нельзя было открыть
# опечаткой в env.
EXPECTED_ROUTES = {
    ("/health", ("GET",)),
    ("/panel", ("GET",)),
    ("/panel/login", ("GET",)),
    ("/panel/login", ("POST",)),
    ("/panel/tamapi", ("GET",)),
    ("/panel/tamapi/", ("GET",)),
    ("/panel/tamapi/action", ("POST",)),
    ("/panel/tamapi/api/summary", ("GET",)),
    ("/panel/tamapi/dynamics", ("GET",)),
}


def _routes(api) -> set:
    out = set()
    for r in api.routes:
        methods = tuple(sorted(getattr(r, "methods", None) or ()))
        # HEAD ездит бесплатным довеском к GET у части классов маршрутов —
        # в состав его не считаем, он ничего не открывает.
        methods = tuple(m for m in methods if m != "HEAD")
        out.add((r.path, methods))
    return out


def test_the_app_offers_exactly_these_routes(instance):
    """В ОБЕ стороны: «нет лишних» ловит ручку, приехавшую вместе с
    `/health`; «нет пропавших» ловит починку, сделанную сносом дашборда."""
    api, _ = instance
    got = _routes(api)
    assert got == EXPECTED_ROUTES, (
        "состав маршрутов инстанса разошёлся со спекой.\n"
        "  лишние: %s\n  пропали: %s"
        % (sorted(got - EXPECTED_ROUTES), sorted(EXPECTED_ROUTES - got)))


def test_the_jarvis_panel_is_still_absent(instance):
    """Названо отдельно, хотя и покрыто списком выше: это ТА САМАЯ дыра, ради
    которой приложение вообще отделено от `app.main`. Отдельный сторож
    переживёт любую законную правку списка."""
    api, _ = instance
    leaked = sorted(p for p, _m in _routes(api) if p.startswith("/panel/jarvis"))
    assert not leaked, "инстанс клиента отдаёт панель Джарвиса: %s" % (leaked,)


def test_health_did_not_drag_in_background_tasks(instance):
    """Ни одного startup-обработчика. Второй `_watchdog_loop` — это второй
    хозяин у живого бота (DEV-38, 13 ч 42 мин простоя Ольги 16.08)."""
    api, _ = instance
    assert not api.router.on_startup, api.router.on_startup
    assert not api.router.on_shutdown, api.router.on_shutdown


def test_the_api_schema_is_still_closed(instance):
    """Карта ручек закрыта (`openapi_url=None`) — возражение §3B опирается
    именно на это. Новая неаутентифицированная ручка не имеет права её
    приоткрыть."""
    api, _ = instance
    assert api.openapi_url is None, api.openapi_url
    assert TestClient(api).get("/openapi.json").status_code != 200
