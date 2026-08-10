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
    c.post("/panel/tamapi/action", data={"data": "stop_all"},
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
