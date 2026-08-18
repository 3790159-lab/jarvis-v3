# -*- coding: utf-8 -*-
"""Панель обязана знать ОБЕ формы отметки живости chatter-раннера.

ЗАМЕР (18.08 23:45, живое дерево). `state/chatter_heartbeat.txt` заморожен на
16.08 17:08 — на минуте, когда поднялся мультиклиентный гардиан (PID 3148,
старт 16.08 17:08:30). С тех пор раннеры пишут пофайлово:
`chatter_heartbeat_volska.txt` (свежий, бот Ольги отвечает) и
`chatter_heartbeat_yarina.txt`. Панель читала ТОЛЬКО легаси-имя
(`tamapi_dashboard._heartbeat_age`, дефолт `state/chatter_heartbeat.txt`),
получала возраст ~2 суток и вторые сутки показывала клиенту
«Немає зв'язку — технічна проблема, ми вже бачимо» при живом боте.

Это ВТОРОЕ появление одного дефекта: ровно то же чинили 16.08 в
`ops_watchdog.py` (`CHATTER_BEAT_LEGACY_NAME` + `CHATTER_BEAT_CLIENT_GLOB`,
`tests/test_ops_watchdog_heartbeat_forms.py`). Там правило — «красим по самому
старому», потому что проба смотрит на ВСЮ ферму. Здесь экран один клиента,
поэтому правило другое и выписано явно: берём САМУЮ СВЕЖУЮ из известных форм.

Сторож держит четыре вещи, и каждая — отдельный способ снова соврать клиенту:
свежая per-slug форма побеждает протухшую легаси; из двух живых берётся
свежайшая; явный `TAMAPI_HEARTBEAT` остаётся единственным источником и НЕ
подменяется догадкой; отсутствие данных остаётся красным, а не становится
зелёным «ну где-то же нашлось».
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

NOW_SLACK = 5.0          # запас на время исполнения теста
STALE = 200_000.0        # ~2.3 суток — порядок величины реального инцидента


def _beat(path: Path, age_s: float) -> None:
    """Создать отметку живости с ЗАДАННЫМ возрастом.

    Возраст задаём через mtime, а не через содержимое: панель меряет именно
    mtime (`Path.stat().st_mtime`), и тест обязан щупать то же самое.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("beat", encoding="utf-8")
    t = time.time() - age_s
    os.utime(path, (t, t))


