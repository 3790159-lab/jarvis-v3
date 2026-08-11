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
    for word in ("рестарт", "restart", "kill", "ротувати ключ"):
        assert f">{word}" not in body.lower()


def test_jarvis_panel_states_external_watchdog_is_absent(client, monkeypatch):
    """§0 спеки: панель обязана говорить о собственной слепоте."""
    monkeypatch.delenv("HEALTHCHECKS_URL", raising=False)
    c, _ = client
    body = c.get("/panel/jarvis", headers={"X-Panels-Key": KEY}).text
    assert "Зовнішній сторож" in body
    assert "НЕ налаштований" in body


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


# ── ручка входа: единственный способ попасть в панель с телефона ──────────
# Гейт принимает ключ в заголовке ИЛИ в cookie, но заголовок с мобильного
# браузера не отправить. Пока ручки не было, cookie ставить было нечем — и
# «панель работает с телефона» держалось на расширении к браузеру.

def test_login_with_the_right_key_sets_the_cookie(client):
    c, _ = client
    r = c.get("/panel/login", params={"key": KEY}, follow_redirects=False)
    assert r.status_code in (302, 303)
    assert r.cookies.get("panels_key") == KEY


def test_the_cookie_is_httponly_and_samesite_strict(client):
    """HttpOnly — чтобы ключ не достался скрипту на странице; SameSite=Strict —
    чтобы чужой сайт не мог дёрнуть панель от твоего имени."""
    c, _ = client
    r = c.get("/panel/login", params={"key": KEY}, follow_redirects=False)
    raw = r.headers.get("set-cookie", "").lower()
    assert "httponly" in raw
    assert "samesite=strict" in raw


def test_after_login_the_panel_opens_without_any_header(client):
    """Ровно то, ради чего ручка нужна: телефон дальше ходит по cookie."""
    c, _ = client
    c.get("/panel/login", params={"key": KEY})
    assert c.get("/panel/tamapi").status_code == 200


def test_a_wrong_key_is_refused_and_sets_nothing(client):
    c, _ = client
    r = c.get("/panel/login", params={"key": "не тот"}, follow_redirects=False)
    assert r.status_code == 401
    assert "set-cookie" not in {k.lower() for k in r.headers}
    assert c.get("/panel/tamapi").status_code == 401


def test_no_key_at_all_is_refused_and_sets_nothing(client):
    c, _ = client
    r = c.get("/panel/login", follow_redirects=False)
    assert r.status_code in (401, 422)
    assert "set-cookie" not in {k.lower() for k in r.headers}


def test_the_key_is_not_echoed_back_in_the_body(client):
    """Ключ и так уедет в историю браузера через query — повторять его в теле
    страницы значит раздать его ещё и скриншотам."""
    c, _ = client
    r = c.get("/panel/login", params={"key": KEY}, follow_redirects=False)
    assert KEY not in r.text


def test_the_redirect_target_cannot_be_an_arbitrary_site(client):
    """Открытый редирект на ручке входа — это фишинг с твоего же домена."""
    c, _ = client
    r = c.get("/panel/login", params={"key": KEY, "next": "https://evil.example"},
              follow_redirects=False)
    assert r.headers.get("location", "").startswith("/panel/")


def test_the_redirect_target_may_pick_the_jarvis_panel(client):
    c, _ = client
    r = c.get("/panel/login", params={"key": KEY, "next": "/panel/jarvis"},
              follow_redirects=False)
    assert r.headers["location"] == "/panel/jarvis"


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

    Путь ровно один — query: он percent-кодируется и доезжает до сервера как
    есть. Заголовок и cookie сюда не входят по факту, а не по решению: HTTP не
    даёт положить в них кириллицу, и клиент отвергает такой запрос сам, до
    сервера (проверено — httpx падает на UnicodeEncodeError)."""
    c, _ = client
    r = c.get("/panel/login", params={"key": "ключ"}, follow_redirects=False)
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
    r = c.get("/panel/login", params={"key": "что угодно"}, follow_redirects=False)
    assert r.status_code == 503
    assert "set-cookie" not in {k.lower() for k in r.headers}
