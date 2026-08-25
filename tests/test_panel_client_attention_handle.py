# -*- coding: utf-8 -*-
"""Часть 1 арки: немая ручка `/ops/attention` на инстансе клиента.

Спека `docs/superpowers/specs/2026-08-24-escalation-seen-probe.md` (§3 форма 1,
§4, §4.1, §4.2, §9 п. 4 и п. 7), контракт «проба карточки эскалации не читают»
§2 и ловушки 1–2.

СТОРОЖА ПИСАНЫ ОТ СПЕКИ И КОНТРАКТА. Реализацию писал другой автор в другом
дереве, его кода автор этих сторожей не видел и не искал: тест, написанный по
коду, согласен с кодом по определению и молчит ровно там, где код забыл.

ЧТО ЗДЕСЬ ОХРАНЯЕТСЯ, тремя разными по природе утверждениями:

1. ЦЕНА ОТВЕТА. Ручка не аутентифицирована, значит её тело читает кто угодно,
   кто дотянулся до порта в тайнете. Форма пиннится ЛИТЕРАЛЬНЫМ списком ключей
   в ОБЕ стороны: ни одного лишнего (утечка слага/контакта/текста), ни одного
   пропавшего (проба получит `bad_payload` и лампа станет вечно красной).
2. ЧИСЛА. Самая старая карточка обязана быть САМОЙ СТАРОЙ (ловушка 1: сортировка
   в `needs_attention` убывающая и режется `limit=50`, то есть на 51-й карточке
   самая старая ВЫПАДАЕТ и возраст будет занижен молча).
3. ПОРОГ. `stale_open` едет за `STALE_AFTER` из `tamapi_metrics`, а не за своим
   числом. Доказывается СДВИГОМ константы, а не совпадением значений сегодня.

Стенд поднят со слагом `yarina`, настоящим конфигом клиента и НАСТОЯЩИМИ
карточками эскалации намеренно: сторож на утечку, поднятый на пустом стенде,
зелен по построению — течь просто нечему.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util as _ilu
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.services.tamapi_metrics as M
from chatter.storage.db import Store

REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENTS_DIR = REPO_ROOT / "chatter" / "clients"
PANEL_CLIENT_SRC = REPO_ROOT / "app" / "panel_client.py"
OPS_PATH = REPO_ROOT / "scripts" / "ops_watchdog.py"

KEY = "yarina-panel-key"
SLUG = "yarina"
# Имя из настоящего конфига клиента — ровно та строка, которая живёт в шапке
# дашборда и потекла бы первой.
PERSONA = "Ярина"
HOUR = 3600.0
DAY = 86400.0

# Путь ручки НАЗВАН ЗДЕСЬ ЛИТЕРАЛОМ, а не выведен из константы watchdog'а:
# литерал и есть предмет договора (контракт §2, «путь: GET /ops/attention»).
# Равенство литерала константе пробы проверяется отдельным сторожем ниже —
# это два числа на одну вещь, и разъехаться они обязаны громко.
ATTENTION_PATH = "/ops/attention"

# Контракт §2: РОВНО эти четыре ключа и ничего больше.
EXPECTED_BODY_KEYS = {"open", "stale_open", "oldest_age_s", "oldest_wait_s"}


def _load_ops_watchdog():
    spec = _ilu.spec_from_file_location("ops_watchdog_att_handle", OPS_PATH)
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── стенд ──────────────────────────────────────────────────────────────────

def _seed(db_path, cards):
    """Открытые карточки эскалации: `cards` = [(contact_id, card_age, last_age)].

    `card_age=None` — флаг открыт, а строки в `console_cards` нет (живой случай:
    карточку показали до появления таблицы). `last_age=None` — входящих от лида
    нет вовсе.
    """
    now = time.time()
    s = Store(str(db_path))
    for i, (cid, card_age, last_age) in enumerate(cards):
        s.get_or_create_contact(cid)
        if last_age is not None:
            s.add_message(cid, "user", "текст ліда %d" % i, ts=now - last_age)
        if card_age is not None:
            s.add_card(msg_id=1000 + i, contact_id=cid, kind="escalation",
                       ts=now - card_age)
        s.set_runtime_flag("esc_active:%s" % cid, "bot:1:%d" % (1000 + i),
                           ts=now - (card_age or 0.0))
    del s
    return now


@pytest.fixture()
def stand(tmp_path, monkeypatch):
    """Фабрика инстанса Ярины: своя БД, свой слаг, свой ключ, свой конфиг."""
    def build(cards=()):
        db = tmp_path / "yarina.db"
        if db.exists():
            db.unlink()
        now = _seed(db, cards)

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
        return pc.build_app(), str(db), now
    return build


def _body(api):
    r = TestClient(api).get(ATTENTION_PATH)
    assert r.status_code == 200, (r.status_code, r.text[:400])
    return r.json()


# ── предпосылка: течь ЕСТЬ чему ────────────────────────────────────────────

def test_the_stand_really_carries_the_clients_identity(stand):
    """Без этого сторожа проверки на утечку ничего не доказывают."""
    api, db, _now = stand([("111:yarina", 3 * DAY, 2 * DAY)])
    c = TestClient(api)
    c.cookies.set("panels_key", KEY)
    body = c.get("/panel/tamapi").text
    assert PERSONA in body, "стенд не показывает имя клиента"
    assert SLUG in body, "стенд не показывает слаг клиента"
    assert db.endswith("yarina.db"), db


def test_the_stand_really_has_open_escalation_cards(stand):
    """Вторая предпосылка: числа, которые ручка обязана отдать, существуют.

    Меряется НЕ ручкой, а тем же `needs_attention`, которым панель красит блок
    «Требує вас»: иначе «ручка отдала 0» и «карточек нет» неотличимы, и все
    сторожа ниже зелены по построению.
    """
    api, db, now = stand([("111:yarina", 3 * DAY, 2 * DAY),
                          ("222:yarina", 5 * HOUR, 1 * HOUR)])
    del api
    items = M.needs_attention(db, now=now)
    assert len(items) == 2, items
    assert sum(1 for i in items if i["stale"]) == 1, items


# ── §3 форма 1: ручка отвечает без ключа ───────────────────────────────────

def test_the_handle_answers_without_any_key(stand):
    """У watchdog'а ключа клиента нет и быть не должно (§3 спеки)."""
    api, _db, _now = stand([("111:yarina", 3 * DAY, 2 * DAY)])
    r = TestClient(api).get(ATTENTION_PATH)
    assert r.status_code == 200, (r.status_code, r.text[:400])