@pytest.fixture()
def panel(tmp_path, monkeypatch):
    """Панель на подставном дереве: `state/` — свой, БД — своя.

    `chdir` обязателен: пути отметок в панели ОТНОСИТЕЛЬНЫЕ (`state/...`), и
    без смены каталога тест щупал бы живое дерево — то самое, чей дефект он
    воспроизводит.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".secrets").mkdir()
    monkeypatch.setenv("TAMAPI_DB", str(tmp_path / ".secrets" / "p.db"))
    monkeypatch.setenv("TAMAPI_SLUG", "volska")
    monkeypatch.delenv("TAMAPI_HEARTBEAT", raising=False)
    import app.routers.tamapi_dashboard as td
    return td


def test_per_slug_beat_wins_over_stale_legacy(panel, tmp_path):
    """ВОСПРОИЗВЕДЕНИЕ ИНЦИДЕНТА: раннер жив, легаси-файл протух.

    До починки панель видела только легаси и говорила клиенту «Немає зв'язку».
    """
    _beat(tmp_path / "state" / "chatter_heartbeat.txt", STALE)
    _beat(tmp_path / "state" / "chatter_heartbeat_volska.txt", 12.0)

    st = panel._status()

    assert st["code"] != "down", (
        "панель объявила разрыв связи при ЖИВОМ раннере — это и есть дефект: "
        "прочитан только легаси-файл"
    )
    assert st["age"] is not None and st["age"] < 60, (
        "возраст взят не из живой отметки: %r" % (st["age"],))
    assert st["beat_source"] == "chatter_heartbeat_volska.txt", (
        "панель обязана НАЗВАТЬ источник, иначе ложный канал снова спрячется: "
        "%r" % (st["beat_source"],))


def test_takes_the_freshest_of_the_two_forms(panel, tmp_path):
    """Из двух известных форм берётся САМАЯ СВЕЖАЯ, а не «своя по имени».

    Обратная половина: если однажды писать начнут в легаси-файл, а per-slug
    останется лежать со вчера, экран не имеет права краснеть по мёртвому.
    """
    _beat(tmp_path / "state" / "chatter_heartbeat.txt", 9.0)
    _beat(tmp_path / "state" / "chatter_heartbeat_volska.txt", STALE)

    st = panel._status()

    assert st["code"] != "down", "свежая легаси-отметка проигнорирована"
    assert st["beat_source"] == "chatter_heartbeat.txt", st["beat_source"]


def test_legacy_only_tree_still_works(panel, tmp_path):
    """Одноарендное дерево (per-slug файла нет вовсе) обязано работать как было."""
    _beat(tmp_path / "state" / "chatter_heartbeat.txt", 10.0)

    st = panel._status()

    assert st["code"] != "down"
    assert st["beat_source"] == "chatter_heartbeat.txt"


def test_explicit_env_stays_the_only_source(panel, tmp_path, monkeypatch):
    """Явный `TAMAPI_HEARTBEAT` НЕ подменяется догадкой.

    На нём стоит демо-стенд (`scripts/panels_demo.py`) и фикстура
    `tests/chatter/test_panels_web.py`, которая намеренно указывает на
    несуществующий файл. Починка, которая «всё равно что-нибудь найдёт»,
    молча увела бы стенд на чужую отметку — это ровно тот класс, который мы
    здесь и чиним, только зеркальный.
    """
    monkeypatch.setenv("TAMAPI_HEARTBEAT", str(tmp_path / "state" / "nope.txt"))
    _beat(tmp_path / "state" / "chatter_heartbeat_volska.txt", 5.0)

    st = panel._status()

    assert st["age"] is None, (
        "указан несуществующий файл, а панель нашла себе другой: %r" % (st["age"],))
    assert st["code"] == "down"


def test_no_beat_at_all_is_still_down(panel):
    """Починка не имеет права превратить «данных нет» в зелёное."""
    st = panel._status()
    assert st["code"] == "down"
    assert st["age"] is None
    assert st["beat_source"] is None, (
        "источника нет — панель обязана сказать это прямо, а не назвать файл, "
        "которого не читала")


def test_page_names_the_source_it_used(tmp_path, monkeypatch):
    """Источник обязан доезжать ДО РАЗМЕТКИ.

    Дефект жил два дня именно потому, что канал был не виден: экран говорил
    «технічна проблема», и по нему нельзя было понять, ЧТО он прочитал.
    Сторож на `_status()` этого не ловит — он щупает функцию, а врёт страница.
    """
    import importlib

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from chatter.storage.db import Store

    key = "test-owner-key"
    db = tmp_path / "p.db"
    Store(str(db))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JARVIS_PANELS_KEY", key)
    monkeypatch.setenv("TAMAPI_DB", str(db))
    monkeypatch.setenv("TAMAPI_SLUG", "volska")
    monkeypatch.delenv("TAMAPI_HEARTBEAT", raising=False)
    _beat(tmp_path / "state" / "chatter_heartbeat.txt", STALE)
    _beat(tmp_path / "state" / "chatter_heartbeat_volska.txt", 7.0)

    import app.routers.panels_auth as pa
    import app.routers.tamapi_dashboard as td
    for m in (pa, td):
        importlib.reload(m)

    api = FastAPI()
    api.include_router(pa.router)
    api.include_router(td.router)
    c = TestClient(api)
    c.cookies.set("panels_key", key)

    body = c.get("/panel/tamapi").text

    assert "chatter_heartbeat_volska.txt" in body, (
        "страница не называет отметку, по которой вынесла статус")
