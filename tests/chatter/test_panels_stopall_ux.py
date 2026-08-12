"""Подтверждение stop_all: порядок, обратимость, цена решения.

Замечания 17–22 из разбора панелей 12.08. Текст самих двух одобренных фраз
модалки НЕ трогаем — тест ниже держит их дословно, чтобы правки вокруг не
переписали согласованное.
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatter.storage.db import Store

KEY = "test-owner-key"
NOW = 1_800_000_000.0
DAY = 86400.0

# Две фразы, одобренные владельцем. Изменение — только с его слов.
APPROVED_1 = ("Вона перестане відповідати <b>ВСІМ</b> лідам, доки ви не "
              "увімкнете її назад.")
APPROVED_2 = "Діалоги не зникнуть, історія збережеться."


def _mk_db(tmp_path):
    """Пять контактов, покрывающих каждое исключение счётчика."""
    db = tmp_path / "p.db"
    s = Store(str(db))
    for cid, state in (("1:volska", "qualifying"),   # ведёт бот  → считается
                       ("2:volska", "hot"),          # ведёт бот  → считается
                       ("3:volska", "dead"),         # терминальный → нет
                       ("4:volska", "closed"),       # терминальный → нет
                       ("5:volska", "escalated")):   # уже у человека → нет
        s.get_or_create_contact(cid)
        s.set_state(cid, state)
        s.add_message(cid, "user", "текст", ts=NOW - DAY)
    # 5-й передан человеку: активная эскалация
    s.set_runtime_flag("esc_active:5:volska", "bot:1:5", ts=NOW - DAY)
    del s
    # 6-й на паузе поимённо — бот его и так не ведёт
    s2 = Store(str(db))
    s2.get_or_create_contact("6:volska")
    s2.set_state("6:volska", "qualifying")
    s2.add_message("6:volska", "user", "текст", ts=NOW - DAY)
    del s2
    con = sqlite3.connect(str(db))
    con.execute("UPDATE contacts SET paused=1 WHERE contact_id='6:volska'")
    con.commit()
    con.close()
    return db


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = _mk_db(tmp_path)
    # Живой heartbeat: без него статус всегда «Немає зв'язку», и ветку паузы
    # («▶️ Увімкнути») не увидеть — она стоит ПОСЛЕ проверки связи.
    hb = tmp_path / "hb.txt"
    hb.write_text("ok", encoding="utf-8")
    monkeypatch.setenv("JARVIS_PANELS_KEY", KEY)
    monkeypatch.setenv("TAMAPI_DB", str(db))
    monkeypatch.setenv("TAMAPI_HEARTBEAT", str(hb))

    import app.routers.panels_auth as pa
    import app.routers.tamapi_dashboard as td
    for m in (pa, td):
        importlib.reload(m)

    api = FastAPI()
    api.include_router(pa.router)
    api.include_router(td.router)
    return TestClient(api), str(db)


def _html(c) -> str:
    r = c.get("/panel/tamapi", headers={"X-Panels-Key": KEY})
    assert r.status_code == 200
    return r.text


def _modal(html: str) -> str:
    i = html.index("id='pausebox'")
    return html[i:html.index("id='paidbox'")]


# ── одобренный текст не переписан ───────────────────────────────────────────

def test_approved_wording_is_untouched(client):
    c, _ = client
    m = _modal(_html(c))
    assert APPROVED_1 in " ".join(m.split())
    assert APPROVED_2 in " ".join(m.split())


# ── 21: разрушительная кнопка не под большим пальцем ────────────────────────

def test_cancel_comes_before_confirm(client):
    c, _ = client
    m = _modal(_html(c))
    assert m.index("Скасувати") < m.index("Так, зупинити"), (
        "подтверждение стоит первым — на телефоне оно попадает ровно туда, "
        "куда палец жмёт «ОК»")


# ── 18: триггер называет действие своим именем ──────────────────────────────

def test_trigger_button_says_stop_all(client):
    c, _ = client
    html = _html(c)
    assert "Зупинити всіх" in html
    assert "⏸ Пауза" not in html, "«пауза» преуменьшает глобальный kill switch"


def test_resume_button_unchanged_when_paused(client, tmp_path):
    """Обратная кнопка — та же: правка триггера не имеет права её тронуть."""
    c, db = client
    s = Store(db)
    s.set_runtime_flag("kill_switch", "1", ts=NOW)
    del s
    html = _html(c)
    assert "▶️ Увімкнути" in html
    # Ищем В РАЗМЕТКЕ, а не по всей странице: в комментарии внутри `<style>`
    # разбор узкого экрана поимённо называет обрезанные элементы, и «Зупинити
    # всіх» там упомянута. Сторож с 53c9ea19 краснел на собственном тексте
    # объяснения, а не на кнопке.
    assert "Зупинити всіх" not in html.split("</style>", 1)[1]


# ── 19: что будет с теми, кто напишет во время паузы ────────────────────────

def test_modal_says_what_happens_to_incoming(client):
    c, _ = client
    m = " ".join(_modal(_html(c)).split())
    assert "Хто напише під час паузи, відповіді не отримає." in m


# ── 20: живой счётчик диалогов ──────────────────────────────────────────────

def test_modal_shows_live_dialog_count(client):
    """Двое из шести: терминальные, эскалированный и поимённо снятый не в счёт."""
    c, _ = client
    m = " ".join(_modal(_html(c)).split())
    assert "Зараз у роботі: 2 діалоги." in m


def test_count_reflects_data_not_a_constant(client, tmp_path):
    c, db = client
    s = Store(db)
    s.set_state("3:volska", "qualifying")     # был dead → стал ведомым
    del s
    m = " ".join(_modal(_html(c)).split())
    assert "Зараз у роботі: 3 діалоги." in m


def test_zero_dialogs_is_its_own_sentence(client, tmp_path):
    """Ноль — не «0 діалогів», а полезный факт: пауза никого не заденет."""
    c, db = client
    s = Store(db)
    for cid in ("1:volska", "2:volska"):
        s.set_state(cid, "dead")
    del s
    m = " ".join(_modal(_html(c)).split())
    assert "нікого не веде" in m
    assert "0 діалог" not in m


@pytest.mark.parametrize(
    "n, expected",
    [(1, "1 діалог"), (2, "2 діалоги"), (4, "4 діалоги"), (5, "5 діалогів"),
     (11, "11 діалогів"), (21, "21 діалог"), (22, "22 діалоги"),
     (25, "25 діалогів"), (0, "0 діалогів")],
)
def test_ukrainian_plural(n, expected):
    from app.routers.panels_ui import plural_dialogs
    assert plural_dialogs(n) == expected


# ── 22: Esc, тап по фону, перехват фокуса ───────────────────────────────────

def test_modal_is_a_dialog_for_assistive_tech(client):
    c, _ = client
    m = _modal(_html(c))
    assert "role='dialog'" in m
    assert "aria-modal='true'" in m
    assert "aria-labelledby='pausebox-title'" in m
    assert "id='pausebox-title'" in m


# Поведение JS браузером отсюда не проверить — держим КАЖДЫЙ обработчик
# отдельным узким утверждением, иначе мутация «выключить Esc» проходит мимо
# теста, который ищет слово «Escape» где угодно на странице. Живая проверка
# клавиатурой остаётся ручной.

def test_js_closes_on_escape(client):
    c, _ = client
    assert "e.key==='Escape'" in _html(c)


def test_js_closes_on_backdrop_tap(client):
    c, _ = client
    assert "closeOnBackdrop(event,'pausebox')" in _modal(_html(c))


def test_js_traps_tab_inside_the_open_modal(client):
    c, _ = client
    html = _html(c)
    assert "e.key!=='Tab'" in html
    assert "e.shiftKey" in html, "цикл должен работать и назад по Shift+Tab"


def test_js_returns_focus_to_the_trigger(client):
    c, _ = client
    assert "lastFocus.focus()" in _html(c)


# ── 17: текстовая метка состояния РЯДОМ с цветом ────────────────────────────

def test_state_label_is_next_to_the_dot_not_instead(tmp_path, monkeypatch):
    from app.routers.panels_ui import STATE_LABEL, STATE_TONE, dot_html

    for state, word in STATE_LABEL.items():
        h = dot_html(state)
        # Класс точки — не имя состояния, а его СМЫСЛ (ok → calm: «норма» цвета
        # не получает, зелёный отдан деньгам). Точка при этом обязана остаться:
        # слово рядом с ней — дополнение, а не замена.
        assert f"dot {STATE_TONE[state]}" in h, "цветная точка обязана остаться"
        assert word in h, "рядом с ней обязано быть слово"


def test_rows_differing_only_by_state_differ_in_text():
    """Две строки «Ready · останній результат N» различались ТОЛЬКО оттенком."""
    import app.routers.jarvis_panel as jp
    from app.services.jarvis_farm import Row

    ok = jp._rows_html([Row("a", "JarvisA", "ok", "Ready · останній результат 0")], NOW)
    bad = jp._rows_html([Row("b", "JarvisB", "warn", "Ready · останній результат 1")], NOW)
    # Снимаем всё, что и так различается (имя, деталь) — остаётся сигнал состояния.
    ok_sig = ok.replace("JarvisA", "X").replace("0", "N")
    bad_sig = bad.replace("JarvisB", "X").replace("1", "N")
    assert ok_sig != bad_sig
    assert "норма" in ok and "увага" in bad