def test_the_handle_is_not_a_redirect_to_the_login_form(stand):
    """Если ручка попадёт под `install_panel_auth_redirect`, проба увидит 200
    от СТРАНИЦЫ ЛОГИНА — то есть позеленеет на панели, которая мертва."""
    api, _db, _now = stand([("111:yarina", 3 * DAY, 2 * DAY)])
    r = TestClient(api).get(ATTENTION_PATH, follow_redirects=False)
    assert r.status_code == 200, (r.status_code, r.headers.get("location"))
    assert "location" not in {k.lower() for k in r.headers}, dict(r.headers)
    assert "<form" not in r.text.lower(), r.text[:400]


def test_the_probe_and_the_handle_name_the_same_path():
    """Два места на одну строку: путь ручки здесь и `ATTENTION_PATH` у пробы.

    Меньшее из двух чисел гасит большее МОЛЧА: разъехавшись, проба получит 404
    («ручки ещё нет») на живой ручке и лампа станет вечно красной.
    """
    ow = _load_ops_watchdog()
    assert getattr(ow, "ATTENTION_PATH", None) == ATTENTION_PATH, (
        "проба ходит не на тот путь, который отдаёт инстанс: %r против %r"
        % (getattr(ow, "ATTENTION_PATH", None), ATTENTION_PATH))


