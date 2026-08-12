"""Знаменатели дашборда: воронка по ЛЮДЯМ, нагрузка по СОБЫТИЯМ, пакет по
календарному месяцу, «Требує вас» с порогом свежести, длительность без
округления в ноль.

Разбор, из которого выросли эти правила (12.08, живая БД): «1 діалог» и рядом
«4 кваліфіковано, 400%» — числитель считал СТРОКИ переходов, знаменатель ЛЮДЕЙ,
и все 4 перехода принадлежали одному лиду. «3 передано, 75%» было опаснее 400%:
абсурд виден, а правдоподобная ложь — нет.
"""
from __future__ import annotations

import importlib
import time
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import tamapi_metrics as M
from chatter.payments.model import PaymentRecord, make_dedup_key
from chatter.payments.money import from_major
from chatter.storage.db import Store

KEY = "test-owner-key"
NOW = 1_800_000_000.0
DAY = 86400.0
HOUR = 3600.0


def _month_start(now: float) -> float:
    """Начало текущего календарного месяца в ЛОКАЛЬНОМ времени — счёт идёт по
    календарю клиента, а не по UTC."""
    d = datetime.fromtimestamp(now)
    return d.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()


def _seed_one_lead(path, base: float) -> str:
    """Ровно живой случай 12.08: один лид, четыре перехода в hot, три карточки."""
    s = Store(str(path))
    cid = "8849893367:volska"
    s.get_or_create_contact(cid)
    s.add_message(cid, "user", "привіт", ts=base - 2 * HOUR)
    for i in range(4):
        s.record_transition(cid, from_state="qualifying", to_state="hot",
                            signal="interested", ts=base - (4 - i) * HOUR)
    for i in range(3):
        s.add_card(msg_id=170 + i, contact_id=cid, kind="escalation",
                   ts=base - HOUR + i * 60)
    del s
    return str(path)


@pytest.fixture()
def one_lead_many_events(tmp_path):
    return _seed_one_lead(tmp_path / "one.db", NOW)


@pytest.fixture()
def live_one_lead(tmp_path):
    """То же самое, но вокруг НАСТОЯЩЕГО времени: страница считает окна от
    `time.time()`, и данные из синтетического «сейчас» в него не попадают."""
    return _seed_one_lead(tmp_path / "live.db", time.time())


# ── A. Воронка считает ЛЮДЕЙ, когорту задаёт первая ступень ────────────────

def test_repeated_events_do_not_inflate_the_funnel(one_lead_many_events):
    f = M.summary(one_lead_many_events, now=NOW, period="week")["funnel"]
    assert f["dialogs"] == 1
    assert f["qualified"] == 1, "четыре перехода одного лида — это один человек"
    assert f["handed"] == 1, "три карточки одного лида — это один человек"


def test_no_step_can_exceed_the_cohort(tmp_path):
    """Инвариант держится КОНСТРУКЦИЕЙ: каждая ступень — подмножество когорты,
    поэтому доля выше 100% невозможна, и зажимать её не нужно.

    Данные подобраны так, чтобы проверка РАЗЛИЧАЛА: в когорте один свежий лид, а
    события (переход, карточка, оплата) висят на ДВУХ лидах вне окна. Ступень,
    посчитанная мимо когорты, даст 2 при когорте 1 — то есть 200%. Первая
    версия теста этого не ловила: у неё событий вне когорты не было вовсе, и
    мутация «оплаты мимо когорты» оставляла её зелёной (гейт DEV-26)."""
    p = tmp_path / "cohort_guard.db"
    s = Store(str(p))
    s.get_or_create_contact("fresh:volska")
    s.add_message("fresh:volska", "user", "сьогодні", ts=NOW - HOUR)
    for i in range(2):
        cid = f"old{i}:volska"
        s.get_or_create_contact(cid)
        s.add_message(cid, "user", "давно", ts=NOW - 9 * DAY)
        s.record_transition(cid, from_state="qualifying", to_state="hot",
                            signal="interested", ts=NOW - DAY)
        s.add_card(msg_id=200 + i, contact_id=cid, kind="escalation", ts=NOW - DAY)
        s.record_payment(PaymentRecord(
            contact_id=cid, dedup_key=make_dedup_key("tap", 50 + i), ts=NOW - DAY,
            confirmed_by="owner", amount=from_major("750", "USD")))
    del s

    f = M.summary(str(p), now=NOW, period="week")["funnel"]
    assert f["dialogs"] == 1
    for step in ("qualified", "handed", "payments"):
        assert f[step] == 0, f"ступень «{step}» посчитана мимо когорты"
        assert f[step] <= f["dialogs"]


