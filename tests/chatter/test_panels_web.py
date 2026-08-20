"""Веб-слой панелей: auth, отсутствие секретов во фронте, read-only фазы 0."""
from __future__ import annotations

import importlib
import os
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from chatter.storage.db import Store

KEY = "test-owner-key"
NOW = 1_800_000_000.0
DAY = 86400.0


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "p.db"
    s = Store(str(db))
    s.get_or_create_contact("777:volska")
    s.add_message("777:volska", "user", "скільки коштує SMM?", ts=NOW - DAY)
    s.add_message("777:volska", "assistant", "750–900 $ за місяць", ts=NOW - DAY + 60)
    s.add_card(msg_id=5, contact_id="777:volska", kind="escalation", ts=NOW - DAY)
    s.set_runtime_flag("esc_active:777:volska", "bot:1:5", ts=NOW - DAY)
    s.record_transition("777:volska", from_state="qualifying", to_state="hot",
                        signal="interested", ts=NOW - DAY)
    del s

    monkeypatch.setenv("JARVIS_PANELS_KEY", KEY)
    monkeypatch.setenv("TAMAPI_DB", str(db))
    monkeypatch.setenv("TAMAPI_HEARTBEAT", str(tmp_path / "nope.txt"))

    import app.routers.panels_auth as pa
    import app.routers.tamapi_dashboard as td
    import app.routers.jarvis_panel as jp
    for m in (pa, td, jp):
        importlib.reload(m)

    api = FastAPI()
    api.include_router(pa.router)
    api.include_router(td.router)
    api.include_router(jp.router)
    # Как в проде: обработчик отказа ставится там же, где монтируются роутеры.
    # Что об этом не забыли в `app/main.py`, держит отдельный сторож ниже.
    pa.install_panel_auth_redirect(api)
    return TestClient(api), str(db)


def test_panels_require_owner_key(client):
    c, _ = client
    assert c.get("/panel/tamapi").status_code == 401
    assert c.get("/panel/jarvis").status_code == 401


def test_wrong_key_is_rejected(client):
    c, _ = client
    assert c.get("/panel/tamapi", headers={"X-Panels-Key": "nope"}).status_code == 401


def test_main_screen_renders_with_key(client):
    c, _ = client
    r = c.get("/panel/tamapi", headers={"X-Panels-Key": KEY})
    assert r.status_code == 200
    assert "Требує вас" in r.text


def test_dynamics_renders_and_caps_at_three_metrics(client):
    c, _ = client
    r = c.get("/panel/tamapi/dynamics?m=dialogs&m=qualified&m=handed&m=payments",
              headers={"X-Panels-Key": KEY})
    assert r.status_code == 200
    # Четвёртая метрика не имеет права попасть в выбор: 4 линии не читаются.
    assert r.text.count("tile on") <= 3