# ── §2 контракта: ЦЕНА ОТВЕТА ──────────────────────────────────────────────

def test_the_body_has_exactly_these_four_keys(stand):
    """ЛИТЕРАЛЬНЫЙ пин формы в ОБЕ стороны (контракт §2).

    «Нет лишних» — это граница безопасности: ключ, добавленный «для удобства
    диагностики» (слаг, contact_id, имя, путь к базе), уедет постороннему, и
    заметить это будет некому. «Нет пропавших» — это граница наблюдаемости:
    проба объявит `bad_payload`, и лампа станет вечно красной, то есть фоном.
    """
    api, _db, _now = stand([("111:yarina", 3 * DAY, 2 * DAY)])
    body = _body(api)
    assert isinstance(body, dict), body
    got = set(body)
    assert got == EXPECTED_BODY_KEYS, (
        "форма ответа разошлась с контрактом.\n  лишние: %s\n  пропали: %s"
        % (sorted(got - EXPECTED_BODY_KEYS), sorted(EXPECTED_BODY_KEYS - got)))


@pytest.mark.parametrize("secret", [
    SLUG, PERSONA, "yarina.db", ".secrets", "111:yarina", "текст ліда",
    "esc_active",
])
def test_the_handle_leaks_nothing_about_the_client(stand, secret):
    """Ни слага, ни contact_id, ни имён, ни текстов, ни пути к базе.

    Разбито параметрами намеренно: одно упавшее имя обязано называть себя, а не
    тонуть в общем «в теле что-то не то».

    Тело берётся через `_body`, то есть с требованием 200: без него сторож
    ЗЕЛЕН ПО ПОСТРОЕНИЮ, пока ручки нет — в теле 404-го ответа секретов нет по
    той простой причине, что там нет ничего. Зелёный сторож на отсутствующей
    реализации почти всегда проверяет не то.
    """
    api, _db, _now = stand([("111:yarina", 3 * DAY, 2 * DAY)])
    r = TestClient(api).get(ATTENTION_PATH)
    assert r.status_code == 200, (
        "ручка не отвечает 200 — проверять на утечку нечего: %s %r"
        % (r.status_code, r.text[:200]))
    text = r.text
    assert secret.lower() not in text.lower(), (
        "`%s` отдал наружу «%s»: %r" % (ATTENTION_PATH, secret, text[:400]))


def test_the_numbers_are_plain_json_types(stand):
    """`open`/`stale_open` — целые, возрасты — числа или None.

    Строка вместо числа («3 карточки») прочтётся пробой как `bad_payload`, и
    новость «не читают» подменится новостью «ручка сломалась».
    """
    api, _db, _now = stand([("111:yarina", 3 * DAY, 2 * DAY)])
    body = _body(api)
    assert isinstance(body["open"], int) and not isinstance(body["open"], bool)
    assert isinstance(body["stale_open"], int)
    for k in ("oldest_age_s", "oldest_wait_s"):
        assert body[k] is None or isinstance(body[k], (int, float)), (k, body[k])


# ── §4: числа не пересчитываются заново ────────────────────────────────────

def test_open_counts_every_open_card(stand):
    api, _db, _now = stand([("111:yarina", 3 * DAY, 2 * DAY),
                            ("222:yarina", 5 * HOUR, 1 * HOUR),
                            ("333:yarina", 1 * HOUR, 0.5 * HOUR)])
    assert _body(api)["open"] == 3, _body(api)


def test_a_closed_card_is_not_counted(stand):
    """Флаг снят (`value` пуст) — карточка закрыта. Это ровно то определение
    открытости, по которому дедуплицируются карточки в пульте: веб, пульт и
    проба обязаны показывать ОДНО множество."""
    api, db, _now = stand([("111:yarina", 3 * DAY, 2 * DAY),
                           ("222:yarina", 5 * HOUR, 1 * HOUR)])
    del api
    s = Store(db)
    s.set_runtime_flag("esc_active:111:yarina", "", ts=time.time())
    del s

    import app.panel_client as pc
    body = _body(pc.build_app())
    assert body["open"] == 1, body


