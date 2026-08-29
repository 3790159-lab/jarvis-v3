# -*- coding: utf-8 -*-
"""Пин РЕАЛИЗАЦИИ пары D: чужая персона в ОБЩЕЙ базе.

🔴 ЗАЧЕМ ОТДЕЛЬНЫМ ФАЙЛОМ, А НЕ ПРАВКОЙ СТОРОЖЕЙ. Сторожа §5 писал другой
автор, и трогать их — значит подгонять договор под код. Здесь не правка
договора, а ЗАКРЫТИЕ СЛЕПОГО ПЯТНА, найденного мутационным гейтом пары D:
мишень «страница диалога не сверяет слуг» осталась СЛЕПОЙ.

Почему сторож §5 п.13 её не ловит — и это не его вина. Его стенд заводит в
базе только СВОИ контакты, поэтому чужой адрес (`111:foreign`) отсекается
СЛЕДУЮЩЕЙ проверкой — «контакта нет в базе». Обе ветки дают один и тот же
404, и по нему их не различить: снеси сверку слуга — сторож останется
зелёным ([[jarvis-guard-caught-dead-branch]], ровно тот же механизм: у ветки
нет ничего, чего не было бы у соседней).

А ветка НЕ мёртвая, и вот почему: `TelethonRunner.primary_store()` отдаёт ОДИН
`Store` всем персонам процесса (это сказано в его собственном докстринге).
То есть база мультиперсонного раннера содержит контакты НЕСКОЛЬКИХ слугов, и
`has_contact` на чужом `contact_id` честно отвечает True. В этот момент
единственное, что стоит между клиенткой и перепиской ЧУЖОГО клиента, — сверка
слуга. Цена ошибки та же, что в §3.1 спеки веба.

Ни живого Telegram, ни живого `.secrets/`: база — файл в `tmp_path`.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from chatter.storage.db import Store

REPO_ROOT = Path(__file__).resolve().parents[2]
CLIENTS_DIR = REPO_ROOT / "chatter" / "clients"

# 🔴 ФОРМА С КАНАЛОМ (мерж пары C, 29.08). До него здесь стояла сегодняшняя
# двухсегментная форма, и после мержа она отвергается fail-closed — вместе со
# СВОИМ диалогом. То есть тест краснел не на дыре, а на предпосылке: «своё
# показывается» переставало быть правдой раньше, чем проверялось «чужое не
# показывается». Сторож, у которого отвалилась предпосылка, не защищает ничего.
OWN = "telegram:111:demo"
# Контакт ДРУГОЙ персоны в ТОЙ ЖЕ базе — то, что мультиперсонный раннер
# создаёт сам, без всякой ошибки.
FOREIGN = "telegram:222:demo2"
TOKEN_FIELD = "event_token"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = tmp_path / "shared.db"
    s = Store(str(db))
    for cid in (OWN, FOREIGN):
        s.get_or_create_contact(cid)
    s.add_message(FOREIGN, "user", "переписка чужого клиента", ts=1000.0)
    s.close()

    beat = tmp_path / "state" / "chatter_heartbeat_demo.txt"
    beat.parent.mkdir(parents=True, exist_ok=True)
    beat.write_text("beat", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("JARVIS_PANELS_KEY", "web-d-pin-key")
    monkeypatch.setenv("TAMAPI_DB", str(db))
    monkeypatch.setenv("TAMAPI_SLUG", "demo")
    monkeypatch.setenv("CHATTER_CLIENTS_DIR", str(CLIENTS_DIR))
    monkeypatch.delenv("TAMAPI_HEARTBEAT", raising=False)

    import app.routers.panels_auth as pa
    import app.routers.tamapi_dashboard as td
    for m in (pa, td):
        importlib.reload(m)
    import app.panel_client as pc
    importlib.reload(pc)

    from fastapi.testclient import TestClient
    c = TestClient(pc.build_app())
    c.cookies.set("panels_key", "web-d-pin-key")
    return c


def test_chuzhaya_persona_v_TOI_ZHE_baze_ne_pokazyvaetsya(client):
    """Контакт ЧУЖОГО слуга существует в базе — и всё равно ОТКАЗ.

    Предпосылка проверяется первой строкой: своя лента показывается. Без неё
    «чужое не показываем» неотличимо от «страницы нет вовсе», и пин был бы
    зелен по отсутствию ([[jarvis-absence-is-not-contradiction]]).
    """
    own = client.get("/panel/tamapi/d/%s" % OWN)
    assert own.status_code == 200, (
        "предпосылка: своя страница диалога обязана открываться (HTTP %s)"
        % own.status_code)

    alien = client.get("/panel/tamapi/d/%s" % FOREIGN)
    assert alien.status_code >= 400, (
        "страница отдала диалог ЧУЖОГО клиента из общей базы (HTTP %s): "
        "`has_contact` тут говорит «есть», и единственное, что стоит между "
        "клиенткой и чужой перепиской, — сверка слуга"
        % alien.status_code)
    assert "переписка чужого клиента" not in alien.text, (
        "чужая переписка утекла в тело ответа")
    assert TOKEN_FIELD not in alien.text, (
        "на отказе отдано поле ввода — форма приглашает писать в чужой диалог")