def test_no_secrets_reach_the_frontend(client, monkeypatch):
    """Железное ограничение владельца: секретов во фронте ноль."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SHOULD-NEVER-APPEAR")
    c, _ = client
    for url in ("/panel/tamapi", "/panel/tamapi/dynamics", "/panel/jarvis"):
        body = c.get(url, headers={"X-Panels-Key": KEY}).text
        assert "SHOULD-NEVER-APPEAR" not in body
        assert KEY not in body, "ключ панели утёк в разметку"
        assert not re.search(r"sk-ant-[A-Za-z0-9]", body)


def test_jarvis_panel_has_no_mutating_controls(client):
    """Фаза 0 read-only: ни формы, ни POST-кнопки на странице."""
    c, _ = client
    body = c.get("/panel/jarvis", headers={"X-Panels-Key": KEY}).text
    assert "<form" not in body.lower()
    assert "method='post'" not in body.lower()
    for word in ("рестарт", "restart", "kill", "ротировать ключ"):
        assert f">{word}" not in body.lower()


def test_jarvis_panel_states_external_watchdog_is_absent(client, monkeypatch):
    """§0 спеки: панель обязана говорить о собственной слепоте."""
    monkeypatch.delenv("HEALTHCHECKS_URL", raising=False)
    c, _ = client
    body = c.get("/panel/jarvis", headers={"X-Panels-Key": KEY}).text
    assert "Внешний сторож" in body
    assert "НЕ настроен" in body


def test_action_goes_through_shared_command_layer(client):
    """Веб не пишет в БД напрямую — только через route_callback."""
    c, db = client
    r = c.post("/panel/tamapi/action",
               data={"data": "paidamt:750:777:volska", "event_token": "tok-web-1"},
               headers={"X-Panels-Key": KEY})
    assert r.status_code == 200

    s = Store(db)
    pays = s.payments_between(0.0, 1e12)
    assert len(pays) == 1 and pays[0]["amount_minor"] == 75000
    assert pays[0]["dedup_key"] == "panel:tok-web-1"
    assert s.get_or_create_contact("777:volska")["state"] == "closed"


def test_panel_payment_without_a_token_is_refused(client):
    """Личность события — обязанность вызывателя. Прежде панель её не слала, и
    все её оплаты по одному контакту схлопывались на сентинеле `0` в одну
    строку. Отказ громкий: тихая запись «как-нибудь» стоила бы выручки."""
    c, db = client
    r = c.post("/panel/tamapi/action", data={"data": "paidamt:750:777:volska"},
               headers={"X-Panels-Key": KEY})
    assert r.status_code == 200

    s = Store(db)
    assert s.payments_between(0.0, 1e12) == []
    assert s.get_or_create_contact("777:volska")["state"] != "closed", (
        "воронка закрыта оплатой, которой не было")


def test_pause_and_resume_both_work_from_web(client):
    """Снятие паузы обязано работать из веба: инцидент P15 запер владельца в TG
    (kill_off снимался только /start в Saved Messages)."""
    c, db = client
    # `confirm` последним токеном: одиночный stop_all теперь ничего не взводит,
    # и проверка этого — отдельный тест ниже.
    c.post("/panel/tamapi/action", data={"data": "stop_all confirm"},
           headers={"X-Panels-Key": KEY})
    s = Store(db)
    assert s.get_runtime_flag("kill_switch") == "1"
    del s

    c.post("/panel/tamapi/action", data={"data": "resume_all"},
           headers={"X-Panels-Key": KEY})
    s = Store(db)
    assert s.get_runtime_flag("kill_switch") == "0"


def test_metric_without_history_is_labelled_not_zeroed(client):
    c, _ = client
    body = c.get("/panel/tamapi/dynamics?m=payments", headers={"X-Panels-Key": KEY}).text
    assert "історія накопичується" in body


# ── ручка входа: ФОРМА с полем пароля, а не ссылка с ключом ───────────────
# Гейт принимает ключ в заголовке ИЛИ в cookie, но заголовок с мобильного
# браузера не отправить, поэтому cookie ставит отдельная дверь.
#
# Дверь была ссылкой `?key=…`. Ключ в query оседает в истории браузера, в
# адресной строке на скриншоте, в логах любого прокси по пути и в Referer.
# Форма шлёт тот же ключ телом POST: cookie ставится так же, а следов не
# остаётся ни одного. Ключ из query после этой замены не работает ВООБЩЕ —
# иначе старая ссылка осталась бы действующей дверью.

def test_login_page_is_a_password_form(client):
    c, _ = client
    r = c.get("/panel/login")
    assert r.status_code == 200
    body = r.text.lower()
    assert "method='post'" in body
    assert "type='password'" in body
    assert "name='key'" in body


def test_a_valid_key_in_the_url_no_longer_logs_anybody_in(client):
    """Суть замены: даже ВЕРНЫЙ ключ из query больше не открывает дверь.
    Старая ссылка из истории телефона должна приводить к форме, а не внутрь."""
    c, _ = client
    r = c.get("/panel/login", params={"key": KEY}, follow_redirects=False)
    assert r.status_code == 200
    assert "set-cookie" not in {k.lower() for k in r.headers}
    assert c.get("/panel/tamapi").status_code == 401


def test_posting_the_right_key_sets_the_cookie(client):
    c, _ = client
    r = c.post("/panel/login", data={"key": KEY}, follow_redirects=False)
    assert r.status_code == 303
    assert r.cookies.get("panels_key") == KEY


def test_the_cookie_is_httponly_samesite_strict_and_panel_scoped(client):
    """HttpOnly — чтобы ключ не достался скрипту на странице; SameSite=Strict —
    чтобы чужой сайт не мог дёрнуть панель от твоего имени; Path=/panel — чтобы
    ключ не уезжал на остальные ручки бэкенда с каждым запросом."""
    c, _ = client
    r = c.post("/panel/login", data={"key": KEY}, follow_redirects=False)
    raw = r.headers.get("set-cookie", "").lower()
    assert "httponly" in raw
    assert "samesite=strict" in raw
    assert "path=/panel" in raw


def test_after_login_the_panel_opens_without_any_header(client):
    """Ровно то, ради чего дверь нужна: телефон дальше ходит по cookie."""
    c, _ = client
    c.post("/panel/login", data={"key": KEY})
    assert c.get("/panel/tamapi").status_code == 200


def test_a_wrong_key_is_refused_and_sets_nothing(client):
    c, _ = client
    r = c.post("/panel/login", data={"key": "не тот"}, follow_redirects=False)
    assert r.status_code == 401
    assert "set-cookie" not in {k.lower() for k in r.headers}
    assert c.get("/panel/tamapi").status_code == 401


def test_no_key_at_all_is_refused_and_sets_nothing(client):
    """Пустая форма — это отказ, а не 422: 422 отличается от 401 и подсказывает
    тому, кто подбирает, что поле вообще существует."""
    c, _ = client
    r = c.post("/panel/login", data={}, follow_redirects=False)
    assert r.status_code == 401
    assert "set-cookie" not in {k.lower() for k in r.headers}


def test_the_key_is_not_echoed_back_in_the_body(client):
    """Отказ не имеет права вернуть введённое значением поля: страница уедет в
    скриншот и в кэш браузера вместе с ним."""
    c, _ = client
    assert KEY not in c.get("/panel/login", params={"key": KEY}).text
    r = c.post("/panel/login", data={"key": KEY}, follow_redirects=False)
    assert KEY not in r.text
    assert KEY not in c.post("/panel/login", data={"key": KEY + "x"}).text


def test_the_redirect_target_cannot_be_an_arbitrary_site(client):
    """Открытый редирект на ручке входа — это фишинг с твоего же домена."""
    c, _ = client
    r = c.post("/panel/login", data={"key": KEY, "next": "https://evil.example"},
               follow_redirects=False)
    assert r.headers.get("location", "").startswith("/panel/")


def test_the_redirect_target_may_pick_the_jarvis_panel(client):
    c, _ = client
    r = c.post("/panel/login", data={"key": KEY, "next": "/panel/jarvis"},
               follow_redirects=False)
    assert r.headers["location"] == "/panel/jarvis"


def test_the_form_carries_a_whitelisted_next_and_drops_the_rest(client):
    """`next` доезжает до POST скрытым полем. Через него в разметку попадает
    строка из запроса — поэтому в форму кладётся только значение из белого
    списка, а не то, что прислали."""
    c, _ = client
    assert "/panel/jarvis" in c.get("/panel/login", params={"next": "/panel/jarvis"}).text
    body = c.get("/panel/login", params={"next": "https://evil.example"}).text
    assert "evil.example" not in body


def test_login_is_dead_while_panels_are_disabled(monkeypatch):
    """Без ключа в окружении роутеры не монтируются вовсе — ручка входа не
    имеет права быть исключением, иначе она станет единственной открытой
    дверью в выключенной панели."""
    import importlib

    import app.routers.panels_auth as pa
    monkeypatch.delenv("JARVIS_PANELS_KEY", raising=False)
    importlib.reload(pa)
    assert pa.panels_enabled() is False


def test_a_non_ascii_key_is_a_refusal_not_a_crash(client):
    """Сравнение постоянным временем идёт по БАЙТАМ: на строках оно требует
    ASCII и бросало TypeError, то есть ключ с кириллицей давал 500 вместо 401.
    Отказ, отличимый от обычного, — подсказка тому, кто подбирает ключ.

    Тело формы кириллицу везёт штатно — в отличие от заголовка и cookie, куда
    её не положить: клиент отвергает такой запрос сам, до сервера."""
    c, _ = client
    r = c.post("/panel/login", data={"key": "ключ"}, follow_redirects=False)
    assert r.status_code == 401


# ── stop_all: глобальная заглушка не взводится одним POST ─────────────────
# Модалка в вебе — защита КЛИЕНТСКАЯ: одиночный запрос мимо неё взводил флаг,
# от которого «бот молчит на всех» (P15). На телефоне это один промах пальцем.

def _flag(db):
    s = Store(db)
    try:
        return s.get_runtime_flag("kill_switch")
    finally:
        del s


def test_a_single_post_does_not_arm_the_global_mute(client):
    c, db = client
    c.cookies.set("panels_key", KEY)
    r = c.post("/panel/tamapi/action", data={"data": "stop_all"})
    assert r.status_code == 200
    assert _flag(db) != "1", "глобальная заглушка взведена одним запросом"


def test_the_first_post_asks_for_confirmation_out_loud(client):
    """Молчаливый отказ неотличим от «сделано» (DEV-18): владелец решил бы, что
    бот остановлен, и ушёл спать."""
    c, _ = client
    c.cookies.set("panels_key", KEY)
    r = c.post("/panel/tamapi/action", data={"data": "stop_all"})
    body = r.json()
    assert body.get("confirm") is True
    assert "підтверд" in body.get("feedback", "").casefold()


def test_the_confirmed_post_arms_it(client):
    c, db = client
    c.cookies.set("panels_key", KEY)
    r = c.post("/panel/tamapi/action", data={"data": "stop_all confirm"})
    assert r.status_code == 200
    assert _flag(db) == "1"


def test_resume_needs_no_confirmation(client):
    """Симметрии здесь быть НЕ должно: подтверждать «включить обратно» значит
    ставить лишний барьер на пути из аварии (P15 — kill_off только через TG)."""
    c, db = client
    c.cookies.set("panels_key", KEY)
    c.post("/panel/tamapi/action", data={"data": "stop_all confirm"})
    assert _flag(db) == "1"
    c.post("/panel/tamapi/action", data={"data": "resume_all"})
    assert _flag(db) == "0"


# ── имена лидов вместо голого id ──────────────────────────────────────────
# Голый id — признак того, что о человеке НЕ известно ничего. На живом дриле
# оператор не смог возобновить диалог, увидев в карточке одно число.
# В ЛОГАХ остаётся хеш: там имя было бы утечкой в файл, который читают шире.

def test_the_feed_shows_the_name_when_it_is_known(client):
    c, db = client
    s = Store(db)
    s.set_display_name("777:volska", "Олена Ковальчук")
    del s
    body = c.get("/panel/tamapi", headers={"X-Panels-Key": KEY}).text
    assert "Олена Ковальчук" in body


def test_without_a_name_the_id_is_still_shown(client):
    """Fallback остаётся: неизвестное имя — это не повод показать пустоту."""
    c, _ = client
    body = c.get("/panel/tamapi", headers={"X-Panels-Key": KEY}).text
    assert "777" in body


def test_a_name_is_escaped_before_it_reaches_the_page(client):
    """Имя — ПОЛЬЗОВАТЕЛЬСКИЙ текст: человек вписывает в профиль что угодно."""
    c, db = client
    s = Store(db)
    s.set_display_name("777:volska", "<script>alert(1)</script>")
    del s
    body = c.get("/panel/tamapi", headers={"X-Panels-Key": KEY}).text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_the_runner_remembers_the_name_on_first_contact(tmp_path):
    """Резолв имени требует Telethon-сущности, которой у веба нет. Значит имя
    обязан положить в БД тот, у кого она есть, — раннер, и один раз."""
    from chatter.telethon_run import remember_display_name

    class _Sender:
        first_name, last_name, username = "Олена", "Ковальчук", "olena"

    s = Store(str(tmp_path / "r.db"))
    s.get_or_create_contact("777:volska")
    remember_display_name(s, "777:volska", _Sender(), user_id=777)
    assert "Олена" in s.get_or_create_contact("777:volska")["display_name"]


def test_an_unknown_sender_does_not_erase_a_known_name(tmp_path):
    from chatter.telethon_run import remember_display_name

    class _Blank:
        first_name = last_name = username = None

    s = Store(str(tmp_path / "r2.db"))
    s.get_or_create_contact("777:volska")
    s.set_display_name("777:volska", "Олена")
    remember_display_name(s, "777:volska", _Blank(), user_id=777)
    assert s.get_or_create_contact("777:volska")["display_name"] == "Олена"


def test_remembering_the_name_never_breaks_the_turn(tmp_path):
    """Имя — украшение экрана, а не часть ответа лиду. Сбой здесь не имеет
    права уронить ход (DEV-18: ловим и логируем, не глотаем молча)."""
    from chatter.telethon_run import remember_display_name

    class _Boom:
        @property
        def first_name(self):
            raise RuntimeError("телетон моргнул")

    s = Store(str(tmp_path / "r3.db"))
    s.get_or_create_contact("777:volska")
    remember_display_name(s, "777:volska", _Boom(), user_id=777)   # не бросает


# ── два решения владельца, сделанные видимыми на экране ───────────────────

def test_qualified_is_labelled_as_the_assistants_opinion(client):
    """Решение 31.07: «квалифицировано» — мнение классификатора. Без подписи
    клиент прочтёт оценку модели как проверенный человеком факт, и первое же
    расхождение будет стоить доверия ко всему экрану."""
    c, _ = client
    body = c.get("/panel/tamapi", headers={"X-Panels-Key": KEY}).text
    assert "за оцінкою асистента" in body


def test_the_package_limit_is_five_hundred_by_default(client, monkeypatch):
    """Значение подтверждено владельцем. Прибито тестом намеренно: пакет —
    биллинговая величина, и её тихая смена меняет счёт клиенту."""
    monkeypatch.delenv("TAMAPI_PACKAGE", raising=False)
    import app.routers.tamapi_dashboard as td
    assert td._package()["limit"] == 500


def test_an_exhausted_package_does_not_mute_the_bot(client, monkeypatch):
    """Решение 31.07: исчерпание НЕ отключает. Молчащий бот теряет живого лида
    необратимо, в отличие от счёта за перерасход."""
    monkeypatch.setenv("TAMAPI_PACKAGE", "1")
    c, db = client
    body = c.get("/panel/tamapi", headers={"X-Panels-Key": KEY}).text
    assert body, "экран не отрисовался на исчерпанном пакете"
    assert (Store(db).get_runtime_flag("kill_switch") or "0") != "1", (
        "исчерпание пакета само взвело заглушку")
    assert "На паузі" not in body


def test_the_login_route_itself_is_dead_while_panels_are_disabled(tmp_path, monkeypatch):
    """Мало проверить флаг `panels_enabled` — надо ДЁРНУТЬ ручку. Открытая
    дверь в выключенной панели хуже, чем отсутствие двери (поймано мутацией:
    гейт снимался, а тест этого не видел, потому что ключ в фикстуре задан)."""
    import importlib

    monkeypatch.delenv("JARVIS_PANELS_KEY", raising=False)
    import app.routers.panels_auth as pa
    importlib.reload(pa)

    api = FastAPI()
    api.include_router(pa.router)
    c = TestClient(api)
    # Обе половины двери: и страница с формой, и приём формы. Закрыть одну и
    # оставить другую значит оставить дверь.
    r = c.get("/panel/login", follow_redirects=False)
    assert r.status_code == 503
    assert "set-cookie" not in {k.lower() for k in r.headers}
    r = c.post("/panel/login", data={"key": "что угодно"}, follow_redirects=False)
    assert r.status_code == 503
    assert "set-cookie" not in {k.lower() for k in r.headers}


# ─────────────────────── узкий экран: страница не едет вбок ──────────────────

def _rows_without_labels(html: str) -> list[str]:
    """Строки таблиц, где непервая ячейка осталась без `data-l`.

    На узком экране таблица перестраивается в карточку: заголовок колонки
    исчезает, и подпись значению даёт ТОЛЬКО `data-l`. Ячейка без него
    превращается в число без имени — «0.7» непонятно чего.
    """
    bad = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        if "<th" in row:
            continue
        cells = re.findall(r"<td([^>]*)>", row)
        if len(cells) < 2:
            continue                      # одна ячейка — подписывать нечего
        if any("colspan" in a for a in cells):
            continue                      # строка-заглушка «немає даних»
        for attrs in cells[1:]:
            if "data-l=" not in attrs:
                bad.append(row[:120])
                break
    return bad


@pytest.mark.parametrize("path", ["/panel/tamapi", "/panel/jarvis"])
def test_every_secondary_cell_carries_its_column_label(client, path):
    c, _ = client
    html = c.get(path, headers={"X-Panels-Key": KEY}).text
    bad = _rows_without_labels(html)
    assert not bad, (
        f"{path}: ячейки без data-l — в мобильной раскладке они безымянны: {bad}")


@pytest.mark.parametrize("path", ["/panel/tamapi", "/panel/jarvis"])
def test_table_headers_live_in_thead(client, path):
    """Перестройка гасит шапку через `thead`. Заголовок, оставленный голым
    `<tr><th>`, на телефоне превратится в столбик слов над карточками."""
    c, _ = client
    html = c.get(path, headers={"X-Panels-Key": KEY}).text
    for m in re.finditer(r"<table[^>]*>(.*?)</table>", html, re.S):
        t = m.group(1)
        if "<th" not in t:
            continue
        assert "<thead>" in t, f"{path}: таблица с <th> вне <thead>: {t[:120]}"


def test_narrow_screen_rules_are_present(client):
    """Сторож на сам медиазапрос: без него перестройка не включится ни на одном
    телефоне, а разметка при этом останется «правильной» и все тесты выше
    останутся зелёными."""
    from app.routers.panels_ui import CSS
    assert "@media(max-width:620px)" in CSS, "медиазапрос узкого экрана исчез"
    # Селектора мало: правило `::before{content:''}` оставляет селектор на
    # месте и гасит подписи — эта мутация пережила проверку на подстроку.
    assert "table td[data-l]::before{content:attr(data-l)" in CSS, (
        "подписи колонок в карточке исчезли — значения останутся без имён")
    # Именно ЭТО правило, а не любое `min-width:0` в файле: проверка на голую
    # подстроку пережила мутацию (правило снято, а подстрока осталась в
    # соседних селекторах) — то есть не охраняла ничего.
    flat = re.sub(r"\s+", "", CSS)
    # `.two>*` добавлен в заходе 1: двухколоночная раскладка панели Джарвиса —
    # такой же грид, и без ограничителя её колонка отказывается сжиматься уже
    # своего содержимого ровно так же, как раньше это делали карточки.
    assert ".row>*,.grid>*,.two>*,.funnel>*,.tile,.fstep{min-width:0}" in flat, (
        "снят ограничитель на flex/grid-элементы — страница снова поедет вбок")
    # `break-word` здесь не годится и молча оставит дефект: он НЕ участвует в
    # расчёте min-content, а ширину карточке диктует именно она. Приёмка 13.08:
    # одна ячейка ленты со строкой без пробелов распирала таблицу до 433 px при
    # окне 390 — значит сторожу мало «какой-нибудь перенос», нужен этот.
    assert "tabletd{display:block;border:none;padding:2px0;overflow-wrap:anywhere}" in flat, (
        "ячейка таблицы снова не умеет рвать длинное слово — машинная строка "
        "без пробелов распирает карточку шире экрана")


# ── /panel: короткий адрес для телефона ─────────────────────────────────────
# Даниил дважды попадал в корень API вместо панели, и в истории браузера
# оседал голый host:port. Указатель должен стоять там, куда промахиваются.

def _panel_root(c):
    """Голый /panel без следования за редиректом."""
    return c.get("/panel", follow_redirects=False)


def test_panel_root_sends_the_owner_to_the_dashboard(client):
    c, _ = client
    from app.routers.panels_auth import COOKIE_NAME
    c.cookies.set(COOKIE_NAME, KEY)
    r = _panel_root(c)
    assert r.status_code == 303
    assert r.headers["location"] == "/panel/tamapi"


def test_panel_root_sends_a_stranger_to_the_login_form(client):
    c, _ = client
    r = _panel_root(c)
    assert r.status_code == 303
    assert r.headers["location"] == "/panel/login"


def test_panel_root_with_a_stale_cookie_goes_to_the_form_not_a_dead_end(client):
    """Протухшая cookie — не «есть cookie». Проверять НАЛИЧИЕ, а не годность,
    значило бы уводить на панель, которая ответит 401: тупик, из которого с
    телефона не выбраться иначе как чисткой cookie вручную."""
    c, _ = client
    from app.routers.panels_auth import COOKIE_NAME
    # Значение ASCII: cookie едет заголовком, а httpx кодирует его latin-1 и
    # роняет запрос на кириллице ещё до приложения.
    c.cookies.set(COOKIE_NAME, "key-from-the-previous-rotation")
    r = _panel_root(c)
    assert r.status_code == 303
    assert r.headers["location"] == "/panel/login"


def test_panel_root_accepts_the_header_too(client):
    """Тем же ключом ходит приёмка (`panels_mobile_check`) — заголовком."""
    c, _ = client
    r = c.get("/panel", headers={"X-Panels-Key": KEY}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/panel/tamapi"


# ── 401 не должен быть тупиком ───────────────────────────────────────────────
# Разбор 13.08: с телефона ходят ТРИ браузера (Chrome mobile, Samsung Internet,
# Chrome в режиме «версия для ПК»). Cookie живёт в банке того браузера, где был
# вход; в остальных любая панель отдавала голый JSON `{"detail":"owner key
# required"}` — и выхода из него не было, ключ ввести негде. Набор адреса руками
# это не лечило: дело не в способе перехода, а в том, чем открыто.

HTML_ACCEPT = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


def test_browser_without_cookie_is_sent_to_the_login_form(client):
    c, _ = client
    r = c.get("/panel/jarvis", headers=HTML_ACCEPT, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/panel/login?next=%2Fpanel%2Fjarvis"


def test_browser_without_cookie_on_the_client_panel_too(client):
    """Отказывала не одна панель — к концу вечера и TAMAPI отдавала 401."""
    c, _ = client
    r = c.get("/panel/tamapi", headers=HTML_ACCEPT, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/panel/login?next=%2Fpanel%2Ftamapi"


def test_a_machine_client_still_gets_401_and_not_a_redirect(client):
    """Приёмка ходит заголовком и ждёт 401 на неверном ключе. Редирект вместо
    отказа превратил бы её красный в зелёный — сторож молчал бы о поломке."""
    c, _ = client
    r = c.get("/panel/jarvis", follow_redirects=False)          # Accept: */*
    assert r.status_code == 401
    r2 = c.get("/panel/jarvis", headers={"X-Panels-Key": "wrong"},
               follow_redirects=False)
    assert r2.status_code == 401


def test_a_post_without_cookie_is_refused_not_redirected(client):
    """Редирект на форму потерял бы тело действия: человек нажал «оплачено», а
    вернулся бы на пустую форму и решил, что оплата записана."""
    c, _ = client
    r = c.post("/panel/tamapi/action", data={"data": "paidamt:750:777:volska"},
               headers=HTML_ACCEPT, follow_redirects=False)
    assert r.status_code == 401


def test_the_return_target_stays_inside_the_whitelist(client):
    """`next` уходит в ту же проверку белого списка, что и форма входа: иначе
    ручка отказа становится генератором ссылок «куда угодно»."""
    c, _ = client
    r = c.get("/panel/jarvis/api/snapshot", headers=HTML_ACCEPT,
              follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/panel/login?next=%2Fpanel%2Ftamapi"


# ── связка панелей ──────────────────────────────────────────────────────────
# Ссылки между панелями не было вовсе: путь на ферму приходилось набирать
# руками, а руками его набирают в том браузере, который под рукой, — то есть
# в чужой банке cookie. Связка убирает сам повод набирать адрес.

def test_owner_panel_links_to_the_farm(client):
    """Приложение ЗДЕСЬ — владельческое: фикстура монтирует и `tamapi`, и
    `jarvis`. Имя теста это говорит вслух, потому что прежнее («client panel»)
    обещало то, чего тест не проверял: на инстансе клиента фермы нет, ссылка
    вела в 404, и покраснеть здесь было нечему — ссылка проверялась на
    приложении, где маршрут есть. Тупик держит
    `tests/test_panel_client_no_farm_link.py`, собирающий НАСТОЯЩЕЕ клиентское
    приложение; этот тест — парный ему, в обратную сторону.
    """
    c, _ = client
    r = c.get("/panel/tamapi", headers={"X-Panels-Key": KEY})
    assert r.status_code == 200
    assert "href='/panel/jarvis'" in r.text
    assert c.get("/panel/jarvis",
                 headers={"X-Panels-Key": KEY}).status_code == 200


def test_farm_links_back_to_the_client_panel(client):
    c, _ = client
    r = c.get("/panel/jarvis", headers={"X-Panels-Key": KEY})
    assert r.status_code == 200
    assert "href='/panel/tamapi'" in r.text