def test_stale_open_counts_only_the_stale_ones(stand):
    """Порог сегодня 48 ч: одна карточка старше, две моложе."""
    api, _db, _now = stand([("111:yarina", 3 * DAY, 2 * DAY),
                            ("222:yarina", 5 * HOUR, 1 * HOUR),
                            ("333:yarina", 47 * HOUR, 1 * HOUR)])
    body = _body(api)
    assert body["open"] == 3, body
    assert body["stale_open"] == 1, body


def test_no_open_cards_gives_zeros_and_nulls(stand):
    """Пусто — это законное состояние, а не отсутствие ответа. `None` в
    возрастах, а НЕ 0: ноль секунд прочитался бы как «карточка только что»."""
    api, _db, _now = stand([])
    body = _body(api)
    assert body["open"] == 0 and body["stale_open"] == 0, body
    assert body["oldest_age_s"] is None, body
    assert body["oldest_wait_s"] is None, body


def test_a_card_without_a_console_row_is_open_but_has_no_invented_age(stand):
    """Флаг открыт, строки в `console_cards` нет: `card_ts` неизвестен.

    Возраст в этом случае неоткуда взять, и придумать его (например, из `ts`
    флага) значило бы отдать число, за которым ничего не стоит.
    """
    api, _db, _now = stand([("111:yarina", None, 2 * HOUR)])
    body = _body(api)
    assert body["open"] == 1, body
    assert body["oldest_age_s"] is None, body


def test_oldest_wait_belongs_to_the_same_card_as_oldest_age(stand):
    """§4.2: возрастов ДВА, и оба — про ОДНУ карточку, самую старую.

    Старый `card_ts` при свежем `last_ts` — лид ещё пишет, а владелец не
    пришёл; старый при старом — лид уже ушёл. Смешать возрасты разных карточек
    значит выдать третью, не существующую ситуацию.
    """
    api, _db, _now = stand([
        ("old:yarina", 10 * DAY, 1 * HOUR),    # самая старая карточка, лид свеж
        ("new:yarina", 1 * DAY, 9 * DAY),      # карточка моложе, лид давно молчит
    ])
    body = _body(api)
    assert body["oldest_age_s"] == pytest.approx(10 * DAY, abs=300), body
    assert body["oldest_wait_s"] == pytest.approx(1 * HOUR, abs=300), (
        "`oldest_wait_s` взят у ДРУГОЙ карточки: два возраста обязаны "
        "описывать одну и ту же самую старую карточку; %r" % (body,))


def test_oldest_wait_is_none_when_the_lead_never_wrote(stand):
    api, _db, _now = stand([("silent:yarina", 3 * DAY, None)])
    body = _body(api)
    assert body["oldest_age_s"] == pytest.approx(3 * DAY, abs=300), body
    assert body["oldest_wait_s"] is None, body


# ── ЛОВУШКА 1: `limit` режет САМЫЕ СТАРЫЕ ──────────────────────────────────