def test_events_outside_the_cohort_do_not_count(tmp_path):
    """Лид написал 9 дней назад (в когорту недели не входит), а в hot уехал
    вчера. Раньше он давал числитель без знаменателя — то есть >100%."""
    p = tmp_path / "old.db"
    s = Store(str(p))
    s.get_or_create_contact("old:volska")
    s.add_message("old:volska", "user", "давно", ts=NOW - 9 * DAY)
    s.record_transition("old:volska", from_state="qualifying", to_state="hot",
                        signal="interested", ts=NOW - DAY)
    s.get_or_create_contact("fresh:volska")
    s.add_message("fresh:volska", "user", "сьогодні", ts=NOW - HOUR)
    del s

    f = M.summary(str(p), now=NOW, period="week")["funnel"]
    assert f["dialogs"] == 1
    assert f["qualified"] == 0, "переход лида вне когорты попал в числитель"


# ── B. События живут отдельным блоком «Навантаження» ───────────────────────

def test_load_block_counts_events_not_people(one_lead_many_events):
    """Факт «на одного лида пришлось 4 перехода и 3 карточки» полезен — он
    просто не воронка. Выбрасывать его нельзя, смешивать с людьми тоже."""
    load = M.summary(one_lead_many_events, now=NOW, period="week")["load"]
    assert load["transitions"] == 4
    assert load["cards"] == 3


# ── C. Пакет: календарный месяц, перерасход видно ──────────────────────────

def test_package_counts_the_calendar_month_not_a_rolling_window(tmp_path, monkeypatch):
    """Скользящее окно 30 дней не обнуляется первого числа, а счёт клиенту
    выставляется за календарный месяц — бар обязан жить по счёту."""
    p = tmp_path / "pkg.db"
    ms = _month_start(NOW)
    s = Store(str(p))
    s.get_or_create_contact("in:volska")
    s.add_message("in:volska", "user", "цього місяця", ts=ms + HOUR)
    s.get_or_create_contact("out:volska")
    s.add_message("out:volska", "user", "минулого місяця", ts=ms - HOUR)
    del s

    import app.routers.tamapi_dashboard as td
    monkeypatch.setenv("TAMAPI_DB", str(p))
    importlib.reload(td)
    pkg = td._package(now=NOW)
    assert pkg["used"] == 1, "в пакет попал лид из прошлого месяца"
    assert pkg["since"] == pytest.approx(ms)


def test_package_reports_overflow_share(tmp_path, monkeypatch):
    """Перерасход должен доезжать до разметки числом, а не тонуть в min(pct,100)."""
    p = tmp_path / "over.db"
    ms = _month_start(NOW)
    s = Store(str(p))
    for i in range(3):
        cid = f"{i}:volska"
        s.get_or_create_contact(cid)
        s.add_message(cid, "user", "привіт", ts=ms + HOUR)
    del s

    import app.routers.tamapi_dashboard as td
    monkeypatch.setenv("TAMAPI_DB", str(p))
    monkeypatch.setenv("TAMAPI_PACKAGE", "2")
    importlib.reload(td)
    pkg = td._package(now=NOW)
    assert pkg["used"] == 3 and pkg["limit"] == 2
    assert pkg["over"] == 1
    assert pkg["over_pct"] > 0, "доля перерасхода не посчитана — полоске нечего рисовать"