def test_the_oldest_card_survives_more_than_fifty_open_ones(stand):
    """🔴 ГЛАВНАЯ ЛОВУШКА ЧАСТИ 1, и она молчалива.

    `needs_attention` сортирует `card_ts` УБЫВАЮЩЕ и возвращает `out[:limit]`
    при `limit=50` по умолчанию. То есть на 51-й открытой карточке САМАЯ
    СТАРАЯ выпадает из выборки, и ручка отдаст возраст более молодой — занизив
    его молча и ровно в том случае, ради которого проба заведена.

    Стенд: 60 открытых карточек, возрасты 1..59 суток плюс одна 300-суточная.
    Сторож требует и правильный счёт (`open == 60`, а не 50), и правильный
    возраст.

    ⚠️ АРИФМЕТИКА НАЗВАНА ВСЛУХ, а не подогнана. Протухших РОВНО 59, а не 60:
    самая младшая карточка ровно суточная, а порог — двое суток. Поэтому здесь
    стоит не голое число, а предпосылка про порог и разность `open -
    stale_open == 1`. Так сторож начинает ловить ещё и СПОЛЗАНИЕ порога:
    сдвинь `STALE_AFTER` ниже суток — и предпосылка покраснеет вслух, а не
    счёт молча съедет на 60.
    """
    cards = [("c%02d:yarina" % i, (i + 1) * DAY, 1 * HOUR) for i in range(59)]
    cards.append(("ancient:yarina", 300 * DAY, 1 * HOUR))
    api, _db, _now = stand(cards)

    # Граница НЕ строгая справа намеренно: `needs_attention` сравнивает
    # `now - card_ts > STALE_AFTER`, а `now` у ручки берётся ПОЗЖЕ, чем
    # засеян стенд. Двухсуточная карточка старше порога на эту дельту и
    # протухшей считается; ровно поэтому не протухла только суточная.
    assert 1 * DAY < M.STALE_AFTER <= 2 * DAY, (
        "предпосылка стенда: порог лежит между суточной и двухсуточной "
        "карточкой, и ровно поэтому младшая из шестидесяти не протухла; "
        "STALE_AFTER = %r" % (M.STALE_AFTER,))

    body = _body(api)
    assert body["open"] == 60, (
        "открытых карточек посчитано %r вместо 60 — счёт срезан `limit`ом "
        "выборки, и владелец узнает про меньшую беду, чем есть" % (body["open"],))
    assert body["oldest_age_s"] == pytest.approx(300 * DAY, abs=600), (
        "самая старая карточка выпала из выборки: `limit` режет ОТСОРТИРОВАННЫЙ "
        "убывающе список, то есть отрезает именно её; %r" % (body,))
    assert body["stale_open"] == 59, (
        "протухших обязано быть 59: из шестидесяти открытых порога не берёт "
        "РОВНО ОДНА — суточная, при пороге в двое суток; %r" % (body,))
    assert body["open"] - body["stale_open"] == 1, (
        "не протухла не одна карточка, а %d: либо порог поехал, либо "
        "`stale_open` считается не тем множеством, что `open`"
        % (body["open"] - body["stale_open"],))


# ── §4.1 / §9 п. 4: порог берётся из STALE_AFTER ───────────────────────────

def test_the_verdict_moves_with_the_STALE_AFTER_constant(stand, monkeypatch):
    """🔴 СТОРОЖ НА РАЗМЕТКУ, а не на совпадение чисел сегодня.

    Порог `STALE_AFTER` живёт в `tamapi_metrics` и уже красит блок «Требує
    вас». Своего числа ни у ручки, ни у пробы нет: правится ОДНА константа, и
    обе стороны едут за ней. Сторож двигает константу и ждёт, что поедет
    вердикт; если не поедет — порог вписан ВТОРЫМ числом, и разъедутся они в
    день, когда владелец попросит «не двое суток, а сутки».
    """
    api, _db, _now = stand([("111:yarina", 10 * HOUR, 1 * HOUR)])

    before = _body(api)
    assert before["open"] == 1 and before["stale_open"] == 0, (
        "предпосылка: при пороге 48 ч карточка возрастом 10 ч не протухла; %r"
        % (before,))

    monkeypatch.setattr(M, "STALE_AFTER", 1 * HOUR)
    after = _body(api)
    assert after["open"] == 1, after
    assert after["stale_open"] == 1, (
        "порог сдвинут в 1 час, а вердикт не поехал: значит `stale_open` "
        "считается СВОИМ числом, а не тем, которым панель красит экран; %r"
        % (after,))