# ── D. «Требує вас»: свежесть, мёртвый лид, два возраста ───────────────────

def test_card_older_than_48h_is_stale(tmp_path):
    p = tmp_path / "att.db"
    s = Store(str(p))
    for cid, age in (("fresh:volska", 47 * HOUR), ("stale:volska", 49 * HOUR)):
        s.get_or_create_contact(cid)
        s.add_message(cid, "user", "текст", ts=NOW - age)
        s.add_card(msg_id=hash(cid) % 1000, contact_id=cid, kind="escalation",
                   ts=NOW - age)
        s.set_runtime_flag(f"esc_active:{cid}", "bot:1:5", ts=NOW - age)
    del s

    by_id = {i["contact_id"]: i for i in M.needs_attention(str(p), now=NOW)}
    assert by_id["fresh:volska"]["stale"] is False
    assert by_id["stale:volska"]["stale"] is True


def test_dead_lead_with_an_open_card_is_flagged(tmp_path):
    """Живой случай: контакт `dead`, а карточка висит 14 суток. Это либо забытый
    флаг, либо разъехавшееся состояние — молчать об этом нельзя."""
    p = tmp_path / "dead.db"
    s = Store(str(p))
    s.get_or_create_contact("dead:volska")
    s.set_state("dead:volska", "dead")
    s.add_message("dead:volska", "user", "текст", ts=NOW - 14 * DAY)
    s.add_card(msg_id=126, contact_id="dead:volska", kind="escalation",
               ts=NOW - 14 * DAY)
    s.set_runtime_flag("esc_active:dead:volska", "bot:1:126", ts=NOW - 14 * DAY)
    del s

    item = M.needs_attention(str(p), now=NOW)[0]
    assert item["dead"] is True


def test_item_carries_both_ages(tmp_path):
    """Возраст карточки и возраст последнего входящего — разные вещи: первое про
    «когда бот поднял руку», второе про «сколько человек ждёт»."""
    p = tmp_path / "two.db"
    s = Store(str(p))
    s.get_or_create_contact("x:volska")
    s.add_message("x:volska", "user", "питання", ts=NOW - 3 * HOUR)
    s.add_card(msg_id=1, contact_id="x:volska", kind="escalation", ts=NOW - 5 * HOUR)
    s.set_runtime_flag("esc_active:x:volska", "bot:1:1", ts=NOW - 5 * HOUR)
    del s

    item = M.needs_attention(str(p), now=NOW)[0]
    assert item["card_ts"] == pytest.approx(NOW - 5 * HOUR)
    assert item["last_ts"] == pytest.approx(NOW - 3 * HOUR)


# ── E. Тривалість: минуты вместо нуля, основание рядом с дельтой ───────────

def test_ten_minute_dialog_is_not_rounded_to_zero(tmp_path):
    """`round(x, 1)` в ДНЯХ схлопывал всё короче 2.4 часа в ноль — и форматтер,
    умеющий минуты, получал уже ноль."""
    p = tmp_path / "dur.db"
    s = Store(str(p))
    s.get_or_create_contact("q:volska")
    s.add_message("q:volska", "user", "a", ts=NOW - 10 * 60)
    s.add_message("q:volska", "user", "b", ts=NOW - 30)
    del s

    total = M.series_for(str(p), ["duration"], "week", now=NOW)[0].total
    assert total is not None and total > 0
    assert round(total * 1440) == pytest.approx(9, abs=1)

    import app.routers.tamapi_dashboard as td
    assert td._fmt_value(M.METRIC_BY_KEY["duration"], total).endswith("хв")


def test_duration_series_reports_its_basis(tmp_path):
    """«▼100%» при n=1 против n=1 — это не падение, а два разных лида. Основание
    обязано доезжать до экрана вместе с процентом."""
    p = tmp_path / "basis.db"
    s = Store(str(p))
    s.get_or_create_contact("now:volska")
    s.add_message("now:volska", "user", "a", ts=NOW - 10 * 60)
    s.add_message("now:volska", "user", "b", ts=NOW - 30)
    s.get_or_create_contact("prev:volska")
    s.add_message("prev:volska", "user", "a", ts=NOW - 14 * DAY)
    s.add_message("prev:volska", "user", "b", ts=NOW - 8 * DAY)
    del s

    ser = M.series_for(str(p), ["duration"], "week", now=NOW)[0]
    assert ser.basis == 1
    assert ser.prev_basis == 1


# ── F. То же самое, но глазами страницы ────────────────────────────────────

def _client(db_path, monkeypatch, package: str = "500"):
    monkeypatch.setenv("JARVIS_PANELS_KEY", KEY)
    monkeypatch.setenv("TAMAPI_DB", str(db_path))
    monkeypatch.setenv("TAMAPI_PACKAGE", package)
    monkeypatch.setenv("TAMAPI_HEARTBEAT", str(db_path) + ".nope")
    import app.routers.panels_auth as pa
    import app.routers.tamapi_dashboard as td
    for m in (pa, td):
        importlib.reload(m)
    api = FastAPI()
    api.include_router(pa.router)
    api.include_router(td.router)
    return TestClient(api)


def _screen(c):
    r = c.get("/panel/tamapi", headers={"X-Panels-Key": KEY})
    assert r.status_code == 200
    return r.text


def _percents(body: str) -> list[int]:
    import re
    return [int(x) for x in re.findall(r"<div class='p'>(\d+)%</div>", body)]


def test_the_screen_shows_no_percent_above_100(live_one_lead, monkeypatch):
    body = _screen(_client(live_one_lead, monkeypatch))
    pcts = _percents(body)
    assert pcts, "проценты со страницы пропали — проверять стало нечего"
    assert max(pcts) <= 100, f"доля выше 100% на экране: {pcts}"


def test_percent_is_the_share_of_the_cohort(tmp_path, monkeypatch):
    """Двое писали, один передан человеку без «горячего» перехода. Доля от
    ПРЕДЫДУЩЕЙ ступени дала бы деление на ноль либо бесконечность; доля от
    когорты — честные 50%."""
    p = tmp_path / "cohort.db"
    live = time.time()
    s = Store(str(p))
    for cid in ("a:volska", "b:volska"):
        s.get_or_create_contact(cid)
        s.add_message(cid, "user", "привіт", ts=live - HOUR)
    s.add_card(msg_id=9, contact_id="b:volska", kind="escalation", ts=live - HOUR)
    del s

    body = _screen(_client(p, monkeypatch))
    assert 50 in _percents(body)
    assert max(_percents(body)) <= 100


def test_the_screen_has_a_separate_load_block(live_one_lead, monkeypatch):
    body = _screen(_client(live_one_lead, monkeypatch))
    assert "Навантаження" in body
    assert "подій" in body, "блок нагрузки обязан назвать единицу — события"


def test_funnel_and_package_are_named_differently(live_one_lead, monkeypatch):
    """«1 діалог» в воронке и «4 діалоги» в баре — одна мера на разных окнах.
    Одинаковое слово для разных окон и есть источник вопроса «почему 1 и 4»."""
    body = _screen(_client(live_one_lead, monkeypatch))
    assert "унікальні за 7 днів" in body          # воронка
    assert "унікальних лідів цього місяця" in body  # пакет


def test_package_overflow_is_drawn_not_clamped(tmp_path, monkeypatch):
    p = tmp_path / "overweb.db"
    live = time.time()
    s = Store(str(p))
    for i in range(3):
        cid = f"{i}:volska"
        s.get_or_create_contact(cid)
        s.add_message(cid, "user", "привіт", ts=live - 60)
    del s

    body = _screen(_client(p, monkeypatch, package="2"))
    assert "class='over'" in body, "перерасход не нарисован — полоска врёт про 100%"
    assert "Пакет вичерпано" in body