def test_the_handle_holds_no_threshold_of_its_own():
    """Структурная половина того же: своего числа у ручки нет.

    Считается ИСХОДНИК по AST, а не текст: комментарий, объясняющий, ПОЧЕМУ
    порога здесь быть не должно, — объяснение, а не порог (буква «И» в
    комментарии уже делала пойманную мутацию слепой).
    """
    tree = ast.parse(PANEL_CLIENT_SRC.read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "STALE_AFTER" not in names, (
        "ручка сама принимает решение «протухло или нет»: контракт §2 требует "
        "брать готовый `stale` из `needs_attention`, иначе решений о протухании "
        "становится ДВА и они разъедутся")
    numbers = {n.value for n in ast.walk(tree)
               if isinstance(n, ast.Constant) and isinstance(n.value, (int, float))
               and not isinstance(n.value, bool)}
    assert 172800 not in numbers and 172800.0 not in numbers, (
        "порог 48 ч вписан в ручку вторым числом")
    assert 48 not in numbers, "порог 48 ч вписан в ручку вторым числом"


def test_the_counting_is_not_reimplemented():
    """Второй реализации подсчёта не появляется (§4 спеки).

    Иначе панель и проба начнут спорить о том, сколько карточек открыто, — и
    спор этот будет тихим: обе стороны уверены в своём числе.
    """
    src = PANEL_CLIENT_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    called = {n.func.attr if isinstance(n.func, ast.Attribute) else
              getattr(n.func, "id", None)
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "needs_attention" in called, (
        "ручка не зовёт `needs_attention` — значит считает сама")
    assert "esc_active" not in src, (
        "ручка перебирает флаги эскалации сама: это ВТОРАЯ реализация "
        "подсчёта, и она разойдётся с панелью молча")


def test_the_counting_call_passes_an_explicit_large_limit():
    """ЛОВУШКА 1 названа и в исходнике, а не только в поведении.

    Поведенческий сторож выше ловит её на стенде с 60 карточками; этот стоит
    затем, чтобы возврат умолчания `limit=50` («ну кто заведёт 51 карточку»)
    не прошёл при пустоватом стенде. Прецедент есть: `active_dialogs` зовёт
    `dialog_feed(..., limit=100_000)` ровно по этой причине.
    """
    tree = ast.parse(PANEL_CLIENT_SRC.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and (getattr(n.func, "attr", None) == "needs_attention"
                  or getattr(n.func, "id", None) == "needs_attention")]
    assert calls, "вызова `needs_attention` в ручке нет вовсе"
    for call in calls:
        kw = {k.arg: k.value for k in call.keywords}
        assert "limit" in kw, (
            "`limit` не передан явно — умолчание 50 отрежет самые старые "
            "карточки, то есть ровно те, ради которых проба заведена")
        val = kw["limit"]
        if isinstance(val, ast.Constant) and isinstance(val.value, int):
            assert val.value > 1000, (
                "`limit=%r` мал: он обязан быть заведомо больше любого "
                "мыслимого числа открытых карточек" % (val.value,))


# ── контракт §2: ручка вешается НА ПРИЛОЖЕНИЕ, а не третьим роутером ───────

EXPECTED_ROUTES = {
    ("/health", ("GET",)),
    (ATTENTION_PATH, ("GET",)),
    # Третья и четвёртая ручки, арка «панель учится отправлять»
    # (спека 2026-08-25-panel-sends.md, ОК владельца 25.08). `/ops/outgoing`
    # — немая, как `/ops/attention`, и по той же причине: у watchdog ключа
    # клиента нет и быть не должно. `/api/outgoing` — ЗА `require_owner`:
    # это единственная ручка инстанса, которая ПИШЕТ, и открытой ей быть
    # нельзя. Список правится РУКАМИ намеренно.
    ("/api/outgoing", ("POST",)),
    ("/api/outgoing/dismiss", ("POST",)),
    ("/ops/outgoing", ("GET",)),
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
        methods = tuple(m for m in methods if m != "HEAD")
        out.add((r.path, methods))
    return out


def test_the_app_offers_exactly_these_routes(stand):
    """В ОБЕ стороны: «нет лишних» ловит ручку, приехавшую попутно с новой
    (состав приложения — граница безопасности, ключ уходит стороннему
    человеку); «нет пропавших» ловит починку, сделанную сносом дашборда."""
    api, _db, _now = stand([("111:yarina", 3 * DAY, 2 * DAY)])
    got = _routes(api)
    assert got == EXPECTED_ROUTES, (
        "состав маршрутов инстанса разошёлся с контрактом.\n"
        "  лишние: %s\n  пропали: %s"
        % (sorted(got - EXPECTED_ROUTES), sorted(EXPECTED_ROUTES - got)))


def test_the_handle_did_not_arrive_as_a_third_router():
    """Контракт §2 дословно: `@api.get(...)` ПРЯМО НА ПРИЛОЖЕНИЕ, рядом с
    `/health`, а НЕ третьим роутером.

    Правило не косметическое и записано в самом `panel_client.py`: состав
    роутеров — предмет отдельного сторожа, и живость не имеет права его
    менять. Роутер тянет за собой ВСЁ, что на нём висит, — то есть открывает
    состав приложения опечаткой в чужом файле.
    """
    tree = ast.parse(PANEL_CLIENT_SRC.read_text(encoding="utf-8"))
    mounts = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
              and getattr(n.func, "attr", None) == "include_router"]
    assert len(mounts) == 2, (
        "роутеров смонтировано %d, а договорено ДВА (вход и дашборд): новая "
        "ручка приехала третьим роутером" % len(mounts))


def test_the_dashboard_is_still_closed_without_the_key(stand):
    """ГРАНИЦА. Немую ручку можно «сделать», сняв авторизацию со всего
    приложения — и все сторожа выше позеленеют, отдав переписку клиента
    любому, кто дотянулся до порта."""
    api, _db, _now = stand([("111:yarina", 3 * DAY, 2 * DAY)])
    assert TestClient(api).get("/panel/tamapi").status_code == 401


def test_the_dashboard_still_answers_with_the_key(stand):
    """Парная граница: авторизация не имеет права сломаться от новой ручки."""
    api, _db, _now = stand([("111:yarina", 3 * DAY, 2 * DAY)])
    c = TestClient(api)
    c.cookies.set("panels_key", KEY)
    assert c.get("/panel/tamapi").status_code == 200


def test_health_still_says_exactly_one_thing(stand):
    """Соседняя ручка не имеет права поехать за компанию: на её форме стоит
    отдельный сторож соседней арки, и молчаливое расширение `/health` было бы
    той же утечкой, только в другом файле."""
    api, _db, _now = stand([("111:yarina", 3 * DAY, 2 * DAY)])
    assert TestClient(api).get("/health").json() == {"ok": True}


def test_the_handle_did_not_drag_in_background_tasks(stand):
    """Ни одного startup-обработчика: второй `_watchdog_loop` — это второй
    хозяин у живого бота (DEV-38, 13 ч 42 мин простоя Ольги 16.08)."""
    api, _db, _now = stand([("111:yarina", 3 * DAY, 2 * DAY)])
    assert not api.router.on_startup, api.router.on_startup
    assert not api.router.on_shutdown, api.router.on_shutdown


def test_the_api_schema_is_still_closed(stand):
    """Карта ручек закрыта (`openapi_url=None`): новая неаутентифицированная
    ручка не имеет права приоткрыть перечень остальных."""
    api, _db, _now = stand([("111:yarina", 3 * DAY, 2 * DAY)])
    assert api.openapi_url is None, api.openapi_url
    assert TestClient(api).get("/openapi.json").status_code != 200