def test_stale_cards_collapse_into_a_counter(tmp_path, monkeypatch):
    p = tmp_path / "stale.db"
    live = time.time()
    s = Store(str(p))
    for cid, age in (("fresh:volska", 2 * HOUR),
                     ("old1:volska", 9 * DAY), ("old2:volska", 14 * DAY)):
        s.get_or_create_contact(cid)
        s.add_message(cid, "user", "текст", ts=live - age)
        s.add_card(msg_id=abs(hash(cid)) % 1000, contact_id=cid,
                   kind="escalation", ts=live - age)
        s.set_runtime_flag(f"esc_active:{cid}", "bot:1:5", ts=live - age)
    del s

    body = _screen(_client(p, monkeypatch))
    assert body.count("▶️ Повернути") == 1, "протухшие карточки остались развёрнутыми"
    assert "Застарілі" in body
    assert ">2<" in body or "Застарілі (2)" in body


def test_dead_lead_is_marked_on_the_screen(tmp_path, monkeypatch):
    p = tmp_path / "deadweb.db"
    live = time.time()
    s = Store(str(p))
    s.get_or_create_contact("d:volska")
    s.set_state("d:volska", "dead")
    s.add_message("d:volska", "user", "текст", ts=live - 2 * HOUR)
    s.add_card(msg_id=5, contact_id="d:volska", kind="escalation", ts=live - 2 * HOUR)
    s.set_runtime_flag("esc_active:d:volska", "bot:1:5", ts=live - 2 * HOUR)
    del s

    assert "лід мертвий, картку не закрито" in _screen(_client(p, monkeypatch))


def test_dead_lead_is_marked_the_same_way_when_stale(tmp_path, monkeypatch):
    """Живьём мёртвый лид И БЫЛ застарелым (14 суток) — то есть полная пометка
    на экран не попадала вообще: в свёртке стояла короткая. Формулировка обязана
    быть одна, иначе решение «помечать противоречие» не выполнено ровно в том
    случае, ради которого принималось."""
    p = tmp_path / "deadstale.db"
    live = time.time()
    s = Store(str(p))
    s.get_or_create_contact("ds:volska")
    s.set_state("ds:volska", "dead")
    s.add_message("ds:volska", "user", "текст", ts=live - 14 * DAY)
    s.add_card(msg_id=6, contact_id="ds:volska", kind="escalation", ts=live - 14 * DAY)
    s.set_runtime_flag("esc_active:ds:volska", "bot:1:6", ts=live - 14 * DAY)
    del s

    body = _screen(_client(p, monkeypatch))
    assert "Застарілі (1)" in body
    assert "лід мертвий, картку не закрито" in body


def test_fresh_card_shows_the_age_of_the_last_inbound(tmp_path, monkeypatch):
    p = tmp_path / "ages.db"
    live = time.time()
    s = Store(str(p))
    s.get_or_create_contact("w:volska")
    s.add_message("w:volska", "user", "питання", ts=live - 3 * HOUR)
    s.add_card(msg_id=7, contact_id="w:volska", kind="escalation", ts=live - 5 * HOUR)
    s.set_runtime_flag("esc_active:w:volska", "bot:1:7", ts=live - 5 * HOUR)
    del s

    body = _screen(_client(p, monkeypatch))
    assert "чекає" in body, "возраст последнего входящего на карточку не доехал"


def test_duration_tile_shows_the_basis(tmp_path, monkeypatch):
    p = tmp_path / "tile.db"
    live = time.time()
    s = Store(str(p))
    s.get_or_create_contact("t:volska")
    s.add_message("t:volska", "user", "a", ts=live - 10 * 60)
    s.add_message("t:volska", "user", "b", ts=live - 30)
    del s

    c = _client(p, monkeypatch)
    body = c.get("/panel/tamapi/dynamics?m=duration",
                 headers={"X-Panels-Key": KEY}).text
    assert "n=1" in body, "процент без основания выборки — не сравнение"
